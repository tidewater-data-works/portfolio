# Reliability check: nightly shipment-event load

**Prepared for:** Northline Freight Cooperative
**Prepared by:** Tidewater Data Works
**Engagement:** Fixed price, $250
**Date:** 20 October 2026
**Script reviewed:** `before/fragile.py`
**Patched script:** `after/reliable.py`

This is a portfolio sample of a fixed-price reliability check. Northline Freight Cooperative is a fictional client. The events in `samples/events.json` are synthetic. No production system was contacted, and this folder contains no credentials.

## Scope

One Python job loads shipment events into a CSV the dispatch desk reads each morning. This check covers retries, what a second run does to the file, data-quality gates, and logging. Hosting, alerting, and the upstream API's contract beyond the sample payload are outside the check.

## Sample batch

`samples/events.json` has eight events. Six are valid. `NL-1007` has status `lost`. `NL-1008` has weight `-3`. Both are the kind of row the old script wrote straight through.

## Findings

### 1. High — a single HTTP 503 fails the night

`fetch_events` calls the endpoint once, passes no timeout, and raises `RuntimeError` on any status other than 200. A 503, a 429, or a hung connection drops the whole load.

**Fix.** `after/reliable.py` calls `get(url, timeout=10)`. It retries 429, 500, 502, 503, and 504, and transport errors (`OSError`, which includes timeouts), up to three attempts. The wait is `backoff * 2^(attempt-1)`: 0.5 seconds, then 1 second, with the default backoff. A 400 fails on the first response. A 200 that is not a JSON list fails on the first response as well.

### 2. High — a rerun duplicates rows

The writer opens the CSV in append mode and flushes every row. Running it again, or running it again after a partial success, writes every event a second time. `samples/before_twice.csv` is that file: the eight source events, including `lost` and weight `-3`, written twice (16 data rows, one header).

**Fix.** Rows are stored by `event_id`. A second run with the same id updates that row. Reading the existing file back also collapses duplicate ids the old script already left behind. The new bytes are written to `shipments.csv.tmp` and moved into place with `os.replace`, so a reader keeps the previous file until the new one is complete.

### 3. High — a crash mid-write leaves a torn file

Rows are flushed onto the live destination. A `KeyError` on a later event (a missing `destination`, in the test) leaves the earlier rows committed and no record that the batch stopped.

**Fix.** The destination is replaced only after the batch passes the quality gate. If the replace itself fails, the previous file is untouched. An empty payload fails the same way: the log says the destination was left unchanged, and nothing is written.

### 4. Medium — invalid events land in the dispatch file

Negative weights and unknown statuses are written through. On this batch that is `NL-1007` and `NL-1008`.

**Fix.** A loadable row needs an `NL-` id with at least four digits, an ISO date, a status in `booked`, `in_transit`, `delivered`, or `exception`, a weight from just above 0 through 50,000 pounds, and a destination. Other rows go to `quarantine.csv` with the field names that failed. `samples/quarantine.csv` holds the two bad sample rows (`status`, and `weight_lb`). If invalid rows are more than 25 percent of the batch, the run fails and does not touch the destination or the quarantine file. The sample is two invalid rows out of eight, which is 25 percent, so the six good rows still load.

### 5. Low — the job prints one line and otherwise stays quiet

The success path prints `writing N events`. There is no log line for a retry, a rejected row, or the final counts.

**Fix.** The logging module records each retry at warning (`attempt 1 retryable HTTP 503`), each quarantined id at warning, the quality-gate failure at error, and one info line when the file is replaced. On the sample batch that line is `wrote 6 rows, quarantined 2`.

## Results on the sample batch

| File | What it shows |
| --- | --- |
| `samples/before_twice.csv` | Fragile writer, same eight events appended twice |
| `samples/shipments.csv` | Hardened writer, six valid events, weights to the hundredth of a pound |
| `samples/quarantine.csv` | `NL-1007` (`status`) and `NL-1008` (`weight_lb`) |

Running the hardened job a second time leaves `samples/shipments.csv` at six rows. `quarantine.csv` is replaced each run with that run's rejected rows, so a clean batch leaves a header and no data rows.

## Checks

From this directory:

```bash
python -m pytest -q
```

The tests force a 503 then a 200, a non-retryable 400, a timeout, a duplicate rerun, an in-place weight change, a torn write on the old script, a batch that is half invalid, and a replace that raises `OSError`.

## Out of scope

Spreadsheet delivery, a Slack or email alert, and a database upsert. The `event_id` merge is the piece those would reuse. Credentials were not requested and are not in this folder.
