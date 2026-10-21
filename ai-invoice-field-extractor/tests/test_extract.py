"""CLI and committed sample files. The OpenAI path is covered with a missing key."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from extract import execute, load_backend, main, public_document

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
DOCUMENTS = SAMPLES / "documents"
ROOT_README = ROOT.parent / "README.md"


def test_rules_sample_matches_the_committed_files(tmp_path):
    json_path = tmp_path / "extracted.json"
    review_path = tmp_path / "review_queue.csv"
    code = execute(input_dir=DOCUMENTS, json_path=json_path, review_path=review_path)
    assert code == 0
    assert json_path.read_text(encoding="utf-8") == (SAMPLES / "extracted.json").read_text(
        encoding="utf-8"
    )
    assert review_path.read_text(encoding="utf-8") == (SAMPLES / "review_queue.csv").read_text(
        encoding="utf-8"
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    queued = [row["document_id"] for row in csv.DictReader(review_path.open(encoding="utf-8"))]
    assert queued == ["counter-2026-0318", "partial-nb-draft"]
    by_id = {document["document_id"]: document for document in payload["documents"]}
    assert by_id["hp-2026-0142"]["needs_review"] is False
    assert by_id["counter-2026-0318"]["confidence"] == "0.33"


def test_mock_sample_keeps_the_money_conflict_in_the_queue(tmp_path):
    json_path = tmp_path / "extracted_mock.json"
    review_path = tmp_path / "review_queue_mock.csv"
    backend = load_backend("mock", SAMPLES / "mock_responses.json")
    code = execute(
        input_dir=DOCUMENTS,
        json_path=json_path,
        review_path=review_path,
        backend=backend,
    )
    assert code == 0
    assert json.loads(json_path.read_text(encoding="utf-8")) == json.loads(
        (SAMPLES / "extracted_mock.json").read_text(encoding="utf-8")
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    counter = next(doc for doc in payload["documents"] if doc["document_id"] == "counter-2026-0318")
    assert counter["extractor"] == "llm"
    assert counter["document_number"] == "R-COUNTER-0318"
    assert counter["confidence"] == "0.82"
    assert counter["needs_review"] is True
    partial = next(doc for doc in payload["documents"] if doc["document_id"] == "partial-nb-draft")
    assert partial["extractor"] == "rules"
    assert "llm_fallback" in partial["confidence_flags"]


def test_strict_exits_1_when_the_queue_is_not_empty(tmp_path):
    code = main(
        [
            "--input",
            str(DOCUMENTS),
            "--json",
            str(tmp_path / "out.json"),
            "--review",
            str(tmp_path / "queue.csv"),
            "--strict",
        ]
    )
    assert code == 1
    assert (tmp_path / "queue.csv").exists()


def test_openai_without_a_key_stays_offline(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def boom(*args, **kwargs):
        raise AssertionError("network was called")

    monkeypatch.setattr("llm.urllib.request.urlopen", boom)
    code = main(
        [
            "--input",
            str(DOCUMENTS),
            "--json",
            str(tmp_path / "out.json"),
            "--review",
            str(tmp_path / "queue.csv"),
            "--backend",
            "openai",
        ]
    )
    assert code == 0
    payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert {document["extractor"] for document in payload["documents"]} == {"rules"}
    assert all("llm_fallback" in document["confidence_flags"] for document in payload["documents"])


def test_empty_input_and_missing_mock_file(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert (
        execute(
            input_dir=empty,
            json_path=tmp_path / "out.json",
            review_path=tmp_path / "queue.csv",
        )
        == 1
    )
    assert main(
        [
            "--input",
            str(DOCUMENTS),
            "--json",
            str(tmp_path / "out.json"),
            "--review",
            str(tmp_path / "queue.csv"),
            "--backend",
            "mock",
        ]
    ) == 1


def test_public_document_uses_two_decimal_strings():
    from extract import extract_directory

    document = extract_directory(DOCUMENTS)[0]
    public = public_document(document)
    assert public["confidence"] == "0.90"
    assert public["total"] == "27.30"


def test_portfolio_readme_links_this_demo():
    text = ROOT_README.read_text(encoding="utf-8")
    assert "](ai-invoice-field-extractor/)" in text
    assert "ai-invoice-field-extractor/README.md" in text
    demo = (ROOT_README.parent / "ai-invoice-field-extractor" / "README.md").read_text(encoding="utf-8")
    assert "Three backends" in demo
    assert "Two backends" not in demo
