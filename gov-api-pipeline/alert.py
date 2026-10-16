"""Deliver a pipeline alert to ALERT_WEBHOOK_URL, or fail closed when it is unset."""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)

SOURCE = "tidewater-data-works"


def send_alert(
    message: str,
    *,
    series_id: str = "LNS14000000",
    webhook_url: str | None = None,
    poster: Callable[..., Any] | None = None,
) -> int:
    """POST ``message`` when a webhook is configured.

    Returns 0 after a 2xx response. Returns 1 when ALERT_WEBHOOK_URL is unset,
    when the request fails, or when the endpoint returns an HTTP error.
    """
    url = webhook_url if webhook_url is not None else os.environ.get("ALERT_WEBHOOK_URL", "")
    url = url.strip()
    if not url:
        logger.error("ALERT_WEBHOOK_URL is not set; %s", message)
        return 1

    payload = {"text": message, "source": SOURCE, "series_id": series_id}
    post = poster or requests.post
    try:
        response = post(url, json=payload, timeout=20)
    except Exception as exc:
        logger.error("Alert webhook request failed: %s", exc)
        return 1
    status = getattr(response, "status_code", None)
    if status is None or status >= 400:
        logger.error("Alert webhook returned HTTP %s", status)
        return 1
    logger.info("Alert delivered")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Send a Tidewater Data Works alert.")
    parser.add_argument("--message", required=True)
    parser.add_argument("--series-id", default="LNS14000000")
    args = parser.parse_args(argv)
    return send_alert(args.message, series_id=args.series_id)


if __name__ == "__main__":
    raise SystemExit(main())
