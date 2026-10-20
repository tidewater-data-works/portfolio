# Data quality report

As of: 2026-03-20
Orders: 16
Quarantined rows: 9
Gate: fail

| Check | Result | Detail |
| --- | --- | --- |
| required_columns | pass | ok |
| customer_list | pass | 5 customers |
| order_id_not_null | pass | ok |
| customer_id_not_null | pass | ok |
| ordered_on_not_null | pass | ok |
| status_not_null | pass | ok |
| amount_not_null | fail | 1 row blank |
| quantity_not_null | pass | ok |
| ordered_on_iso | fail | 1 row not an ISO date |
| amount_numeric | pass | ok |
| quantity_numeric | pass | ok |
| unique_order_id | fail | repeated ORD-1002 on 2 rows |
| amount_range | fail | 2 rows outside 0.01-50000 |
| quantity_range | fail | 1 row outside 1-500 |
| status_allowed | fail | 1 row outside booked, shipped, delivered, cancelled |
| customer_id_known | fail | 1 row with an unknown customer_id |
| ship_to_city_present | warn | 1 row blank |
| freshness | pass | newest 2026-03-18 is 2 days old |
| row_count | pass | 16 rows |
| row_count_drift | warn | 16 vs 20 (20.0%) |
