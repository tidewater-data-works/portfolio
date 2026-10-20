# BLS unemployment-rate pipeline

Pulls [BLS](https://www.bls.gov/) series `LNS14000000` — the civilian unemployment rate, 16 years and over, seasonally adjusted — from the public API v1. v1 does not take a registration key. The default request covers the current calendar year and the two years before it, which stays inside the unregistered ten-year limit.

The pipeline retries transient HTTP failures, checks the payload, and writes three files:

- `lns14000000.csv` — monthly observations, oldest first
- `summary.json` — latest value, month-over-month change, trailing 12-month min and max
- `lns14000000.png` — line chart of the numeric observations

Annual `M13` rows are dropped. A BLS `-` (and the dash characters BLS uses for missing data) is stored as a blank cell, not as the character `-`. Blank months stay in the CSV so the gap is visible, and they are left out of the summary and the chart.

## Validation

The run fails, and does not write output, when any of these are true:

- the series has **0 numeric rows** (an empty `data` array, or every value blank)
- the payload does not match the v1 timeseries schema (missing `value`, unexpected period, and similar)

A `REQUEST_FAILED` status or an HTTP failure that survives the retries fails the run as well. On any failure, `alert.py` posts the message to `ALERT_WEBHOOK_URL` when that variable is set. When it is not set, the alert is logged and the alert step exits non-zero. The pipeline exits non-zero either way.

Freshness uses the last calendar day of the newest monthly period. A period that ended exactly 60 days ago is current. One day older is a warning: the CSV, summary, and chart are still written, and the run exits 0. A late BLS release therefore does not fail the weekday job.

## Writers

`writers/CSVWriter` always runs. It is the default.

`writers/SheetsWriter` is added only when `GOOGLE_SERVICE_ACCOUNT_JSON` is set to the service-account JSON document itself (not a file path). `GOOGLE_SHEETS_SPREADSHEET_ID` is required in that case. `GOOGLE_SHEETS_WORKSHEET` defaults to `Unemployment`. The sheet tab must already exist. Each run clears columns A–E on that tab and writes the header plus the current rows.

## Summary

`summary.py` reports:

- **latest value** — the most recent numeric month
- **month-over-month change** — that value minus the previous calendar month. If the previous month is blank, the change is `null`
- **12-month min and max** — the numeric values in the calendar year ending at the latest numeric month

## Run

```bash
cd gov-api-pipeline
python -m pip install -r requirements.txt
python pipeline.py --output-dir output
```

Useful flags: `--series-id`, `--start-year`, `--end-year`, and `--as-of YYYY-MM-DD` (the date used for the 60-day check).

Send an alert on its own with:

```bash
python alert.py --message "series check failed"
```

| Variable | Role |
| --- | --- |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Turns on `SheetsWriter`. The JSON document, not a path. |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | Required when Sheets is on. |
| `GOOGLE_SHEETS_WORKSHEET` | Tab name. Default `Unemployment`. |
| `ALERT_WEBHOOK_URL` | POST target for failures. JSON body `{"text", "source", "series_id"}`. |

The weekday workflow at [`.github/workflows/daily.yml`](../.github/workflows/daily.yml) runs this command on a `1-5` cron and uploads `output/` as an artifact. It does not receive those variables and it does not commit the files.

Unregistered v1 usage is capped by BLS at 25 requests a day. One weekday run is a single request.

## Tests

From the repository root, `make test` includes this suite. On its own:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Fixtures in `tests/fixtures/` cover a successful payload (including a `-` value and an annual row), an empty series, an all-blank series, a schema mismatch, and a `REQUEST_FAILED` status. Retry behavior uses a stand-in HTTP client. No test calls BLS or Google.
