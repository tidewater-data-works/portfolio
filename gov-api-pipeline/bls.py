"""Client for the BLS Public Data API v1.

The unregistered v1 endpoint does not take an API key. This module fetches one
series, checks the response shape, and turns BLS missing-value tokens into blanks.
"""

from __future__ import annotations

import re
import time
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

import requests

API_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
DEFAULT_SERIES_ID = "LNS14000000"
MAX_AGE_DAYS = 60
USER_AGENT = "TidewaterDataWorks/1.0 (+https://github.com/tidewater-data-works/portfolio)"

_MONTHLY = re.compile(r"^M(0[1-9]|1[0-2])$")
_ANNUAL = "M13"
_BLANK = {"-", "–", "—", ""}
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class BLSError(Exception):
    """Fetch or validation failure that should fail the pipeline."""


class SchemaError(BLSError):
    """The payload does not match the v1 timeseries schema."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"schema mismatch: {detail}")


class EmptySeriesError(BLSError):
    """The series contained no numeric rows."""

    def __init__(self) -> None:
        super().__init__("0 rows")


class StaleSeriesError(BLSError):
    """The newest period ends more than MAX_AGE_DAYS before the run date."""

    def __init__(self, period_end: date, age_days: int) -> None:
        super().__init__(
            f"data older than {MAX_AGE_DAYS} days "
            f"(latest period ends {period_end.isoformat()}, age {age_days} days)"
        )


class FetchError(BLSError):
    """The API request failed."""


class _Retry(Exception):
    """Internal signal that the attempt should be repeated."""


@dataclass(frozen=True)
class Observation:
    series_id: str
    year: int
    period: str
    period_name: str
    month: int
    value: Decimal | None
    value_text: str

    @property
    def period_end(self) -> date:
        last_day = monthrange(self.year, self.month)[1]
        return date(self.year, self.month, last_day)

    def as_row(self) -> dict[str, str]:
        return {
            "series_id": self.series_id,
            "year": str(self.year),
            "period": self.period,
            "period_name": self.period_name,
            "value": self.value_text,
        }


def default_year_window(today: date) -> tuple[int, int]:
    """Three calendar years, inside the unregistered 10-year v1 limit."""
    return today.year - 2, today.year


def fetch_payload(
    series_id: str,
    start_year: int,
    end_year: int,
    *,
    poster: Callable[..., Any] | None = None,
    max_attempts: int = 3,
    backoff_seconds: float = 1.0,
    timeout: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """POST a v1 timeseries query, retrying transient failures.

    ``poster`` replaces ``requests.post`` in tests. It is called as
    ``poster(url, json=body, timeout=timeout, headers=headers)``.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    body = {
        "seriesid": [series_id],
        "startyear": str(start_year),
        "endyear": str(end_year),
    }
    post = poster or _default_poster()
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = post(API_URL, json=body, timeout=timeout, headers=headers)
            status = getattr(response, "status_code", None)
            if status in _RETRYABLE_STATUS:
                raise _Retry(f"HTTP {status}")
            if status is None or status >= 400:
                raise FetchError(f"BLS HTTP {status}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise _Retry("response was not JSON") from exc
            if not isinstance(payload, dict):
                raise SchemaError("top-level JSON must be an object")
            return payload
        except (requests.Timeout, requests.ConnectionError, _Retry) as exc:
            last_error = exc
            if attempt == max_attempts:
                break
            sleep(backoff_seconds * (2 ** (attempt - 1)))
    raise FetchError(f"BLS request failed after {max_attempts} attempts") from last_error


def parse_series(payload: Mapping[str, Any], series_id: str) -> list[Observation]:
    """Validate the v1 envelope and return monthly observations.

    Annual ``M13`` rows are dropped. A BLS ``-`` value becomes a blank
    (``value is None`` and ``value_text == ""``).
    """
    if not isinstance(payload, Mapping):
        raise SchemaError("top-level JSON must be an object")
    status = payload.get("status")
    if not isinstance(status, str):
        raise SchemaError("missing status")
    if status != "REQUEST_SUCCEEDED":
        messages = payload.get("message") or []
        if not isinstance(messages, list):
            messages = [str(messages)]
        detail = "; ".join(str(item) for item in messages if str(item).strip())
        suffix = f": {detail}" if detail else ""
        raise FetchError(f"BLS status {status}{suffix}")

    results = payload.get("Results")
    if not isinstance(results, Mapping):
        raise SchemaError("missing Results")
    series_list = results.get("series")
    if not isinstance(series_list, list):
        raise SchemaError("Results.series must be a list")
    match = None
    for series in series_list:
        if isinstance(series, Mapping) and series.get("seriesID") == series_id:
            match = series
            break
    if match is None:
        raise SchemaError(f"series {series_id} not in Results.series")
    data = match.get("data")
    if not isinstance(data, list):
        raise SchemaError("series data must be a list")

    observations: list[Observation] = []
    for index, point in enumerate(data):
        if not isinstance(point, Mapping):
            raise SchemaError(f"data[{index}] must be an object")
        observations.extend(_parse_point(point, series_id, index))
    return observations


def validate_observations(
    rows: list[Observation],
    *,
    today: date,
    max_age_days: int = MAX_AGE_DAYS,
) -> list[Observation]:
    """Fail when the series has zero numeric rows.

    ``today`` and ``max_age_days`` are accepted so existing callers stay
    stable. Freshness is reported separately by ``freshness_warning``.
    """
    del today, max_age_days
    numeric = [row for row in rows if row.value is not None]
    if not numeric:
        raise EmptySeriesError()
    return rows


def freshness_warning(
    rows: list[Observation],
    *,
    today: date,
    max_age_days: int = MAX_AGE_DAYS,
) -> str | None:
    """Return a warning when the newest period is older than ``max_age_days``.

    A period that ended exactly ``max_age_days`` ago is current. One day
    older is a warning. The pipeline still writes its files.
    """
    if not rows:
        return None
    latest = max(rows, key=lambda row: (row.year, row.month))
    age_days = (today - latest.period_end).days
    if age_days > max_age_days:
        return str(StaleSeriesError(latest.period_end, age_days))
    return None


def _parse_point(point: Mapping[str, Any], series_id: str, index: int) -> list[Observation]:
    for field in ("year", "period", "value"):
        if field not in point:
            raise SchemaError(f"data[{index}] missing {field}")
    period = str(point["period"]).strip()
    if period == _ANNUAL:
        return []
    if not _MONTHLY.match(period):
        raise SchemaError(f"data[{index}] has unsupported period {period!r}")
    year_text = str(point["year"]).strip()
    if not re.fullmatch(r"\d{4}", year_text):
        raise SchemaError(f"data[{index}] has unsupported year {point['year']!r}")
    value, value_text = _parse_value(point["value"], index)
    period_name = point.get("periodName") or ""
    if not isinstance(period_name, str):
        raise SchemaError(f"data[{index}] periodName must be a string")
    return [
        Observation(
            series_id=series_id,
            year=int(year_text),
            period=period,
            period_name=period_name.strip(),
            month=int(period[1:]),
            value=value,
            value_text=value_text,
        )
    ]


def _parse_value(raw: Any, index: int) -> tuple[Decimal | None, str]:
    if isinstance(raw, bool) or raw is None:
        raise SchemaError(f"data[{index}] value is not a number or blank")
    if isinstance(raw, (int, float, Decimal)):
        try:
            value = Decimal(str(raw))
        except InvalidOperation as exc:
            raise SchemaError(f"data[{index}] value is not a number or blank") from exc
        return value, format(value, "f")
    if not isinstance(raw, str):
        raise SchemaError(f"data[{index}] value is not a number or blank")
    token = raw.strip()
    if token in _BLANK:
        return None, ""
    try:
        value = Decimal(token)
    except InvalidOperation as exc:
        raise SchemaError(f"data[{index}] value is not a number or blank") from exc
    return value, token


def _default_poster() -> Callable[..., Any]:
    session = requests.Session()

    def poster(url: str, *, json: dict[str, Any], timeout: float, headers: dict[str, str]) -> Any:
        return session.post(url, json=json, timeout=timeout, headers=headers)

    return poster
