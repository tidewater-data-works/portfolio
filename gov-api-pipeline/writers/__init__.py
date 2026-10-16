"""Output writers. CSV is always on. Sheets is added only when configured."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from writers.csv_writer import CSVWriter
from writers.sheets_writer import SheetsWriter

logger = logging.getLogger(__name__)


def build_writers(output_dir: Path, series_id: str) -> list[CSVWriter | SheetsWriter]:
    """Return the CSV writer, plus a Sheets writer when credentials are present.

    ``GOOGLE_SERVICE_ACCOUNT_JSON`` must be the service-account JSON itself,
    not a filesystem path. ``GOOGLE_SHEETS_SPREADSHEET_ID`` is required in that
    case. ``GOOGLE_SHEETS_WORKSHEET`` defaults to ``Unemployment``.
    """
    writers: list[CSVWriter | SheetsWriter] = [
        CSVWriter(output_dir / f"{series_id.lower()}.csv")
    ]
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        logger.info("Sheets writer disabled")
        return writers
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
    if not isinstance(info, dict):
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON must be a JSON object")
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
    if not spreadsheet_id:
        raise RuntimeError(
            "GOOGLE_SHEETS_SPREADSHEET_ID must be set when GOOGLE_SERVICE_ACCOUNT_JSON is set"
        )
    worksheet = os.environ.get("GOOGLE_SHEETS_WORKSHEET", "Unemployment").strip() or "Unemployment"
    writers.append(SheetsWriter(spreadsheet_id, worksheet, info))
    logger.info("Sheets writer enabled for worksheet %s", worksheet)
    return writers


__all__ = ["CSVWriter", "SheetsWriter", "build_writers"]
