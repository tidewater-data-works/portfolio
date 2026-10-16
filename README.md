# Tidewater Data Works

Tidewater Data Works is a freelance data studio. The work is web scraping, scheduled data pipelines, and API workflow automation: jobs that pull a source, check it, and land it somewhere a team already looks.

This repository is the public portfolio. Each demo stands on its own, with a README and an offline test suite. Nothing here asks for a personal name, a committed credential, or a machine-specific path.

## Services

### Web scraping

Catalogue sites and JavaScript-rendered pages, normalized to a deduplicated CSV. A run that comes back empty or short fails instead of writing a silent empty file. See [toscrape-scraper](toscrape-scraper/README.md).

### Data pipelines

Public-API ingestion with retries, schema and freshness checks, a default file writer, an optional spreadsheet writer, a charted summary, and a weekday job that publishes artifacts only. See [gov-api-pipeline](gov-api-pipeline/README.md).

### API and workflow automation

A scheduled CRM sync that searches for records changed since the last run, dedupes them, upserts a spreadsheet, and posts to Slack when a deal is new or changes stage. Failures alert Slack from an Error Trigger. See [n8n-hubspot-sheets-slack](n8n-hubspot-sheets-slack/README.md).

## Demos

| Demo | What it runs |
| --- | --- |
| [gov-api-pipeline](gov-api-pipeline/) | BLS Public API v1 series `LNS14000000` (unemployment rate), no API key |
| [toscrape-scraper](toscrape-scraper/) | All 50 catalogue pages of books.toscrape.com, plus the JavaScript quotes site |
| [n8n-hubspot-sheets-slack](n8n-hubspot-sheets-slack/) | 15-minute HubSpot deals search, Sheets upsert, Slack alerts |

## Tests

From the repository root:

```bash
make test
```

`make test` creates a local virtual environment, installs each demo's dependencies, and runs the three pytest suites. The tests use fixtures. They do not call the network and they do not need credentials.

## Weekday job

[`.github/workflows/daily.yml`](.github/workflows/daily.yml) runs the BLS pipeline on weekday afternoons (14:30 UTC, Monday through Friday) and uploads the CSV, summary, and chart as workflow artifacts. The job cannot push to the repository. Spreadsheet credentials and the alert webhook are not provided to it.
