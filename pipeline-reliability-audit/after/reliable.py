"""Shipment-event load after the reliability check.

Retries transient HTTP failures with exponential backoff, replaces the CSV
by event id, quarantines rows that fail the quality checks, and leaves the
previous destination in place when the batch is empty or too dirty.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

logger = logging.getLogger(__name__)

USER_AGENT = "TidewaterDataWorks/1.0 (+https://github.com/tidewater-data-works/portfolio)"
DEFAULT_URL = "https://example.invalid/events"
COLUMNS = ["event_id", "shipped_on", "status", "weight_lb", "destination"]
QUARANTINE_COLUMNS = COLUMNS + ["errors"]
STATUSES = {"booked", "in_transit", "delivered", "exception"}
MAX_WEIGHT = Decimal("50000")
RETRYABLE = {429, 500, 502, 503, 504}
DEFAULT_SHARE = Decimal("0.25")


class PipelineError(Exception):
    """A failed run. The previous destination file is left in place."""


class FetchError(PipelineError):
    """The event feed could not be read."""


class QualityError(PipelineError):
    """The batch failed a data-quality gate."""


class Response:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if not isinstance(self._payload, (list, dict)):
            raise ValueError("response was not JSON")
        return self._payload


def urllib_get(url: str, timeout: float = 10.0) -> Response:
    """GET one JSON document. HTTP error statuses come back as a Response."""
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        return Response(exc.code, None)
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise FetchError("response was not JSON") from exc
    return Response(status, payload)


def fetch_events(
    get,
    url: str,
    *,
    sleep=time.sleep,
    max_attempts: int = 3,
    backoff_seconds: float = 0.5,
    timeout: float = 10.0,
) -> list:
    """GET a JSON list, retrying 429/5xx and transport errors.

    ``get`` is called as ``get(url, timeout=timeout)``. Waits are
    ``backoff_seconds * 2 ** (attempt - 1)`` after a failed attempt.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = get(url, timeout=timeout)
        except OSError as exc:
            last_error = exc
            logger.warning("attempt %s failed: %s", attempt, exc)
        else:
            status = getattr(response, "status_code", None)
            if status == 200:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise FetchError("response was not JSON") from exc
                if not isinstance(payload, list):
                    raise FetchError("payload must be a list")
                return payload
            if status not in RETRYABLE:
                raise FetchError(f"HTTP {status}")
            last_error = FetchError(f"HTTP {status}")
            logger.warning("attempt %s retryable HTTP %s", attempt, status)
        if attempt < max_attempts:
            sleep(backoff_seconds * (2 ** (attempt - 1)))
    raise FetchError(f"request failed after {max_attempts} attempts") from last_error


def assess(event: object) -> list[str]:
    """Return the field names that fail. An empty list means the row is loadable."""
    if not isinstance(event, dict):
        return ["not_an_object"]
    errors: list[str] = []
    event_id = _cell(event.get("event_id"))
    if not _event_id_ok(event_id):
        errors.append("event_id")
    shipped = _cell(event.get("shipped_on"))
    try:
        date.fromisoformat(shipped)
    except ValueError:
        errors.append("shipped_on")
    if _cell(event.get("status")) not in STATUSES:
        errors.append("status")
    try:
        weight = Decimal(str(event.get("weight_lb")))
    except (InvalidOperation, ValueError):
        errors.append("weight_lb")
    else:
        if weight <= 0 or weight > MAX_WEIGHT:
            errors.append("weight_lb")
    if len(" ".join(_cell(event.get("destination")).split())) < 3:
        errors.append("destination")
    return errors


def normalize(event: dict) -> dict:
    weight = Decimal(str(event["weight_lb"])).quantize(Decimal("0.01"))
    return {
        "event_id": _cell(event["event_id"]),
        "shipped_on": _cell(event["shipped_on"]),
        "status": _cell(event["status"]),
        "weight_lb": f"{weight}",
        "destination": " ".join(_cell(event["destination"]).split()),
    }


def load_events(path: Path) -> list:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise QualityError("payload must be a list")
    return payload


def load_existing(path: Path) -> dict[str, dict]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != COLUMNS:
            raise QualityError("destination header does not match the shipment columns")
        rows: dict[str, dict] = {}
        for row in reader:
            rows[row["event_id"]] = {key: row[key] for key in COLUMNS}
        return rows


def atomic_write(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def publish(
    events: list,
    output: Path,
    quarantine: Path,
    *,
    max_invalid_share: Decimal = DEFAULT_SHARE,
) -> dict[str, int]:
    """Merge accepted rows by event id. Reject the batch before any replace."""
    if not events:
        logger.error("payload had 0 events; destination left unchanged")
        raise QualityError("payload had 0 events")
    good: list[dict] = []
    bad: list[dict] = []
    for event in events:
        errors = assess(event)
        if errors:
            bad.append(_quarantine_row(event, errors))
        else:
            good.append(normalize(event))
    share = Decimal(len(bad)) / Decimal(len(events))
    if share > max_invalid_share:
        logger.error(
            "invalid share %s exceeds %s; destination left unchanged",
            f"{share.quantize(Decimal('0.01'))}",
            f"{max_invalid_share.quantize(Decimal('0.01'))}",
        )
        raise QualityError(
            f"invalid share {share.quantize(Decimal('0.01'))} exceeds "
            f"{max_invalid_share.quantize(Decimal('0.01'))}"
        )
    merged = load_existing(output)
    for row in good:
        merged[row["event_id"]] = row
    ordered = [merged[key] for key in sorted(merged)]
    for row in bad:
        label = row["event_id"] or "(blank)"
        logger.warning("quarantined %s: %s", label, row["errors"])
    atomic_write(output, ordered, COLUMNS)
    atomic_write(quarantine, bad, QUARANTINE_COLUMNS)
    logger.info("wrote %s rows, quarantined %s", len(ordered), len(bad))
    return {"written": len(ordered), "accepted": len(good), "quarantined": len(bad)}


def run(
    *,
    output: Path,
    quarantine: Path,
    events: list | None = None,
    input_path: Path | None = None,
    url: str | None = None,
    get=None,
    sleep=time.sleep,
    max_invalid_share: Decimal = DEFAULT_SHARE,
    max_attempts: int = 3,
    backoff_seconds: float = 0.5,
) -> dict[str, int]:
    if events is None:
        if input_path is not None:
            events = load_events(input_path)
        else:
            events = fetch_events(
                get or urllib_get,
                url or DEFAULT_URL,
                sleep=sleep,
                max_attempts=max_attempts,
                backoff_seconds=backoff_seconds,
            )
    return publish(events, output, quarantine, max_invalid_share=max_invalid_share)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Load shipment events into an idempotent CSV.")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--url")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--quarantine", required=True, type=Path)
    parser.add_argument("--max-invalid-share", default="0.25", type=_share)
    args = parser.parse_args(argv)
    if (args.input is None) == (args.url is None):
        logger.error("pass exactly one of --input or --url")
        return 1
    try:
        run(
            output=args.output,
            quarantine=args.quarantine,
            input_path=args.input,
            url=args.url,
            max_invalid_share=args.max_invalid_share,
        )
    except PipelineError as exc:
        logger.error("%s", exc)
        return 1
    return 0


def _share(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("expected a decimal share") from exc
    if number < 0 or number > 1:
        raise argparse.ArgumentTypeError("share must be between 0 and 1")
    return number


def _event_id_ok(value: str) -> bool:
    return re.fullmatch(r"NL-\d{4,}", value) is not None


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _quarantine_row(event: object, errors: list[str]) -> dict:
    source = event if isinstance(event, dict) else {}
    return {
        "event_id": _cell(source.get("event_id")),
        "shipped_on": _cell(source.get("shipped_on")),
        "status": _cell(source.get("status")),
        "weight_lb": _cell(source.get("weight_lb")),
        "destination": _cell(source.get("destination")),
        "errors": "|".join(errors),
    }


if __name__ == "__main__":
    raise SystemExit(main())
