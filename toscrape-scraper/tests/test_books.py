"""Fixture crawls for the books catalogue. No network."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from books import COLUMNS, crawl_books, main, parse_catalogue

FIXTURES = Path(__file__).parent / "fixtures"
START = "https://books.toscrape.com/catalogue/page-1.html"


def _fetch_factory():
    pages = {
        START: (FIXTURES / "catalogue_page.html").read_text(encoding="utf-8"),
        "https://books.toscrape.com/catalogue/page-2.html": (
            FIXTURES / "catalogue_page_2.html"
        ).read_text(encoding="utf-8"),
    }
    seen: list[str] = []

    def fetch(url: str) -> str:
        seen.append(url)
        return pages[url]

    return fetch, seen


def test_parse_price_rating_stock_and_absolute_url():
    html = (FIXTURES / "catalogue_page.html").read_text(encoding="utf-8")
    rows, next_url = parse_catalogue(html, START)
    assert next_url == "https://books.toscrape.com/catalogue/page-2.html"
    assert [row["title"] for row in rows] == ["Alpha, the Book", "Beta Primer"]
    assert rows[0]["price"] == "10.50"
    assert rows[1]["price"] == "7.00"
    assert [row["rating"] for row in rows] == [3, 1]
    assert [row["stock"] for row in rows] == ["In stock", "Out of stock"]
    assert rows[0]["url"] == "https://books.toscrape.com/catalogue/alpha-book_1/index.html"
    assert all(Decimal(row["price"]) > 0 for row in rows)


def test_crawl_dedupes_and_keeps_every_rating():
    fetch, seen = _fetch_factory()
    rows = crawl_books(fetch, START, max_pages=50, delay=0)
    assert seen == [START, "https://books.toscrape.com/catalogue/page-2.html"]
    titles = [row["title"] for row in rows]
    assert titles == ["Alpha, the Book", "Beta Primer", "Gamma Notes", "Delta Ledger", "Fish & Chips"]
    assert sorted(row["rating"] for row in rows) == [1, 2, 3, 4, 5]
    assert len({row["url"] for row in rows}) == 5


def test_crawl_stops_at_max_pages():
    fetch, seen = _fetch_factory()
    rows = crawl_books(fetch, START, max_pages=1, delay=0)
    assert seen == [START]
    assert len(rows) == 2


def test_csv_is_deduped_and_clean(tmp_path):
    fetch, _seen = _fetch_factory()
    output = tmp_path / "books.csv"
    code = main(
        ["--output", str(output), "--start-url", START, "--max-pages", "50", "--delay", "0", "--min-rows", "5"],
        fetch=fetch,
    )
    assert code == 0
    with output.open(encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    assert list(records[0]) == COLUMNS
    assert records[0]["title"] == "Alpha, the Book"
    assert records[0]["price"] == "10.50"
    assert records[0]["rating"] == "3"
    assert "£" not in output.read_text(encoding="utf-8")
    assert len(records) == len({row["url"] for row in records})


def test_empty_run_exits_2(tmp_path):
    empty = (FIXTURES / "empty_catalogue.html").read_text(encoding="utf-8")
    output = tmp_path / "books.csv"
    code = main(
        ["--output", str(output), "--start-url", START, "--delay", "0", "--min-rows", "1"],
        fetch=lambda _url: empty,
    )
    assert code == 2
    with output.open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == []


def test_short_run_exits_2(tmp_path):
    fetch, _seen = _fetch_factory()
    code = main(
        [
            "--output",
            str(tmp_path / "books.csv"),
            "--start-url",
            START,
            "--delay",
            "0",
            "--min-rows",
            "20",
        ],
        fetch=fetch,
    )
    assert code == 2
