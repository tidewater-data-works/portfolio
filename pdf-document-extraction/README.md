# PDF document extraction

Pull header fields and ruled tables out of invoice and report PDFs, check the amounts, and write CSV and JSON a workbook can open.

## Problem

A client receives a handful of PDFs — vendor invoices, plus a one-page activity report — and retypes them into a sheet. The typed totals sometimes disagree with the lines, and an empty extract looks the same as a real one. The job is to land the fields and the tables in files, and to fail the run when the arithmetic or the required fields do not hold.

The PDFs in this demo are synthetic. The companies (Harbor & Pine Supply Co., Northbank Analytics LLC, Blue Heron Printworks, Cedar Wharf Logistics, Lantern Street Clinic, Osprey Municipal Library) are fictional. There are no personal names and no real addresses.

## Approach

`generate_samples.py` draws each document with reportlab: a heading, one `Label: value` line per field, a ruled table, and, on invoices, subtotal, tax, and total under the table. Reportlab's invariant mode keeps a rebuild byte-for-byte stable.

`extract.py` opens each PDF with pdfplumber. Header fields come from the labeled lines. The line-item table is the grid whose header is Description, Quantity, Unit price, Amount. The report table is the grid whose header is Metric, Value, Notes. Dollar amounts accept a `$` and thousands separators and reject more than two decimal places.

An invoice passes validation when:

- vendor, invoice number, invoice date, due date, bill-to, currency, subtotal, tax, and total are present
- the invoice number matches the `HP-2026-0142` shape and the currency is three letters
- both dates are real calendar dates and the due date is on or after the invoice date
- each line amount equals quantity times unit price, to the cent
- the line amounts sum to the subtotal, and subtotal plus tax equals the total

A report passes when the title, organization, prepared-by, and period are present, the period is ordered, and every metric value is numeric.

The JSON file includes every document and its validation errors. The CSVs include only documents that passed, so a bad invoice cannot land in the clean extract. The process exits 1 when any document fails or when the input folder has no PDFs.

## Run

```bash
cd pdf-document-extraction
python -m pip install -r requirements.txt
python generate_samples.py --output-dir samples
python extract.py --input-dir samples --output-dir samples
```

`generate_samples.py` writes the four PDFs. `extract.py` writes `invoices.json`, `invoices.csv`, `line_items.csv`, and `report_rows.csv` into the output directory. It reads `*.pdf` only, so it can write back into `samples/` without treating its own output as input.

## Sample output

Four PDFs are committed under `samples/`. Three are invoices and one is the Q1 activity report for Cedar Wharf Logistics.

`samples/invoices.csv`:

```text
source_file,vendor_name,invoice_number,invoice_date,due_date,bill_to,currency,subtotal,tax,total
blue-heron-bhp-3319.pdf,Blue Heron Printworks,BHP-3319,2026-03-02,2026-04-01,Cedar Wharf Logistics,USD,486.00,29.16,515.16
harbor-pine-hp-2026-0142.pdf,Harbor & Pine Supply Co.,HP-2026-0142,2026-02-12,2026-03-14,Lantern Street Clinic,USD,245.40,12.27,257.67
northbank-nb-2026-088.pdf,Northbank Analytics LLC,NB-2026-088,2026-01-20,2026-02-19,Osprey Municipal Library,USD,2250.00,0.00,2250.00
```

`samples/line_items.csv` has one row per invoice line (eight rows). Harbor & Pine's lines are 114.00, 75.00, and 56.40, which sum to the 245.40 subtotal; tax at five percent is 12.27 and the total is 257.67. `samples/report_rows.csv` has the four Q1 metrics, including an on-time share of 0.94. `samples/invoices.json` is the same extract with a `validation` object on each document. On this batch every document has `"ok": true`.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

`make test` from the repository root runs the same suite. Tests generate PDFs in a temporary directory, open the committed samples, and check a mismatched line amount, a due date before the invoice date, a reversed report period, an unknown document type, and a file that is not a PDF. No test calls the network.
