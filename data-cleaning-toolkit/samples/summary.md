# Vendor cleaning summary

Input rows: 7
Output rows: 6
Rows merged as duplicates: 1
Logged changes: 53

| Source row | Vendor before | Vendor after | In the clean file |
| --- | --- | --- | --- |
| 1 | Harbor  &  Pine Supply Co. | Harbor & Pine Supply Co. | yes |
| 2 | HARBOR AND PINE SUPPLY | Harbor & Pine Supply Co. | merged into row 1 |
| 3 |  lantern street clinic | Lantern Street Clinic | yes |
| 4 | Cedar Wharf Logistics | Cedar Wharf Logistics | yes |
| 5 | blue heron printworks | Blue Heron Printworks | yes |
| 6 | Osprey Municipal Library | Osprey Municipal Library | yes |
| 7 | Northbank Analytics LLC | Northbank Analytics LLC | yes |

| Reason | Count |
| --- | --- |
| category_mapped | 6 |
| category_unmapped | 1 |
| city_case | 5 |
| currency_assumed_usd | 3 |
| currency_parsed | 3 |
| date_normalized | 4 |
| merged_duplicate | 1 |
| name_case | 3 |
| phone_normalized | 4 |
| phone_unparsed | 1 |
| region_normalized | 6 |
| street_normalized | 7 |
| weight_missing | 1 |
| weight_normalized | 6 |
| whitespace | 2 |
