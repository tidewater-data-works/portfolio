"""Paginate the JavaScript edition of quotes.toscrape.com with Playwright.

quotes.toscrape.com/js/ writes quotes into the page with JavaScript, so the
crawl uses a headless browser. Parsing itself is plain HTML, which is what the
offline fixtures exercise. ``--min-rows`` exits 2 on an empty or short run.
"""

from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path
from typing import Protocol

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_START = "https://quotes.toscrape.com/js/"
USER_AGENT = "TidewaterDataWorks/1.0 (+https://github.com/tidewater-data-works/portfolio)"
COLUMNS = ["text", "author", "tags", "url"]


class QuotePage(Protocol):
    """The slice of a browser page the crawler uses."""

    @property
    def url(self) -> str: ...

    def goto(self, url: str) -> None: ...

    def content(self) -> str: ...

    def wait_for_quotes(self) -> None: ...

    def click_next(self) -> bool: ...


class PlaywrightClient:
    """Adapt a Playwright page to :class:`QuotePage`."""

    def __init__(self, page) -> None:
        self.page = page

    @property
    def url(self) -> str:
        return self.page.url

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)

    def content(self) -> str:
        return self.page.content()

    def wait_for_quotes(self) -> None:
        try:
            self.page.wait_for_selector("div.quote", timeout=15000)
        except Exception as exc:
            # An empty render times out. That is an empty run, not a crash.
            if exc.__class__.__name__ == "TimeoutError":
                return
            raise

    def click_next(self) -> bool:
        locator = self.page.locator("li.next a")
        if locator.count() == 0:
            return False
        locator.click()
        self.page.wait_for_load_state("domcontentloaded")
        return True


def parse_quotes(html: str, page_url: str) -> tuple[list[dict], bool]:
    """Return quotes on one rendered page and whether a next link is present."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for quote in soup.select("div.quote"):
        row = _parse_quote(quote, page_url)
        if row is not None:
            rows.append(row)
    has_next = soup.select_one("li.next a") is not None
    return rows, has_next


def collect_quotes(client: QuotePage, start_url: str, max_pages: int) -> list[dict]:
    """Open the start URL, then follow the Next control. Deduped by text and author."""
    client.goto(start_url)
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for _ in range(max_pages):
        client.wait_for_quotes()
        page_rows, _has_next = parse_quotes(client.content(), client.url)
        for row in page_rows:
            key = (row["text"], row["author"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        if not client.click_next():
            break
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def execute(
    *,
    client: QuotePage,
    output: Path,
    min_rows: int | None,
    start_url: str,
    max_pages: int,
) -> int:
    try:
        rows = collect_quotes(client, start_url, max_pages)
    except OSError as exc:
        logger.error("%s", exc)
        return 1
    write_csv(rows, output)
    if min_rows is not None and (len(rows) == 0 or len(rows) < min_rows):
        logger.error("Row count %s is below --min-rows %s", len(rows), min_rows)
        return 2
    logger.info("Wrote %s quote rows", len(rows))
    return 0


def main(argv: list[str] | None = None, client: QuotePage | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Crawl quotes.toscrape.com/js/ into a CSV.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start-url", default=DEFAULT_START)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument(
        "--min-rows",
        type=int,
        help="Exit 2 when the crawl is empty or has fewer rows than this value.",
    )
    args = parser.parse_args(argv)
    if client is not None:
        return execute(
            client=client,
            output=args.output,
            min_rows=args.min_rows,
            start_url=args.start_url,
            max_pages=args.max_pages,
        )
    return _run_playwright(args)


def _run_playwright(args) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error(
            "Playwright is not installed. Install the requirements, then run: playwright install chromium"
        )
        return 1
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        try:
            return execute(
                client=PlaywrightClient(page),
                output=args.output,
                min_rows=args.min_rows,
                start_url=args.start_url,
                max_pages=args.max_pages,
            )
        finally:
            browser.close()


def _parse_quote(quote, page_url: str) -> dict | None:
    text_el = quote.select_one("span.text")
    author_el = quote.select_one("small.author")
    if text_el is None or author_el is None:
        return None
    text = " ".join(text_el.get_text(" ", strip=True).split())
    author = " ".join(author_el.get_text(" ", strip=True).split())
    if not text or not author:
        return None
    tags = []
    for tag in quote.select("a.tag"):
        label = " ".join(tag.get_text(" ", strip=True).split())
        if label:
            tags.append(label)
    return {"text": text, "author": author, "tags": "|".join(tags), "url": page_url}


if __name__ == "__main__":
    raise SystemExit(main())
