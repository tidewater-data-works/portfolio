# Data cleaning toolkit

Turn a messy vendor export into one row per vendor, and write down every change.

## Problem

A vendor list arrives as CSV or Excel with mixed dates, uneven capitalization, phone numbers in several shapes, abbreviated streets, currency symbols, weights in pounds and grams, and the same supplier typed twice. Someone has to make it one row per vendor before it can be loaded. The load also needs a record of what changed, and why, so a reviewer can see the edits instead of diffing two files by hand.

The companies in the sample (Harbor & Pine Supply Co., Lantern Street Clinic, Cedar Wharf Logistics, Blue Heron Printworks, Osprey Municipal Library, Northbank Analytics LLC) are fictional. Phone numbers use the 555 exchange. There are no personal names and no real street addresses.

## Approach

`clean.py` reads `.csv` or `.xlsx`. Both sample files hold the same seven rows. Each field is cleaned in a fixed order, and a change-log row is written only when the stored value changes.

- **Names.** Extra whitespace is collapsed. A name is retitled only when its letters are all upper case or all lower case, so `Harbor & Pine Supply Co.` stays as written. Small words (`and`, `of`, `the`) stay lower case unless they are first. `Co.`, `LLC`, `Inc.`, and `Ltd.` keep a stable suffix.
- **Phones.** Digits only. A leading `1` on an 11-digit number is dropped, then the number is formatted `(757) 555-0142`. Anything else becomes blank and is logged as unparsed.
- **Streets.** Whitespace is collapsed, tokens are titled, and `st`, `ln`, `rd`, `ave`, `ste`, `ct`, and `way` expand to the full word. A comma stays a comma.
- **Cities.** A city is titled only when its letters are uniformly upper or lower case.
- **Regions.** `VA` / `Virginia` and `NC` / `North Carolina` become the two-letter code. Any other two-letter region is uppercased. A longer unknown region is left as written.
- **Categories.** Aliases in `category_map.csv` map to Packaging, Healthcare, Freight, Print, or Public. An unknown category becomes blank.
- **Dates.** Accepted forms are `YYYY-MM-DD`, `YYYY/MM/DD`, `MM/DD/YYYY`, `12-Mar-2025`, and `Feb 2, 2026`. Month names are matched from a fixed list, so the result does not depend on the machine locale. A two-digit year is left unparsed. US numeric dates are month-first.
- **Currency.** `$`, `€`, and `£` set USD, EUR, and GBP. A three-letter code in the cell wins. A bare number is USD. Amounts are two decimals, half up.
- **Weights.** Pounds use the factor `0.45359237` and are stored in kilograms to three decimals, half up. Grams are divided by 1000. `n/a` becomes blank.

Duplicate detection builds a match key: lower case, `&` rewritten to `and`, punctuation removed, and the tokens `co`, `company`, `llc`, `inc`, and `ltd` dropped. Two keys that score 90 or higher on a token-sort ratio are the same vendor, including a chain of matches. The kept row is the one with more filled fields; a tie keeps the earlier source row. Blank fields on that row are filled from the duplicate, and the duplicate is logged as `merged_duplicate` with the score and the source row it folded into.

The clean file is sorted by vendor name. Ids start at `V-1001`.

## Run

```bash
cd data-cleaning-toolkit
python -m pip install -r requirements.txt
python clean.py --input samples/vendors_raw.csv --output-dir samples
python clean.py --input samples/vendors_raw.xlsx --output-dir samples
```

Both commands write `vendors_clean.csv`, `change_log.csv`, `summary.json`, and `summary.md`. The category map defaults to `category_map.csv` beside the script.

## Sample output

Seven source rows become six. `HARBOR AND PINE SUPPLY` matches `Harbor & Pine Supply Co.` with score 100 and folds into source row 1. The kept name stays `Harbor & Pine Supply Co.` because that spelling was already mixed case; only the extra spaces were removed.

`samples/vendors_clean.csv`:

```text
vendor_id,vendor_name,phone,street,city,region,category,since,contract_amount,contract_currency,weight_kg,source_row
V-1001,Blue Heron Printworks,(757) 555-0133,9 Heron Court,Osprey,VA,Print,2026-02-02,515.16,USD,1.361,5
V-1002,Cedar Wharf Logistics,(757) 555-0177,"400 Harbor Lane, Suite 2",Port City,VA,Freight,2025-01-20,0.00,EUR,0.500,4
V-1003,Harbor & Pine Supply Co.,(757) 555-0142,18 Wharf Lane,Port City,VA,Packaging,2024-03-12,1240.50,USD,5.443,1
V-1004,Lantern Street Clinic,(757) 555-0188,4 Lantern Street,Lantern,NC,Healthcare,2025-03-12,800.00,USD,2.500,3
V-1005,Northbank Analytics LLC,(757) 555-0160,12 Northbank Road,Northbank,VA,,2026-02-02,2250.00,USD,1.000,7
V-1006,Osprey Municipal Library,,1 Reading Way,Osprey,VA,Public,2023-11-01,0.00,USD,,6
```

Harbor & Pine's 12 lb is 5.443 kg. Blue Heron's 3 lb is 1.361 kg. Cedar Wharf's 500 g is 0.500 kg, and the contract is `0.00 EUR`. Lantern Street's bare `800` is logged as assumed USD. Northbank's category `dashboards` is not in the map, so the category cell is blank. Osprey's phone `call the desk` and weight `n/a` are blank. The change log has 53 rows. `summary.md` is the before/after table.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

`make test` from the repository root runs the same suite. Tests rerun the sample, compare the Excel file with the CSV, and check date, phone, weight, region, category, and merge behavior, including a blank filled from a duplicate. No test calls the network.
