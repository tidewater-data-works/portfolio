"""Latest value, month-over-month change, trailing 12-month range, and a PNG chart."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from bls import EmptySeriesError, Observation


@dataclass(frozen=True)
class Summary:
    series_id: str
    latest_period: str
    latest_value: Decimal
    mom_change: Decimal | None
    trailing_12_min: Decimal
    trailing_12_max: Decimal
    numeric_rows: int
    trailing_12_count: int


def summarize(rows: list[Observation]) -> Summary:
    """Summarize numeric monthly observations.

    Month-over-month change compares the latest numeric month with the previous
    calendar month. A blank previous month leaves the change unset. The 12-month
    min and max cover the calendar year ending at the latest numeric month, and
    blank months inside that window are ignored.
    """
    numeric = sorted(
        (row for row in rows if row.value is not None),
        key=lambda row: (row.year, row.month),
    )
    if not numeric:
        raise EmptySeriesError()
    latest = numeric[-1]
    assert latest.value is not None
    by_month = {(row.year, row.month): row for row in numeric}
    previous_key = _shift_month(latest.year, latest.month, -1)
    previous = by_month.get(previous_key)
    mom = None if previous is None or previous.value is None else latest.value - previous.value
    start = _shift_month(latest.year, latest.month, -11)
    window = [
        row.value
        for row in numeric
        if start <= (row.year, row.month) <= (latest.year, latest.month) and row.value is not None
    ]
    return Summary(
        series_id=latest.series_id,
        latest_period=f"{latest.year:04d}-{latest.month:02d}",
        latest_value=latest.value,
        mom_change=mom,
        trailing_12_min=min(window),
        trailing_12_max=max(window),
        numeric_rows=len(numeric),
        trailing_12_count=len(window),
    )


def write_summary(summary: Summary, path: Path) -> None:
    payload = {
        "series_id": summary.series_id,
        "latest_period": summary.latest_period,
        "latest_value": float(summary.latest_value),
        "mom_change": None if summary.mom_change is None else float(summary.mom_change),
        "trailing_12_min": float(summary.trailing_12_min),
        "trailing_12_max": float(summary.trailing_12_max),
        "numeric_rows": summary.numeric_rows,
        "trailing_12_count": summary.trailing_12_count,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_chart(rows: list[Observation], path: Path) -> None:
    """Write a line chart of the numeric observations. Blank months are omitted."""
    numeric = sorted(
        (row for row in rows if row.value is not None),
        key=lambda row: (row.year, row.month),
    )
    if not numeric:
        raise EmptySeriesError()

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [f"{row.year:04d}-{row.month:02d}" for row in numeric]
    values = [float(row.value) for row in numeric if row.value is not None]
    fig, ax = plt.subplots(figsize=(10.5, 4.8), dpi=120)
    ax.plot(
        range(len(values)),
        values,
        color="#0E4D5C",
        marker="o",
        markersize=3.2,
        linewidth=1.6,
    )
    step = max(1, len(labels) // 12)
    ticks = list(range(0, len(labels), step))
    if ticks[-1] != len(labels) - 1:
        ticks.append(len(labels) - 1)
    ax.set_xticks(ticks)
    ax.set_xticklabels([labels[index] for index in ticks], rotation=45, ha="right")
    ax.set_title(f"{numeric[-1].series_id} unemployment rate")
    ax.set_ylabel("Percent")
    ax.set_xlabel("Month")
    ax.grid(True, axis="y", color="#D5DEE2", linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="png")
    plt.close(fig)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    absolute = year * 12 + (month - 1) + delta
    return absolute // 12, absolute % 12 + 1
