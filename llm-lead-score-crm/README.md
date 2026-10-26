# Lead qualification to a CRM stub

Read an inbound lead note, require a structured JSON qualification from a model, score it Hot, Warm, Medium, or Cold, and upsert the contacts that clear the gate. Invalid or low-confidence model output is written to a quarantine file and is not sent to the CRM.

The companies, people, and notes in `samples/` are fictional. The CRM is a stub. Nothing in the sample run calls a live model or a live CRM.

## Problem

A desk collects web-form and email notes and has to decide which ones are worth a follow-up. A model can read the note, but a free-text answer is not a CRM record. If the model returns prose, an extra key, or a low confidence, that row has to stop. It must not be written as if it were a scored contact. A second run of the same batch has to update the contact for that email, not create another one.

## Approach

Each lead is one object: `lead_id`, `email`, `company`, and `note`. The email is the upsert key, after it is stripped and lowercased. A value that is not an email is quarantined as `missing_lead_key`, and the model is not called.

The model has to return one JSON object with `intent`, `budget_stated`, `timeline_days`, `contact_role`, `fit_reason`, and `model_confidence`. It does not pick the band. `schema.py` rejects an extra key (including `band` or `score`), a confidence that is not a finite number, a timeline that is not an integer from 0 through 3650, or a `budget_stated` value that is not a boolean. Confidence is rounded half up to two decimals and then compared with **0.70**. Below that, the reason is `low_model_confidence`.

Points, capped at 100:

| Signal | Points |
| --- | --- |
| intent `ready_to_buy` | 45 |
| intent `evaluating` | 30 |
| intent `browsing` | 15 |
| intent `not_a_fit` | 0 |
| `budget_stated` is true | 25 |
| `timeline_days` 0–30 | 25 |
| `timeline_days` 31–90 | 15 |
| `timeline_days` 91–180 | 5 |
| `contact_role` present | 5 |

| Score | Band |
| --- | --- |
| 80–100 | Hot |
| 55–79 | Warm |
| 30–54 | Medium |
| 0–29 | Cold |

A Cold lead is still upserted. The quarantine file is not a fifth band, and it has no band column.

`llm.py` has two backends. **mock** replays `samples/mock_responses.json`, keyed by `lead_id`. An object is a qualification. A string is raw model text, which is how the sample scripts a non-JSON reply. **openai** posts a chat completion with temperature 0, and only when `OPENAI_API_KEY` is set. Tests pass a stand-in for the HTTP call. There is no key in this repo. If the backend is unavailable, or the text is not a JSON object, that lead is quarantined (`model_unavailable` or `invalid_model_json`) and the run continues with the rest of the batch.

`crm.py` upserts by email, and the upsert is idempotent: the same email updates the stored contact. `FileCrm` rewrites a JSON object with `os.replace`. `MemoryCrm` is the in-process stub. `HttpCrmClient` POSTs the contact plus `idempotency_key` set to the email. The raw note is not part of the contact. A transport error or a non-2xx response fails that upsert. `CRM_API_TOKEN` is sent only when it is set. This repo does not contain a token.

`qualify.py` qualifies the whole batch before any upsert. The quarantine CSV is written first. A CRM error then exits 1. Upserts that already succeeded stay in the file; a rerun updates those emails instead of inserting duplicates. When every lead is quarantined, an existing CRM file is left as it was. The default exit code is 0 when the only problem is a quarantined lead, so a scheduled run still drops the file for a person to clear. `--strict` exits 1 when the quarantine is not empty.

`app.py` is the same two functions behind FastAPI. `POST /qualify` takes `{"leads": [...]}` and returns the accepted contacts, the quarantine rows, and `created` or `updated` for each upsert. A CRM error is HTTP 502, not a 200 that lists those leads as accepted. Upserts that already landed stay in the store. `POST /crm/contacts` is the stub contract. The default app loads the sample mock and an empty memory store. Importing it does not open a socket. A lead id that is not in the mock file is quarantined as `model_unavailable`.

## Run

```bash
cd llm-lead-score-crm
python -m pip install -r requirements.txt
python qualify.py \
  --input samples/leads.json \
  --backend mock \
  --mock-file samples/mock_responses.json \
  --crm /tmp/crm_contacts.json \
  --quarantine /tmp/quarantine.csv
```

Run the same command again. The log line changes from `4 created` to `4 updated`, and `/tmp/crm_contacts.json` still has four keys.

`OPENAI_API_KEY` is read only for `--backend openai`. Without it, every lead is quarantined as `model_unavailable` and the CRM file is left unchanged.

The API, still on the sample mock:

```bash
uvicorn app:app
```

## Sample output

`samples/leads.json` has six fictional notes. Four clear the gate. `LD-2041` is stored under the lowercased email.

| Lead | Email | Band | Score |
| --- | --- | --- | --- |
| LD-2041 | mara.ellison@cedarwharf.example | Hot | 100 |
| LD-2042 | jonah.peck@lanternmail.example | Warm | 75 |
| LD-2043 | priya.shah@ospreypress.example | Medium | 45 |
| LD-2044 | alex.kim@mailbox.example | Cold | 0 |

`samples/quarantine.csv`:

```text
lead_id,email,company,reason,detail
LD-2045,riley.cho@harborpine.example,Harbor & Pine,low_model_confidence,model_confidence 0.41 is below 0.70
LD-2046,sam.ortiz@blueheron.example,Blue Heron Printworks,invalid_model_json,model response was not JSON
```

LD-2045 is schema-valid. The confidence is 0.41, so it is not a CRM contact. LD-2046 is scripted in the mock file as the sentence `This lead looks warm.`, not as JSON. `samples/crm_contacts.json` has the four emails above and neither of those two.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

`make test` from the repository root runs the same suite. Tests cover the point table, the confidence floor, a schema rejection when the model includes a band, the committed sample files, a second upsert of the same emails, a CRM failure, and the FastAPI routes. The model call and the CRM call are stand-ins. No test contacts an API.
