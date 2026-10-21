# Books and quotes scrapers

Two scrapers for the public toscrape sandboxes. Both write a deduplicated UTF-8 CSV and exit **2** when `--min-rows` is set and the run is empty or shorter than that count. The partial CSV is still written, so a short run can be inspected. Any other failure exits 1.

## books.py

Crawls all 50 catalogue pages of [books.toscrape.com](https://books.toscrape.com/), starting at `catalogue/page-1.html` and following `li.next` until the pager ends or 50 pages have been fetched.

| Column | Meaning |
| --- | --- |
| `title` | Full title from the link's title attribute |
| `price` | Numeric price with two decimals. The currency symbol is removed. |
| `rating` | Integer 1–5 (`One` through `Five`) |
| `stock` | Availability text, for example `In stock` |
| `url` | Absolute URL of the book |

Rows are deduped by URL. A book card missing a price, rating, or URL is skipped. Requests are retried three times and pause briefly between pages.

```bash
cd toscrape-scraper
python -m pip install -r requirements.txt
python books.py --output books.csv --min-rows 1000
```

## quotes_js.py

Crawls [quotes.toscrape.com/js/](https://quotes.toscrape.com/js/) with Playwright. That page writes the quotes into the document with JavaScript, so a plain HTTP GET does not see them. The browser opens the first page, waits for `div.quote`, then clicks Next until the pager ends.

| Column | Meaning |
| --- | --- |
| `text` | Quote text |
| `author` | Author, as published on the page |
| `tags` | Pipe-separated tags |
| `url` | Page URL the quote was read from |

Rows are deduped on text plus author. Install the browser once before a live run:

```bash
python -m pip install -r requirements.txt
playwright install chromium
python quotes_js.py --output quotes.csv --min-rows 100
```

## Samples

`samples/books.csv` is the first 20 rows of one full 50-page crawl. `samples/quotes.csv` is one full crawl of the JavaScript quotes site. Both were produced by the scripts above. Re-run those commands to refresh them. The quotes file includes the author field published by the sandbox.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

`make test` from the repository root runs the same suite. Tests parse HTML fixtures and a stand-in for the Playwright page. They do not fetch the sites and they do not launch a browser. Coverage includes pagination, dedupe, rating and price cleanup, a short run, and an empty run that exits 2.
