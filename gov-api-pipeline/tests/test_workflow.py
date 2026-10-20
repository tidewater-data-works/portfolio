"""The weekday workflow runs the pipeline and uploads artifacts only."""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "daily.yml"


def test_weekday_cron_writes_artifacts_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    cron = re.search(r"""cron:\s*['"]([^'"]+)['"]""", text)
    assert cron is not None
    minute, hour, day_of_month, month, day_of_week = cron.group(1).split()
    assert day_of_week == "1-5"
    assert day_of_month == "*"
    assert month == "*"
    assert minute.isdigit() and hour.isdigit()
    assert "gov-api-pipeline" in text
    assert "upload-artifact" in text
    assert "git push" not in text
    assert "git commit" not in text
    assert "GOOGLE_SERVICE_ACCOUNT_JSON" not in text
    assert "ALERT_WEBHOOK_URL" not in text
    assert "contents: read" in text
    assert "actions: write" not in text
    assert "timeout-minutes: 15" in text
