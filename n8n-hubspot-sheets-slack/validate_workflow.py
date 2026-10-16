"""Check that workflow.json parses and that every connection names a real node."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

FORBIDDEN_FRAGMENTS = (
    "BEGIN PRIVATE KEY",
    "xoxb-",
    "xoxp-",
    "pat-na",
    "sk_live_",
    "ya29.",
)


def load_and_validate(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"invalid JSON: {exc}"]
    except OSError as exc:
        return [f"could not read workflow: {exc}"]
    if not isinstance(data, dict):
        return ["workflow must be a JSON object"]
    return validate_workflow(data, raw_text=raw)


def validate_workflow(data: dict[str, Any], *, raw_text: str | None = None) -> list[str]:
    errors = connection_errors(data)
    errors.extend(structure_errors(data))
    text = raw_text if raw_text is not None else json.dumps(data)
    for fragment in FORBIDDEN_FRAGMENTS:
        if fragment in text:
            errors.append(f"workflow contains a secret-like value ({fragment})")
    return errors


def connection_errors(data: dict[str, Any]) -> list[str]:
    """Return one message per connection that does not reference existing nodes."""
    errors: list[str] = []
    nodes = data.get("nodes")
    connections = data.get("connections")
    if not isinstance(nodes, list):
        return ["nodes must be a list"]
    if not isinstance(connections, dict):
        return ["connections must be an object"]

    names: list[str] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or not isinstance(node.get("name"), str) or not node["name"]:
            errors.append(f"nodes[{index}] is missing a name")
            continue
        names.append(node["name"])
    if len(names) != len(set(names)):
        errors.append("node names must be unique")
    known = set(names)

    for source, outputs in connections.items():
        if source not in known:
            errors.append(f"connection source {source!r} is not a node")
        if not isinstance(outputs, dict):
            errors.append(f"connections for {source!r} must be an object")
            continue
        for output_type, groups in outputs.items():
            if not isinstance(groups, list):
                errors.append(f"{source!r} output {output_type!r} must be a list")
                continue
            for group in groups:
                if not isinstance(group, list):
                    errors.append(f"{source!r} has a connection group that is not a list")
                    continue
                for link in group:
                    if not isinstance(link, dict):
                        errors.append(f"{source!r} has a connection that is not an object")
                        continue
                    target = link.get("node")
                    if target not in known:
                        errors.append(
                            f"connection {source!r} -> {target!r} references a missing node"
                        )
                    if not isinstance(link.get("type"), str) or not link.get("type"):
                        errors.append(f"connection {source!r} -> {target!r} is missing a type")
                    if not isinstance(link.get("index"), int):
                        errors.append(f"connection {source!r} -> {target!r} is missing an index")
    return errors


def structure_errors(data: dict[str, Any]) -> list[str]:
    """Check the schedule, retry policy, dedupe key, upsert, and error path."""
    errors: list[str] = []
    nodes = data.get("nodes")
    connections = data.get("connections")
    if not isinstance(nodes, list) or not isinstance(connections, dict):
        return errors
    by_name = {
        node["name"]: node
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("name"), str)
    }

    schedules = [node for node in by_name.values() if node.get("type") == "n8n-nodes-base.scheduleTrigger"]
    if not _has_fifteen_minute_schedule(schedules):
        errors.append("workflow needs a schedule trigger that runs every 15 minutes")

    hubspot = [node for node in by_name.values() if node.get("type") == "n8n-nodes-base.hubspot"]
    if not any(_hubspot_search_is_configured(node) for node in hubspot):
        errors.append(
            "workflow needs a HubSpot deal search for hs_lastmodifieddate with 3 retries, 5 seconds apart"
        )

    code = [node for node in by_name.values() if node.get("type") == "n8n-nodes-base.code"]
    if not any("id|hs_lastmodifieddate" in str(node.get("parameters", {}).get("jsCode", "")) for node in code):
        errors.append("dedupe code must key static data on id|hs_lastmodifieddate")

    sheets = [node for node in by_name.values() if node.get("type") == "n8n-nodes-base.googleSheets"]
    if not any(_sheets_matches_deal_id(node) for node in sheets):
        errors.append("Google Sheets upsert must match on deal_id")

    error_names = [name for name, node in by_name.items() if node.get("type") == "n8n-nodes-base.errorTrigger"]
    if not error_names:
        errors.append("workflow needs an Error Trigger")
    else:
        slack_names = {
            name for name, node in by_name.items() if node.get("type") == "n8n-nodes-base.slack"
        }
        for name in error_names:
            targets = _targets(connections, name)
            if not any(target in slack_names for target in targets):
                errors.append(f"Error Trigger {name!r} must connect to a Slack node")

    if not any(node.get("type") == "n8n-nodes-base.slack" for node in by_name.values()):
        errors.append("workflow needs a Slack node for deal alerts")

    for node in by_name.values():
        credentials = node.get("credentials") or {}
        if not isinstance(credentials, dict):
            errors.append(f"node {node.get('name')!r} has credentials that are not an object")
            continue
        for cred_type, cred in credentials.items():
            cred_id = cred.get("id") if isinstance(cred, dict) else None
            if not isinstance(cred_id, str) or not cred_id.startswith("PLACEHOLDER_"):
                errors.append(
                    f"node {node.get('name')!r} credential {cred_type!r} must use a PLACEHOLDER_ id"
                )
    return errors


def _has_fifteen_minute_schedule(nodes: list[dict[str, Any]]) -> bool:
    for node in nodes:
        intervals = node.get("parameters", {}).get("rule", {}).get("interval", [])
        if not isinstance(intervals, list):
            continue
        for item in intervals:
            if isinstance(item, dict) and item.get("field") == "minutes" and item.get("minutesInterval") == 15:
                return True
    return False


def _hubspot_search_is_configured(node: dict[str, Any]) -> bool:
    parameters = node.get("parameters") or {}
    if parameters.get("resource") != "deal" or parameters.get("operation") != "search":
        return False
    if node.get("retryOnFail") is not True:
        return False
    if node.get("maxTries") != 3 or node.get("waitBetweenTries") != 5000:
        return False
    encoded = json.dumps(parameters.get("filterGroupsUi", {}))
    return "hs_lastmodifieddate" in encoded and "GTE" in encoded


def _sheets_matches_deal_id(node: dict[str, Any]) -> bool:
    columns = node.get("parameters", {}).get("columns", {})
    matching = columns.get("matchingColumns") if isinstance(columns, dict) else None
    operation = node.get("parameters", {}).get("operation")
    return operation == "appendOrUpdate" and isinstance(matching, list) and "deal_id" in matching


def _targets(connections: dict[str, Any], source: str) -> list[str]:
    targets: list[str] = []
    outputs = connections.get(source, {})
    if not isinstance(outputs, dict):
        return targets
    for groups in outputs.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, list):
                continue
            for link in group:
                if isinstance(link, dict) and isinstance(link.get("node"), str):
                    targets.append(link["node"])
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the HubSpot workflow JSON.")
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path(__file__).with_name("workflow.json"),
    )
    args = parser.parse_args(argv)
    errors = load_and_validate(args.path)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"{args.path.name}: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
