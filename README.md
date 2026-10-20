# Tidewater Data Works

Tidewater Data Works is an independent data engineering practice. The work is web scraping, scheduled data pipelines, document extraction, data quality, data cleaning, and API workflow automation: jobs that pull a source, check it, and land it somewhere a team already looks.

This repository is the public portfolio. Each demo stands on its own, with a README, sample output, and an offline test suite. Sample companies and figures are fictional.

## Services

### Web scraping

Catalogue sites and JavaScript-rendered pages, normalized to a deduplicated CSV. A run that comes back empty or short fails instead of writing a silent empty file. See [toscrape-scraper](toscrape-scraper/README.md).

### Data pipelines

Public-API ingestion with retries, schema and freshness checks, a default file writer, an optional spreadsheet writer, a charted summary, and a weekday job that publishes artifacts only. See [gov-api-pipeline](gov-api-pipeline/README.md).

### API and workflow automation

A scheduled CRM sync that searches for records changed since the last run, dedupes them, upserts a spreadsheet, and posts to Slack when a deal is new or changes stage. Failures alert Slack from an Error Trigger. See [n8n-hubspot-sheets-slack](n8n-hubspot-sheets-slack/README.md).

### Document and PDF extraction

Invoices and a one-page activity report, drawn as PDFs, extracted to CSV and JSON. Line totals, tax, and required fields are checked before the clean files are written. The PDFs are synthetic. See [pdf-document-extraction](pdf-document-extraction/README.md).

### Invoice and receipt fields

A structured extractor for invoice and receipt text. Pydantic checks the schema. A rules backend runs with no API key; a scripted model backend and an HTTP backend can be plugged in, and the HTTP call is mocked in tests. Low-confidence rows go to a review-queue CSV. See [ai-invoice-field-extractor](ai-invoice-field-extractor/README.md).

### Pipeline reliability

A before-and-after of a nightly shipment load: retries with backoff, idempotent writes, a data-quality quarantine, and logging, plus a short written audit. See [pipeline-reliability-audit](pipeline-reliability-audit/README.md).

### Data quality

Declarative checks on a synthetic order file: required columns, nulls, uniqueness, ranges, allowed statuses, referential integrity against a customer list, freshness, and row-count drift. The run writes a markdown report, a JSON report, and a quarantine of the failing rows. A hard failure exits non-zero after the files are written. See [data-quality-monitoring](data-quality-monitoring/README.md).

### Data cleaning

A messy vendor export, in CSV or Excel, normalized to one row per vendor: names, phones, streets, cities, regions, categories, dates, currency, and weights, with near-duplicate rows merged. The run writes the clean file, a change log, and a before/after summary. See [data-cleaning-toolkit](data-cleaning-toolkit/README.md).

## Demos

| Demo | What it runs |
| --- | --- |
| [gov-api-pipeline](gov-api-pipeline/) | BLS Public API v1 series `LNS14000000` (unemployment rate), no API key |
| [toscrape-scraper](toscrape-scraper/) | All 50 catalogue pages of books.toscrape.com, plus the JavaScript quotes site |
| [n8n-hubspot-sheets-slack](n8n-hubspot-sheets-slack/) | 15-minute HubSpot deals search, Sheets upsert, Slack alerts |
| [pdf-document-extraction](pdf-document-extraction/) | Synthetic invoice and report PDFs to CSV and JSON, with total checks |
| [ai-invoice-field-extractor](ai-invoice-field-extractor/) | Invoice and receipt fields, offline rules backend, review queue |
| [pipeline-reliability-audit](pipeline-reliability-audit/) | Fragile shipment load, hardened rerun, and a $250 audit write-up |
| [data-quality-monitoring](data-quality-monitoring/) | Synthetic orders checked for schema, ranges, freshness, and drift, with a quarantine |
| [data-cleaning-toolkit](data-cleaning-toolkit/) | Messy vendor CSV and Excel normalized to a clean file and a change log |

## Tests

From the repository root:

```bash
make test
```

`make test` creates a local virtual environment, installs each demo's dependencies, and runs every pytest suite. The tests use fixtures. They do not call the network and they do not need credentials.

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs `make test` on every push and pull request.

## Weekday job

[`.github/workflows/daily.yml`](.github/workflows/daily.yml) runs the BLS pipeline on weekdays at 14:30 UTC and uploads the CSV, summary, and chart as workflow artifacts. The job cannot push to the repository. Spreadsheet credentials and the alert webhook are not provided to it.
