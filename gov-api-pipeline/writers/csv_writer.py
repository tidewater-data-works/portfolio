"""Default writer: one UTF-8 CSV, oldest month first."""

from __future__ import annotations

import csv
from pathlib import Path

from bls import Observation

COLUMNS = ["series_id", "year", "period", "period_name", "value"]


class CSVWriter:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, rows: list[Observation]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(rows, key=lambda row: (row.year, row.month))
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
            writer.writeheader()
            for row in ordered:
                writer.writerow(row.as_row())
