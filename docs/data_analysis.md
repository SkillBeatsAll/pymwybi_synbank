## A. Data audit


| Dataset | Rows | Date range | Entities / sectors |
|---|---:|---|---|
| Transactional banking | 2,802,875 | 2023-07-01 to 2026-06-30 | 20 / 7 |
| Cross-border payments | 241,117 | 2023-07-01 to 2026-06-30 | 20 / 7 |
| Trade finance | 20,303 | 2023-07-01 to 2026-06-30 | 20 / 7 |

Each file has activity on all 1,096 calendar days in the range. The same 20 entity IDs occur in every dataset and map one-to-one to the same entity names and sectors—no cross-file mapping conflicts were found.

Common sectors: `consumer`, `industrials_pharma`, `insurance`, `mining`, `real_estate`, `tech`, `telecoms`.

| Dataset | Columns and recommended types |
|---|---|
| Transactional | `transaction_id`, `entity_id`, `entity_name`, `sector`, `leg_type`, `direction`, `currency`, `channel`, `beneficiary_name`, `reference`, `memo`: string/categorical; `date`: ISO date; `amount_zar`: decimal |
| Cross-border | `transaction_id`, `entity_id`, `entity_name`, `sector`, `direction`, `currency_pair`, `counterparty_country`, `corridor_type`, `beneficiary_name`, `reference`, `memo`: string/categorical; `date`: ISO date; `value_zar`: decimal |
| Trade finance | `instrument_id`, `entity_id`, `entity_name`, `sector`, `instrument_type`, `direction`, `counterparty_country`, `commodity_or_contract_type`, `status`, `beneficiary_name`, `reference`, `memo`: string/categorical; `date`: ISO date; `tenor_days`: integer; `value_zar`: decimal |

Missing values:

| Dataset | Missing values |
|---|---:|
| Transactional | `memo`: 2,799,218 (99.87%) |
| Cross-border | `memo`: 240,669 (99.81%); `counterparty_country`: 3,665 (1.52%) |
| Trade finance | `memo`: 20,209 (99.54%); `counterparty_country`: 319 (1.57%) |

No invalid dates, non-numeric amounts, or non-positive amounts were found.

Key categorical values:

- Transactional: `direction` = inbound/outbound; `leg_type` = collections, supplier_payments, intercompany_sweeps, tax, payroll; `channel` = EFT, SWIFT, Internal Transfer, RTC, Debit Order.
- Transactional currency is inconsistent: `ZAR` occurs 2,774,594 times and lowercase `zar` 28,281 times.
- Cross-border: five currency pairs—USD/ZAR, EUR/ZAR, GBP/ZAR, CNY/ZAR, AED/ZAR; corridors = trade, intercompany, other; direction = inbound/outbound.
- Trade finance: instruments = letters_of_credit, export_collections, guarantees; direction = import/export; statuses = issued, active, settled, expired; tenors are restricted to 30, 60, 90, 120, 180, 270 and 365 days.
- Both country fields contain the same 34 nonblank countries, plus blank values. Country values are counterparty locations, not necessarily client operating geographies.

Raw monetary totals and distribution:

| Dataset | Raw total | Median | 99th percentile* | Maximum |
|---|---:|---:|---:|---:|
| Transactional | R405.44bn | R55.0k | R1.49m | R64.83m |
| Cross-border | R133.75bn | R259.9k | R4.48m | R92.97m |
| Trade finance | R38.48bn | R947.2k | R14.55m | R138.16m |

\*Quantiles are based on a 100,000-row reservoir sample for the two larger files; the trade-finance distribution is full-file.

The amount distributions are highly right-skewed. Sample-based Tukey thresholds identify roughly 9–10% of rows as high-value candidates; these are not automatically errors in corporate banking. Transactional amounts have more than two decimal places in 116,261 rows (4.15%); cross-border and trade-finance amounts do not.

Duplicate findings are material:

| Dataset | Reused ID groups | Extra rows from reused IDs | Exact duplicate extra rows | Value in exact duplicate rows |
|---|---:|---:|---:|---:|
| Transactional | 52,984 | 53,793 | 10,812 | R1.569bn |
| Cross-border | 1,218 | 1,223 | 926 | R511.1m |
| Trade finance | 91 | 91 | 88 | R170.7m |

Many repeated IDs are not merely duplicated rows: 42,535 transactional ID groups, 297 cross-border groups, and 3 trade-finance groups contain different record payloads under the same ID. Those cannot be safely deduplicated without a source-system rule.

## B. Key discoveries

- All 20 clients are represented in all three product datasets, so absence of a client is not a coverage issue. Activity levels, however, vary substantially by client and product.
- Raw transactional volume is dominated by intercompany sweeps (R201.09bn), then collections (R138.62bn) and supplier payments (R65.04bn). This is service-use volume, not banking revenue.
- Cross-border volume is split almost evenly between trade corridors (R60.33bn) and intercompany corridors (R60.16bn). The five currency pairs are also remarkably balanced by volume.
- Trade finance is a portfolio of instruments, not a cash-flow ledger: letters of credit account for R15.15bn, export collections R12.77bn and guarantees R10.55bn. Active, issued, settled and expired instruments should not be aggregated as if they were equivalent cash flows.
- Transactional SWIFT activity and cross-border payments may overlap conceptually, but cannot be reconciled from supplied fields. There are no exact matches on entity, date, direction, amount, beneficiary and reference. They do share 29,107 entity-date-direction cells, so adding their values without product lineage is unsafe.
- The brief mentions 50 clients in its data description, while the supplied data contains 20. It also refers to trading data and mapped public-financial-statement inputs; neither is present in `/data`.