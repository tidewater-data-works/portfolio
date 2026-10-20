"""The written audit stays tied to the sample files."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "audit-report.md"
ROOT_README = ROOT.parent / "README.md"


def test_audit_report_is_the_client_deliverable():
    text = REPORT.read_text(encoding="utf-8")
    assert "$250" in text
    assert "Northline Freight Cooperative" in text
    assert "fictional" in text
    for name in (
        "samples/events.json",
        "samples/before_twice.csv",
        "samples/shipments.csv",
        "samples/quarantine.csv",
    ):
        assert name in text
        assert (ROOT / name).exists()
    assert "retry" in text.casefold()
    assert "event_id" in text
    assert "25 percent" in text


def test_portfolio_readme_links_this_demo():
    text = ROOT_README.read_text(encoding="utf-8")
    assert "](pipeline-reliability-audit/)" in text
    assert "pipeline-reliability-audit/README.md" in text
