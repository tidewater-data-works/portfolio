"""Crawl the books.toscrape.com catalogue into a deduplicated CSV.

The catalogue is 50 pages. Each row is a title, a numeric price, a rating from
1 to 5, the stock label, and the absolute book URL. Pass ``--min-rows`` to
exit 2 when the run is empty or shorter than the threshold.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import time
from decimal import Decimal
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_START = "https://books.toscrape.com/catalogue/page-1.html"
USER_AGENT = "TidewaterDataWorks/1.0 (+https://github.com/tidewater-data-works/portfolio)"
COLUMNS = ["title", "price", "rating", "stock", "url"]
RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
_PRICE = re.compile(r"\d+(?:\.\d+)?")


def parse_catalogue(html: str, page_url: str) -> tuple[list[dict], str | None]:
    """Return book rows from one catalogue page and the absolute next-page URL."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for article in soup.select("article.product_pod"):
        row = _parse_article(article, page_url)
        if row is not None:
            rows.append(row)
    next_link = soup.select_one("li.next a")
    href = next_link.get("href") if next_link else None
    next_url = urljoin(page_url, href) if href else None
    return rows, next_url


def crawl_books(
    fetch: Callable[[str], str],
    start_url: str,
    *,
    max_pages: int = 50,
    delay: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """Follow catalogue pagination, deduping by absolute book URL."""
    rows: list[dict] = []
    seen_urls: set[str] = set()
    seen_pages: set[str] = set()
    url: str | None = start_url
    for index in range(max_pages):
        if url is None or url in seen_pages:
            break
        if index and delay:
            sleep(delay)
        seen_pages.add(url)
        page_rows, next_url = parse_catalogue(fetch(url), url)
        for row in page_rows:
            if row["url"] in seen_urls:
                continue
            seen_urls.add(row["url"])
            rows.append(row)
        url = next_url
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def execute(
    *,
    fetch: Callable[[str], str],
    output: Path,
    min_rows: int | None,
    start_url: str,
    max_pages: int,
    delay: float,
) -> int:
    try:
        rows = crawl_books(fetch, start_url, max_pages=max_pages, delay=delay)
    except (requests.RequestException, OSError) as exc:
        logger.error("%s", exc)
        return 1
    write_csv(rows, output)
    if _below_minimum(len(rows), min_rows):
        logger.error("Row count %s is below --min-rows %s", len(rows), min_rows)
        return 2
    logger.info("Wrote %s book rows", len(rows))
    return 0


def http_fetch(url: str) -> str:
    """GET one page, retrying transient HTTP failures."""
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.content.decode("utf-8", errors="replace")
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(0.5 * attempt)
    assert last_error is not None
    raise last_error


def main(argv: list[str] | None = None, fetch: Callable[[str], str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Crawl books.toscrape.com into a CSV.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start-url", default=DEFAULT_START)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument(
        "--min-rows",
        type=int,
        help="Exit 2 when the crawl is empty or has fewer rows than this value.",
    )
    args = parser.parse_args(argv)
    return execute(
        fetch=fetch or http_fetch,
        output=args.output,
        min_rows=args.min_rows,
        start_url=args.start_url,
        max_pages=args.max_pages,
        delay=args.delay,
    )


def _parse_article(article, page_url: str) -> dict | None:
    title_link = article.select_one("h3 a")
    price_el = article.select_one("p.price_color")
    rating_el = article.select_one("p.star-rating")
    stock_el = article.select_one("p.availability")
    if not title_link or not price_el or not rating_el or not stock_el:
        return None
    href = title_link.get("href")
    if not href:
        return None
    title = " ".join(str(title_link.get("title") or title_link.get_text(" ", strip=True)).split())
    match = _PRICE.search(price_el.get_text(" ", strip=True))
    classes = [item for item in (rating_el.get("class") or []) if item != "star-rating"]
    rating = RATING_WORDS.get(classes[0]) if classes else None
    stock = " ".join(stock_el.get_text(" ", strip=True).split())
    if not title or match is None or rating is None or not stock:
        return None
    return {
        "title": title,
        "price": f"{Decimal(match.group(0)):.2f}",
        "rating": rating,
        "stock": stock,
        "url": urljoin(page_url, href),
    }


def _below_minimum(count: int, min_rows: int | None) -> bool:
    return min_rows is not None and (count == 0 or count < min_rows)


if __name__ == "__main__":
    raise SystemExit(main())
