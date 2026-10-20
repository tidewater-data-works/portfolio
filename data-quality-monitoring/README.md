# Data quality monitoring

Run a fixed set of checks on an orders file, write a pass/warn/fail report, and quarantine the rows that failed a hard check.

## Problem

A nightly order export lands in a sheet the warehouse already uses. Some nights the file is short, a customer id does not exist, an amount is blank or out of range, or the newest order is weeks old. Those rows should not sit in the clean file as if they were fine, and a late file should not look the same as a healthy one.

The companies in the sample (Harbor & Pine Supply Co., Lantern Street Clinic, Cedar Wharf Logistics, Osprey Municipal Library, Blue Heron Printworks) are fictional. The order ids and amounts are synthetic.

## Approach

`monitor.py` reads the orders CSV and the customer list with pandas (`dtype=str`, so a blank stays blank). `checks.py` then scores the batch. The thresholds sit at the top of that file.

| Check | Pass | Warn | Fail |
| --- | --- | --- | --- |
| Required columns | Every column is present | — | A required column is missing. Later checks are skipped. |
| Nulls | `order_id`, `customer_id`, `ordered_on`, `status`, `amount`, and `quantity` are filled | — | Any of those is blank |
| `ordered_on` | `YYYY-MM-DD` and a real calendar date | — | Any other text. A blank is the null check, not this one. |
| Amount and quantity | Amount parses as a number; quantity parses as an integer | — | Unparseable text. A blank is the null check, not this one. |
| `order_id` | Unique among non-blank ids | — | Every copy of a repeated id |
| Amount | 0.01 through 50000, inclusive | — | Outside that range |
| Quantity | 1 through 500, inclusive | — | Outside that range |
| Status | `booked`, `shipped`, `delivered`, `cancelled` | — | Any other non-blank status |
| Customer | `customer_id` is on the customer list | — | Unknown id. A blank is the null check. |
| `ship_to_city` | Filled | Blank | — |
| Freshness | Newest ISO date is 0–7 days before `--as-of` | 8–30 days | Older than 30 days, a future date, or no parseable date |
| Row count | At least one row | — | Zero rows |
| Row-count drift | Within 15% of the previous count | Above 15% and up to 50% | Above 50%, or the previous count is missing or zero |

The gate is fail when any check fails, warn when nothing failed but something warned, and pass otherwise. Hard row failures are written to the quarantine with the check names joined by `|`. A blank city, on its own, is a warning and is not quarantined. The three report files are written even when the gate is fail. The process exits 1 only when the gate is fail.

## Run

```bash
cd data-quality-monitoring
python -m pip install -r requirements.txt
python monitor.py \
  --orders samples/orders.csv \
  --customers samples/customers.csv \
  --previous samples/previous_count.json \
  --as-of 2026-03-20 \
  --report-md samples/quality_report.md \
  --report-json samples/quality_report.json \
  --quarantine samples/quarantine.csv
```

`--as-of` is the date the freshness check uses. The sample's newest good order is 2026-03-18, so 2026-03-20 keeps that check in the pass band.

## Sample output

`samples/orders.csv` has 16 orders. `samples/previous_count.json` records 20 rows from the prior run, so the count moved 20%. Two rows share `ORD-1002`. One amount is blank, one is negative, one is 60000, one quantity is 0, one status is `mystery`, one customer id is not on the list, one date is not ISO, and one city is blank.

The gate is **fail**. Nine rows are quarantined. The blank city is a warning and stays out of the quarantine. Freshness passes (newest order is 2 days old). Drift warns at 20.0%.

`samples/quality_report.md`:

```text
As of: 2026-03-20
Orders: 16
Quarantined rows: 9
Gate: fail
```

`samples/quarantine.csv` lists source rows 2, 8, 9, 10, 11, 12, 13, 14, and 16. `samples/quality_report.json` is the same result as JSON.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

`make test` from the repository root runs the same suite. Tests cover the committed sample, a clean batch, a warning that still exits 0, blank versus unparseable amounts, duplicate ids, a missing column, and the freshness and drift boundaries. No test calls the network.
