# HubSpot deals to Sheets and Slack

An n8n workflow that, every 15 minutes, searches HubSpot for deals modified since the last successful run, upserts them into Google Sheets by `deal_id`, and posts to Slack when a deal is new or its stage changed. An Error Trigger posts a separate Slack message when the sync fails.

`workflow.json` ships with placeholder credential ids only. There are no tokens in this directory.

## Flow

1. **Every 15 Minutes** — schedule trigger, minute interval 15.
2. **Build Search Window** — reads workflow static data. The search starts at `lastRunMs`, or 15 minutes ago on the first run. It records `pendingRunMs` but does not move `lastRunMs` yet, so a failed search retries the same window.
3. **Search Modified Deals** — HubSpot deal search. Filter: `hs_lastmodifieddate` greater than or equal to the window start. Properties: `dealname`, `dealstage`, `amount`, `pipeline`, `hs_lastmodifieddate`. The node retries **3 times, 5 seconds apart** (`retryOnFail`, `maxTries` 3, `waitBetweenTries` 5000). An empty result still emits one item so the cursor can advance.
4. **Dedupe Deals** — skips a record whose static-data key `id|hs_lastmodifieddate` was already seen. Compares `dealstage` with the stage stored for that id. Sets `notify` when the deal is new or the stage changed, and writes a one-line `message`. After a successful pass, `lastRunMs` becomes `pendingRunMs`. The seen map is capped so static data cannot grow without bound.
5. **Upsert Deals** — Google Sheets append-or-update, matching column `deal_id`. Columns: `deal_id`, `dealname`, `dealstage`, `amount`, `hs_lastmodifieddate`.
6. **New or Stage Change** — true branch only when `notify` is true.
7. **Slack Deal Alert** — posts `message` for new deals and stage changes. Amount-only edits are upserted and stay quiet.
8. **Error Trigger** → **Slack Error Alert** — posts the workflow name, the last node, and the error message.

Static data keys are `seen`, `stages`, `lastRunMs`, and `pendingRunMs`.

## Placeholders

Replace these in the n8n editor after import. Do not commit the replacements.

| Placeholder | Where |
| --- | --- |
| `PLACEHOLDER_HUBSPOT_CREDENTIAL_ID` | HubSpot App Token credential |
| `PLACEHOLDER_SHEETS_CREDENTIAL_ID` | Google Sheets OAuth credential |
| `PLACEHOLDER_SLACK_CREDENTIAL_ID` | Slack API credential |
| `PLACEHOLDER_SPREADSHEET_ID` | Spreadsheet id on the Sheets node |
| `PLACEHOLDER_SLACK_CHANNEL` | Channel name on both Slack nodes |

The sheet tab must be named `Deals` and its header row must be `deal_id`, `dealname`, `dealstage`, `amount`, `hs_lastmodifieddate`.

## Test with a free HubSpot account and n8n

1. Create a free HubSpot developer account at [developers.hubspot.com](https://developers.hubspot.com/). From that account, create a test account. That test account is a CRM sandbox.
2. In the test account, open Settings → Integrations → Private Apps, create a private app, and grant `crm.objects.deals.read`. Copy the access token into an n8n **HubSpot App Token** credential. Leave the token out of git.
3. Create a Google Sheet as described above and connect a Google Sheets OAuth credential in n8n. Put the spreadsheet id in the Sheets node.
4. Create a Slack app with the `chat:write` scope, install it to a workspace, and connect the Slack credential. Invite the app to the channel you put on the Slack nodes.
5. Start n8n locally:

```bash
npx n8n
```

6. Import `workflow.json` (menu → Import from File, or `npx n8n import:workflow --input=workflow.json`). Attach the three credentials and replace the spreadsheet id and channel name.
7. Execute the workflow once from the editor. Confirm the search window, the HubSpot results, the sheet rows, and that Slack only fires for a new deal or a stage change. Run it again with no new modifications and confirm the dedupe key suppresses repeats.
8. Activate the workflow so the 15-minute schedule runs.
9. Point the sync at the Error Trigger. In the sync workflow, Settings → Error workflow. If this n8n version allows a workflow to be its own error workflow, select this one. If it rejects that, import the file a second time, leave the copy inactive so its schedule does not double-run, and select the copy as the error workflow. The copy's Error Trigger and Slack Error Alert nodes are the failure path. Force a failure (revoke the HubSpot token temporarily, for example) and confirm Slack receives the error text. Restore the token afterward.

## Check the file offline

```bash
python validate_workflow.py
python -m pytest -q
```

`validate_workflow.py` parses `workflow.json` and fails when a connection names a node that is not in the file. It also checks the 15-minute schedule, the HubSpot retry settings, the `id|hs_lastmodifieddate` dedupe key, the `deal_id` upsert, the Error Trigger's Slack connection, and that every credential id is a `PLACEHOLDER_` value. `make test` from the repository root runs the same pytest file. No test starts n8n or contacts HubSpot, Google, or Slack.
