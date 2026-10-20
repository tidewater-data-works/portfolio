"""Fetch LNS14000000, validate it, and write the CSV, summary, and chart."""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime
from pathlib import Path

from alert import send_alert
from bls import (
    BLSError,
    DEFAULT_SERIES_ID,
    default_year_window,
    fetch_payload,
    freshness_warning,
    parse_series,
    validate_observations,
)
from summary import summarize, write_chart, write_summary
from writers import build_writers

logger = logging.getLogger(__name__)


def run(
    *,
    output_dir: Path,
    series_id: str = DEFAULT_SERIES_ID,
    start_year: int | None = None,
    end_year: int | None = None,
    today: date | None = None,
    poster=None,
    alert_poster=None,
) -> int:
    """Run one pipeline pass. Returns 0 on success and 1 on failure."""
    as_of = today or date.today()
    if start_year is None or end_year is None:
        default_start, default_end = default_year_window(as_of)
        start_year = default_start if start_year is None else start_year
        end_year = default_end if end_year is None else end_year
    try:
        payload = fetch_payload(series_id, start_year, end_year, poster=poster)
        rows = parse_series(payload, series_id)
        rows = validate_observations(rows, today=as_of)
        warning = freshness_warning(rows, today=as_of)
        writers = build_writers(output_dir, series_id)
        for writer in writers:
            writer.write(rows)
        summary = summarize(rows)
        write_summary(summary, output_dir / "summary.json")
        chart_path = output_dir / f"{series_id.lower()}.png"
        write_chart(rows, chart_path)
    except (BLSError, RuntimeError, OSError) as exc:
        logger.error("%s", exc)
        send_alert(str(exc), series_id=series_id, poster=alert_poster)
        return 1

    mom = "n/a" if summary.mom_change is None else f"{summary.mom_change:+f}"
    logger.info(
        "%s %s latest %s MoM %s 12-month min %s max %s chart %s",
        summary.series_id,
        summary.latest_period,
        summary.latest_value,
        mom,
        summary.trailing_12_min,
        summary.trailing_12_max,
        chart_path.name,
    )
    if warning:
        logger.warning("%s", warning)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Pull BLS series LNS14000000 and write a CSV, summary, and chart."
    )
    parser.add_argument("--output-dir", default="output", type=Path)
    parser.add_argument("--series-id", default=DEFAULT_SERIES_ID)
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument(
        "--as-of",
        type=_parse_date,
        help="Override the run date (YYYY-MM-DD) used for the freshness check.",
    )
    args = parser.parse_args(argv)
    return run(
        output_dir=args.output_dir,
        series_id=args.series_id,
        start_year=args.start_year,
        end_year=args.end_year,
        today=args.as_of,
    )


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


if __name__ == "__main__":
    raise SystemExit(main())
