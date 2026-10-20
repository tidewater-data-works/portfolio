"""Extract invoice and receipt fields, score them, and queue low-confidence rows.

The default backend is the rules parser. ``--backend mock`` replays a JSON
file of scripted model responses. ``--backend openai`` calls a chat model
only when OPENAI_API_KEY is set, and falls back to the rules parser when
the call cannot be made or the payload fails the schema.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from llm import BackendResponseError, BackendUnavailable, MockLLMBackend, OpenAIChatBackend
from rules import rules_extract
from schema import (
    ExtractedDocument,
    FieldsIn,
    confidence_for,
    needs_review,
    quality_flags,
    review_reason,
)

logger = logging.getLogger(__name__)

REVIEW_COLUMNS = [
    "document_id",
    "document_type",
    "vendor_name",
    "document_number",
    "total",
    "confidence",
    "flags",
    "review_reason",
]


def extract_document(text: str, *, document_id: str, backend=None) -> ExtractedDocument:
    flags: list[str] = []
    extractor = "rules"
    fields: FieldsIn | None = None
    parser_flags: list[str] = []
    if backend is not None:
        try:
            payload = backend.extract_fields(text, document_id=document_id)
        except BackendUnavailable as exc:
            logger.warning("model backend unavailable for %s: %s", document_id, exc)
            flags.append("llm_fallback")
        except BackendResponseError as exc:
            logger.warning("model response unusable for %s: %s", document_id, exc)
            flags.extend(["llm_invalid_response", "llm_fallback"])
        else:
            try:
                fields = FieldsIn.model_validate(payload)
            except ValidationError:
                logger.warning("model payload failed schema for %s", document_id)
                flags.extend(["llm_schema_invalid", "llm_fallback"])
            else:
                extractor = "llm"
    if fields is None:
        guessed, parser_flags = rules_extract(text)
        flags.extend(parser_flags)
        fields = FieldsIn.model_validate(guessed)
    flags.extend(quality_flags(fields, parser_flags))
    unique = sorted(set(flags))
    score = confidence_for(extractor, unique)
    return ExtractedDocument(
        document_id=document_id,
        confidence=score,
        confidence_flags=unique,
        needs_review=needs_review(score, unique),
        extractor=extractor,
        **fields.model_dump(),
    )


def extract_directory(input_dir: Path, *, backend=None) -> list[ExtractedDocument]:
    documents = []
    for path in sorted(input_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        documents.append(extract_document(text, document_id=path.stem, backend=backend))
    return documents


def public_document(document: ExtractedDocument) -> dict:
    return {
        "document_id": document.document_id,
        "document_type": document.document_type,
        "vendor_name": document.vendor_name,
        "document_number": document.document_number,
        "document_date": document.document_date,
        "bill_to": document.bill_to,
        "currency": document.currency,
        "subtotal": _fmt_money(document.subtotal),
        "tax": _fmt_money(document.tax),
        "total": _fmt_money(document.total),
        "line_items": [
            {
                "description": item.description,
                "quantity": _fmt_qty(item.quantity),
                "unit_price": _fmt_money(item.unit_price),
                "amount": _fmt_money(item.amount),
            }
            for item in document.line_items
        ],
        "confidence": _fmt_money(document.confidence),
        "confidence_flags": list(document.confidence_flags),
        "needs_review": document.needs_review,
        "extractor": document.extractor,
    }


def write_outputs(
    documents: list[ExtractedDocument],
    *,
    json_path: Path,
    review_path: Path,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"documents": [public_document(document) for document in documents]}
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    rows = []
    for document in sorted(documents, key=lambda item: item.document_id):
        if not document.needs_review:
            continue
        rows.append(
            {
                "document_id": document.document_id,
                "document_type": document.document_type,
                "vendor_name": document.vendor_name or "",
                "document_number": document.document_number or "",
                "total": _fmt_money(document.total) or "",
                "confidence": _fmt_money(document.confidence) or "",
                "flags": "|".join(document.confidence_flags),
                "review_reason": review_reason(document.confidence_flags),
            }
        )
    with review_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def execute(
    *,
    input_dir: Path,
    json_path: Path,
    review_path: Path,
    backend=None,
    strict: bool = False,
) -> int:
    if not input_dir.is_dir():
        logger.error("input directory does not exist: %s", input_dir)
        return 1
    try:
        documents = extract_directory(input_dir, backend=backend)
    except (OSError, ValidationError) as exc:
        logger.error("%s", exc)
        return 1
    if not documents:
        logger.error("no text documents in %s", input_dir)
        return 1
    write_outputs(documents, json_path=json_path, review_path=review_path)
    queued = sum(1 for document in documents if document.needs_review)
    logger.info("wrote %s documents, %s queued for review", len(documents), queued)
    if strict and queued:
        logger.error("%s document(s) need review", queued)
        return 1
    return 0


def load_backend(name: str, mock_file: Path | None):
    if name == "rules":
        return None
    if name == "mock":
        if mock_file is None:
            raise ValueError("--mock-file is required with --backend mock")
        payload = json.loads(mock_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("mock file must be a JSON object")
        return MockLLMBackend(payload)
    if name == "openai":
        return OpenAIChatBackend()
    raise ValueError(f"unknown backend: {name}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Extract invoice and receipt fields.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--backend", choices=("rules", "mock", "openai"), default="rules")
    parser.add_argument("--mock-file", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 when any document is queued for review.",
    )
    args = parser.parse_args(argv)
    try:
        backend = load_backend(args.backend, args.mock_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("%s", exc)
        return 1
    return execute(
        input_dir=args.input,
        json_path=args.json,
        review_path=args.review,
        backend=backend,
        strict=args.strict,
    )


def _fmt_money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value.quantize(Decimal('0.01'))}"


def _fmt_qty(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == value.to_integral():
        return str(value.to_integral())
    return format(value, "f")


if __name__ == "__main__":
    raise SystemExit(main())
