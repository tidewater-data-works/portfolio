# AI invoice and receipt field extractor

Read invoice and receipt text, return a validated field object, and park low-confidence documents in a review queue. The default run uses a rules parser and does not need an API key.

## Problem

A bookkeeping inbox holds invoices and counter receipts as plain text (an email body, or the text already pulled out of a PDF). Someone has to copy the vendor, the document number, the date, and the total into a sheet. The messy slips are the expensive ones: a two-digit date, no document number, or a total that does not match the lines. Those should not land in the sheet as if they were clean.

## Approach

Each `.txt` file is one document. The filename stem is the document id.

`schema.py` is a pydantic model. A payload with an extra key, a date that is not `YYYY-MM-DD`, or a currency that is not three letters is rejected. Money fields accept a `$` and thousands separators. Line amounts may be negative; that is a confidence flag, not a schema error, so the row can still be reviewed.

Two backends implement the same call, `extract_fields(text, document_id=...)`:

- **rules** (the default) reads `Label: value` lines and line-item rows. Item columns are separated by two or more spaces. A `MM/DD/YYYY` date is converted to ISO. A two-digit year is not converted. When the vendor line has no label, the parser guesses from the first plain line and sets `weak_vendor`.
- **mock** replays a JSON object of scripted responses keyed by document id. A missing id raises the same "unavailable" error a live client raises when it cannot be called.
- **openai** posts a chat completion with temperature 0. It runs only when `OPENAI_API_KEY` is set. Tests pass a stand-in for the HTTP call. There is no key in this repo.

If the model backend is unavailable, returns non-JSON, or fails the schema, the rules parser produces the row and the flags include `llm_fallback`. Quality checks then run on whichever fields were kept: missing vendor or total, line amounts that do not equal quantity times unit price, line items that do not add up, and subtotal plus tax against the total.

Confidence starts at 0.90 for rules and 0.97 for a schema-valid model response, then subtracts a fixed penalty per flag. A row needs review when the score is below 0.80, or when a flag is in the force-review set (a guessed vendor, a money mismatch, a missing total, and the other flags listed in `schema.py`). `llm_fallback` is recorded and does not, by itself, queue a clean rules row.

`--strict` exits 1 when the queue is not empty. The default exit code is 0 so a scheduled run still writes the queue for a person to clear.

## Run

```bash
cd ai-invoice-field-extractor
python -m pip install -r requirements.txt
python extract.py --input samples/documents --json samples/extracted.json --review samples/review_queue.csv
python extract.py \
  --input samples/documents \
  --backend mock \
  --mock-file samples/mock_responses.json \
  --json samples/extracted_mock.json \
  --review samples/review_queue_mock.csv
```

`OPENAI_API_KEY` is read only for `--backend openai`. Without it, that mode logs a warning and uses the rules parser.

## Sample output

`samples/documents/` has four fictional texts. Harbor & Pine invoice `HP-2026-0142` and Blue Heron receipt `R-88421` extract cleanly at confidence **0.90** and stay out of the queue. The other two are queued.

`samples/review_queue.csv` from the rules run:

```text
document_id,document_type,vendor_name,document_number,total,confidence,flags,review_reason
counter-2026-0318,receipt,blue heron counter,,9.00,0.33,ambiguous_date|inferred_document_type|line_sum_mismatch|missing_document_number|weak_vendor,Date is not a full ISO date; Document type was inferred; Line items do not add up to the subtotal or total; Document number is missing; Vendor was guessed from an unlabeled line
partial-nb-draft,invoice,,,1800.00,0.55,missing_document_number|missing_vendor,Document number is missing; Vendor is missing
```

The counter slip says amount due 9.00. The lines are 4.50 and 3.25. The date on the slip is `03/18/26`, which is stored as null.

`samples/mock_responses.json` scripts a model response for that slip only: vendor Blue Heron Printworks, number `R-COUNTER-0318`, date 2026-03-18, and the same 9.00 total. `samples/extracted_mock.json` records that row as `extractor: llm` at confidence **0.82**. It stays in `samples/review_queue_mock.csv` because the lines still do not add up to 9.00. The other three documents are not in the mock file, so they fall back to rules; the partial draft remains queued, and the two clean documents stay off the queue with an `llm_fallback` flag.

`samples/extracted.json` is the full rules extract, including line items and the confidence block.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

`make test` from the repository root runs the same suite. Tests cover the rules parse, the score, pydantic rejections, a scripted mock response, a fenced chat completion, a schema failure, a non-JSON completion, and a missing API key. The HTTP call is a stand-in. No test contacts an API.
