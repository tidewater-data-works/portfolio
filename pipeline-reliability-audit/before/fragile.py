"""Shipment-event load before the reliability check.

This is the script the audit starts from. One HTTP attempt, no timeout,
append-only CSV writes, no row checks, and a print on the success path.
The patched script is ``after/reliable.py``.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

COLUMNS = ["event_id", "shipped_on", "status", "weight_lb", "destination"]


def fetch_events(get, url: str):
    """GET once. The callable is invoked with the URL and no timeout."""
    response = get(url)
    status = getattr(response, "status_code", None)
    if status != 200:
        raise RuntimeError(f"request failed: HTTP {status}")
    return response.json()


def append_csv(path: Path, events: list) -> None:
    """Append every event. A second call writes the same rows again.

    Each row is flushed before the next one is written. A later KeyError
    leaves the earlier rows already in the destination.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        if new_file:
            writer.writeheader()
            handle.flush()
        for event in events:
            writer.writerow({key: event[key] for key in COLUMNS})
            handle.flush()


def load_and_append(input_path: Path, output_path: Path) -> None:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("payload must be a list")
    print(f"writing {len(payload)} events")
    append_csv(output_path, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append shipment events to a CSV.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        load_and_append(args.input, args.output)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, RuntimeError) as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
