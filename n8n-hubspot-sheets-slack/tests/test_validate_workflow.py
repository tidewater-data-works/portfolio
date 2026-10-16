"""workflow.json must parse, and every connection must name an existing node."""

from __future__ import annotations

from pathlib import Path

from validate_workflow import connection_errors, load_and_validate, validate_workflow

WORKFLOW = Path(__file__).resolve().parents[1] / "workflow.json"


def test_workflow_parses_and_connections_reference_existing_nodes():
    errors = load_and_validate(WORKFLOW)
    assert errors == []


def test_missing_connection_target_is_reported():
    workflow = {
        "nodes": [
            {"name": "Start", "type": "n8n-nodes-base.manualTrigger"},
        ],
        "connections": {
            "Start": {
                "main": [
                    [
                        {"node": "Missing", "type": "main", "index": 0},
                    ]
                ]
            }
        },
    }
    errors = connection_errors(workflow)
    assert any("Missing" in error and "missing node" in error for error in errors)


def test_unknown_connection_source_is_reported():
    workflow = {
        "nodes": [{"name": "Start", "type": "n8n-nodes-base.manualTrigger"}],
        "connections": {"Gone": {"main": [[{"node": "Start", "type": "main", "index": 0}]]}},
    }
    errors = connection_errors(workflow)
    assert any("Gone" in error for error in errors)


def test_invalid_json_is_reported(tmp_path):
    path = tmp_path / "workflow.json"
    path.write_text("{", encoding="utf-8")
    errors = load_and_validate(path)
    assert errors and errors[0].startswith("invalid JSON:")


def test_secret_like_values_are_rejected():
    workflow = {
        "nodes": [
            {
                "name": "Slack",
                "type": "n8n-nodes-base.slack",
                "parameters": {"text": "token xoxb-123"},
                "credentials": {"slackApi": {"id": "PLACEHOLDER_SLACK_CREDENTIAL_ID", "name": "Slack account"}},
            }
        ],
        "connections": {},
    }
    errors = validate_workflow(workflow)
    assert any("secret-like" in error for error in errors)
