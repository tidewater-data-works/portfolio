# Pipeline reliability audit

A before-and-after of a nightly shipment-event load, plus a short written audit. The patched script retries, replaces the CSV by event id, quarantines bad rows, and logs the run.

## Problem

Northline Freight Cooperative (a fictional desk, for this sample) runs one Python script each night. It pulls shipment events and appends them to a CSV the morning dispatch reads. The script drops the night on a single HTTP 503, writes the same rows again on a rerun, and will happily append a negative weight. The engagement is a $250 fixed-price reliability check: name the failures, patch the script, and leave a report the client can file.

The write-up is [`audit-report.md`](audit-report.md). The companies and the eight events are synthetic.

## Approach

`before/fragile.py` is the script as it was running.

- `fetch_events` calls the endpoint once and does not pass a timeout.
- `append_csv` opens the destination in append mode and flushes every row. A second run duplicates the batch. A `KeyError` halfway through leaves the earlier rows on disk.
- Statuses and weights are copied through.
- The success path prints `writing N events`.

`after/reliable.py` is the patched script.

- Transient HTTP statuses (429, 500, 502, 503, 504) and transport errors, including timeouts, retry with exponential backoff. A 400 fails immediately. The call passes `timeout=10`.
- Accepted rows are merged on `event_id` and written with `os.replace`, so a rerun updates a row instead of repeating it, and a failed replace leaves the previous file.
- Rows that fail the checks go to `quarantine.csv`. The checks are an `NL-` id, an ISO date, a known status, a weight from just above 0 through 50,000 pounds, and a destination. More than 25 percent invalid, or an empty payload, fails the run before either file is replaced.
- Retries, quarantined ids, and the final counts go through the logging module.

Both commands read the local JSON fixture. The HTTP functions take a `get` callable so the tests never open a socket. `urllib_get` is the live client; a 503 comes back as a status the retry loop can see.

## Run

```bash
cd pipeline-reliability-audit
python before/fragile.py --input samples/events.json --output /tmp/fragile.csv
python before/fragile.py --input samples/events.json --output /tmp/fragile.csv
python after/reliable.py \
  --input samples/events.json \
  --output /tmp/shipments.csv \
  --quarantine /tmp/quarantine.csv
```

The second fragile command appends the same eight events again. The hardened command writes six rows and a two-row quarantine. Run it again and `/tmp/shipments.csv` still has six rows.

`--url` is the HTTP path. Pass either `--input` or `--url`. The sample batch is the file, so the command above does not need a network.

## Sample output

`samples/before_twice.csv` is the fragile writer run twice. It has one header and 16 data rows. `NL-1001` appears twice, and the bad source rows are in the file as `lost` and weight `-3`.

`samples/shipments.csv` is the hardened load:

```text
event_id,shipped_on,status,weight_lb,destination
NL-1001,2026-03-02,delivered,420.50,"Port City, VA"
NL-1002,2026-03-02,in_transit,88.00,"Lantern, NC"
NL-1003,2026-03-03,booked,15.25,"Cedar Wharf, VA"
NL-1004,2026-03-03,delivered,640.00,"Osprey, VA"
NL-1005,2026-03-04,exception,12.00,"Harbor Lane, VA"
NL-1006,2026-03-04,in_transit,210.75,"Northbank, VA"
```

`samples/quarantine.csv`:

```text
event_id,shipped_on,status,weight_lb,destination,errors
NL-1007,2026-03-05,lost,10,"Osprey, VA",status
NL-1008,2026-03-05,delivered,-3,"Port City, VA",weight_lb
```

The info line for that run is `wrote 6 rows, quarantined 2`.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

`make test` from the repository root runs the same suite. The runtime dependencies are the standard library. Tests cover the fragile failure modes and the patched retries, idempotent rewrite, quality gate, and atomic replace. No test calls the network.
