"""Fixture crawls for the JavaScript quotes site. No browser and no network."""

from __future__ import annotations

import csv
from pathlib import Path

from quotes_js import COLUMNS, PlaywrightClient, collect_quotes, main, parse_quotes

FIXTURES = Path(__file__).parent / "fixtures"
START = "https://quotes.toscrape.com/js/"
PAGE_2 = "https://quotes.toscrape.com/js/page/2/"


class _Pages:
    def __init__(self, items: list[tuple[str, str]]) -> None:
        self.items = items
        self.index = 0
        self.visits: list[str] = []

    @property
    def url(self) -> str:
        return self.items[self.index][0]

    def goto(self, url: str) -> None:
        self.visits.append(url)
        self.index = 0

    def content(self) -> str:
        return self.items[self.index][1]

    def wait_for_quotes(self) -> None:
        return None

    def click_next(self) -> bool:
        if self.index + 1 >= len(self.items):
            return False
        self.index += 1
        return True


def _client() -> _Pages:
    return _Pages(
        [
            (START, (FIXTURES / "quotes_page.html").read_text(encoding="utf-8")),
            (PAGE_2, (FIXTURES / "quotes_page_2.html").read_text(encoding="utf-8")),
        ]
    )


def test_parse_quotes_and_tags():
    html = (FIXTURES / "quotes_page.html").read_text(encoding="utf-8")
    rows, has_next = parse_quotes(html, START)
    assert has_next is True
    assert rows[0]["text"] == "First fixture line."
    assert rows[0]["author"] == "Author One"
    assert rows[0]["tags"] == "alpha|beta"
    assert rows[0]["url"] == START
    assert len(rows) == 2


def test_pagination_dedupes_on_text_and_author():
    rows = collect_quotes(_client(), START, max_pages=10)
    assert [(row["text"], row["url"]) for row in rows] == [
        ("First fixture line.", START),
        ("Second fixture line.", START),
        ("Third fixture line.", PAGE_2),
    ]
    assert len(rows) == 3


def test_playwright_client_follows_next_and_stops():
    class _Locator:
        def __init__(self, page: "_FakePage") -> None:
            self.page = page

        def count(self) -> int:
            return 0 if self.page.index == len(self.page.pages) - 1 else 1

        def click(self) -> None:
            self.page.index += 1

    class _FakePage:
        def __init__(self) -> None:
            self.pages = [
                (FIXTURES / "quotes_page.html").read_text(encoding="utf-8"),
                (FIXTURES / "quotes_page_2.html").read_text(encoding="utf-8"),
            ]
            self.urls = [START, PAGE_2]
            self.index = 0
            self.url = START

        def goto(self, url: str, **_kwargs) -> None:
            self.url = url
            self.index = 0

        def content(self) -> str:
            return self.pages[self.index]

        def wait_for_selector(self, _selector: str, timeout: int = 0) -> None:
            return None

        def locator(self, _selector: str) -> _Locator:
            return _Locator(self)

        def wait_for_load_state(self, _state: str) -> None:
            self.url = self.urls[self.index]

    rows = collect_quotes(PlaywrightClient(_FakePage()), START, max_pages=10)
    assert [row["text"] for row in rows] == [
        "First fixture line.",
        "Second fixture line.",
        "Third fixture line.",
    ]


def test_csv_columns(tmp_path):
    output = tmp_path / "quotes.csv"
    code = main(
        ["--output", str(output), "--start-url", START, "--max-pages", "10", "--min-rows", "3"],
        client=_client(),
    )
    assert code == 0
    with output.open(encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    assert list(records[0]) == COLUMNS
    assert len(records) == 3


def test_empty_run_exits_2(tmp_path):
    class TimeoutError(Exception):
        """Same class name Playwright raises when the quote list never appears."""

    class _EmptyLocator:
        def count(self) -> int:
            return 0

        def click(self) -> None:
            raise AssertionError("empty page has no next control")

    class _EmptyPage:
        url = START

        def goto(self, _url: str, **_kwargs) -> None:
            return None

        def content(self) -> str:
            return (FIXTURES / "empty_quotes.html").read_text(encoding="utf-8")

        def wait_for_selector(self, _selector: str, timeout: int = 0) -> None:
            raise TimeoutError("no quotes")

        def locator(self, _selector: str) -> _EmptyLocator:
            return _EmptyLocator()

        def wait_for_load_state(self, _state: str) -> None:
            return None

    output = tmp_path / "quotes.csv"
    code = main(
        ["--output", str(output), "--min-rows", "1"],
        client=PlaywrightClient(_EmptyPage()),
    )
    assert code == 2
    with output.open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == []


def test_short_run_exits_2(tmp_path):
    code = main(
        ["--output", str(tmp_path / "quotes.csv"), "--min-rows", "10"],
        client=_client(),
    )
    assert code == 2


def test_readme_describes_the_author_field_as_sample_data():
    text = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "author field published by the sandbox" in text
    assert "not a byline" not in text
