# Repository Audit — Standard Bank Hackathon (Syn Bank Share of Wallet)

Audit date: 2026-08-14. Branch `master`, HEAD `8ca150c`. Read-only audit; the pipeline was
not executed. Every number below was re-derived from the data files and code with DuckDB
1.5.5 / Python 3.14 (`.venv`), independently of any repo document. Where a repo document
states a different number, both are shown.

All monetary figures are South African Rand (ZAR) unless a different currency is named.
"bn" = 10^9, "m" = 10^6.

**Revision 2026-08-15.** Two changes since the original audit:

1. **`data/finances/entities.csv` was updated** — E11 Pepkor Holdings, E12 Clicks Group and
   E13 NEPI Rockcastle now carry a `fiscal_year_end`, and `fye_basis` is no longer `UNVERIFIED`
   for any entity. All 20 entities are now dated. Every affected finding below has been
   re-measured, and one **new** defect appeared as a direct consequence — see Blocker 8.
2. **Source and URL provenance is out of scope by team decision.** Sections 4.7 and 6.2 and
   Blocker 10 originally graded values by whether they carried a source document or URL. Those
   measurements are retained as description, but they are **no longer treated as defects** and
   must not gate any downstream work. Findings that concern the *value itself* rather than its
   citation (unverifiable zeros, failed identities) are unchanged and still stand.

---

## SECTION 1: REPO MAP

### 1.1 Directory tree (depth 3, excluding `.git`, `__pycache__`, `.venv`, `node_modules`, `.pytest_cache`)

```
.
├── .gitignore
├── README.md
├── brief.md
├── data_analysis.md
├── requirements.txt
├── AUDIT_REPORT.md            (this file, created by this audit)
├── .DS_Store
├── data/
│   ├── .DS_Store
│   ├── data.tgz
│   ├── cross_border_payments.csv
│   ├── trade_finance.csv
│   ├── transactional_banking.csv
│   ├── finances/
│   │   ├── data_dictionary.csv
│   │   ├── data_quality_exceptions.csv
│   │   ├── entities.csv
│   │   ├── external_financials_normalized.csv
│   │   ├── external_financials_wide.csv
│   │   ├── fx_rate_crosscheck.csv
│   │   ├── fx_rates_fy_window.csv
│   │   ├── fx_rates_normalized.csv
│   │   └── fx_rates_sarb_daily.csv
│   └── processed/
│       ├── cross_border_payments.parquet
│       ├── quality_report.json
│       ├── trade_finance.parquet
│       └── transactional_banking.parquet
├── src/
│   ├── __init__.py
│   └── syn_wallet/
│       ├── __init__.py
│       └── clean_data.py
└── tests/
    └── test_clean_data.py
```

There are no subdirectories at depth 4 other than `__pycache__` (excluded).

### 1.2 Data file inventory

Row counts exclude the header row. Column counts are physical columns.

| Path | Size (bytes) | Rows | Cols | Last modified (local) |
|---|---:|---:|---:|---|
| data/transactional_banking.csv | 392,854,065 | 2,802,875 | 13 | 2026-08-04 20:21:24 |
| data/cross_border_payments.csv | 33,003,150 | 241,117 | 13 | 2026-08-04 20:21:24 |
| data/trade_finance.csv | 3,245,811 | 20,303 | 15 | 2026-08-04 20:21:24 |
| data/data.tgz | 68,064,267 | n/a (gzip tar) | n/a | 2026-08-09 19:42:34 |
| data/processed/transactional_banking.parquet | 60,054,751 | 2,791,803 | 14 | 2026-08-10 22:50:42 |
| data/processed/cross_border_payments.parquet | 5,530,660 | 240,191 | 14 | 2026-08-10 22:50:43 |
| data/processed/trade_finance.parquet | 487,770 | 20,215 | 16 | 2026-08-10 22:50:43 |
| data/processed/quality_report.json | 4,466 | n/a (JSON object) | n/a | 2026-08-10 22:50:43 |
| data/finances/external_financials_normalized.csv | 98,449 | 380 | 17 | 2026-08-14 21:11:04 |
| data/finances/external_financials_wide.csv | 4,475 | 20 | 21 | 2026-08-14 21:11:04 |
| data/finances/entities.csv | 1,613 | 20 | 6 | 2026-08-14 21:11:04 |
| data/finances/data_dictionary.csv | 1,970 | 21 | 4 | 2026-08-14 21:11:04 |
| data/finances/data_quality_exceptions.csv | 33,851 | 144 | 8 | 2026-08-14 21:11:04 |
| data/finances/fx_rates_sarb_daily.csv | 62,051 | 2,709 | 3 | 2026-08-14 21:11:04 |
| data/finances/fx_rates_fy_window.csv | 4,302 | 60 | 10 | 2026-08-14 21:11:04 |
| data/finances/fx_rates_normalized.csv | 5,638 | 50 | 9 | 2026-08-14 21:11:04 |
| data/finances/fx_rate_crosscheck.csv | 887 | 17 | 7 | 2026-08-14 21:11:04 |

The parquet files carry one extra column versus their CSV source: `has_identifier_conflict`
(BOOLEAN).

### 1.3 Git tracking flags

- **Tracked and present on disk:** all nine `data/finances/*.csv`, `data/data.tgz`, the four
  Python files, `README.md`, `brief.md`, `data_analysis.md`, `requirements.txt`, `.gitignore`.
- **Tracked but absent from disk:** none. `git ls-files` returns 18 paths and all 18 exist.
- **On disk but gitignored:** `data/transactional_banking.csv`, `data/cross_border_payments.csv`,
  `data/trade_finance.csv` (named individually in `.gitignore`), the whole of `data/processed/`
  (4 files), `.venv/`, `__pycache__/`, `.pytest_cache/`.
- `.DS_Store` and `data/.DS_Store` exist on disk, are not in `.gitignore`, and are not
  tracked; `git status` is clean, so they are being suppressed by a global/`core.excludesFile`
  ignore rule outside the repo. UNVERIFIED: the exact global ignore file was not inspected.
- **Consequence:** the three raw CSVs — the only inputs the pipeline reads — are not in version
  control. `data/data.tgz` (68.06 MB) is tracked and is the plausible archive of them.
  UNVERIFIED: the contents of `data.tgz` were not extracted or listed, so it is not confirmed
  that the archive reproduces the three CSVs byte-for-byte.

### 1.4 Python modules

| Module | What it actually does |
|---|---|
| [src/\_\_init\_\_.py](src/__init__.py) | One docstring line, `"""Project source package."""`. No code. |
| [src/syn_wallet/\_\_init\_\_.py](src/syn_wallet/__init__.py) | One docstring line, `"""Syn Bank Share of Wallet data utilities."""`. No code, no re-exports. |
| [src/syn_wallet/clean_data.py](src/syn_wallet/clean_data.py) | The entire pipeline. Reads the three raw CSVs with DuckDB `read_csv_auto(all_varchar=true, nullstr='', strict_mode=true)`, asserts the column list matches a hardcoded tuple, casts `date`→DATE, the amount column→DECIMAL(30,16), `tenor_days`→INTEGER, uppercases `currency`, drops exact duplicate rows across all business columns, adds a `has_identifier_conflict` flag for rows whose ID appears more than once after dedup, runs reconciliation assertions (no required nulls, `EXCEPT` both directions, no residual exact duplicates), writes ZSTD Parquet plus `quality_report.json`. Has `main()` + `__main__` guard. |
| [tests/test_clean_data.py](tests/test_clean_data.py) | Two pytest tests. One builds 5-row synthetic CSVs per spec in `tmp_path` and asserts 5→3 rows, 2 duplicates removed, 1 conflict group, and that `currency` is all `ZAR` in the output. The second runs `run_pipeline` over the real `data/` directory into `tmp_path` and asserts the exact source/clean/removed row counts (2,802,875 / 2,791,803 / 11,072; 241,117 / 240,191 / 926; 20,303 / 20,215 / 88). |

**Dead code:** none of the four modules is dead. `clean_data.py` is imported by the test and has
a `__main__` entry point; both `__init__.py` files are package markers required for the
`src.syn_wallet.clean_data` import path used by the test and by `python -m`. There is no
notebook, no dashboard, no modelling code, and no module that reads `data/finances/` — see
Section 6.

---

## SECTION 2: CONFIG AND CONTRACTS

### 2.1 Complete inventory of config-shaped files

A filesystem-wide search for `*.yaml`, `*.yml`, `*.toml`, `*.json`, `*.cfg`, `*.ini`
(excluding `.git`, `.venv`, `.pytest_cache`) returns exactly two files:

| Path | Nature |
|---|---|
| `.venv/pyvenv.cfg` | Virtualenv metadata, not project config. Keys: `home`, `include-system-site-packages`, `version`, `executable`, `command`. |
| `data/processed/quality_report.json` | **Pipeline output, not input.** Written by `run_pipeline`. |

`data/processed/quality_report.json` key structure:

```
generated_at_utc            : string (ISO-8601 UTC)
policy.exact_duplicate_policy      : string
policy.identifier_conflict_policy  : string
policy.normalisations              : array[string]
policy.missing_value_policy        : string
policy.amount_policy               : string
datasets.<name>.source_rows                                  : int
datasets.<name>.source_exact_duplicate_rows                  : int
datasets.<name>.canonical_exact_duplicate_rows_removed       : int
datasets.<name>.clean_rows                                   : int
datasets.<name>.unique_identifiers_after_cleaning            : int
datasets.<name>.identifier_conflict_groups_retained          : int
datasets.<name>.identifier_conflict_rows_retained            : int
datasets.<name>.source_amount_total_after_type_normalisation : string (decimal)
datasets.<name>.clean_amount_total                           : string (decimal)
datasets.<name>.missing_values_source  : object{column: int}
datasets.<name>.missing_values_clean   : object{column: int}
datasets.<name>.normalisation_counts   : object{name: int}
  where <name> ∈ {transactional_banking, cross_border_payments, trade_finance}
```

`requirements.txt` (2 lines): `duckdb>=1.4.4,<2.0`, `pytest>=8.0,<9.0`. Installed DuckDB is
1.5.5. No pandas, no pyarrow, no plotting or modelling libraries.

### 2.2 Rates / economics config

**There is no rates or economics config file in this repository.** No YAML, TOML, JSON or
`.py` file defines fee rates, revenue margins, product pricing, wallet coefficients, or any
economic assumption. Nothing to list, and therefore no configured-but-unconsumed keys of that
kind.

The closest thing to rate *data* is `data/finances/fx_rates_sarb_daily.csv`,
`fx_rates_normalized.csv`, `fx_rates_fy_window.csv` and `fx_rate_crosscheck.csv`. These are
data files, not config, and **no code path in the repository reads any of them** (see 2.4).

### 2.3 Gap-policy config

**There is no gap-policy config file.** The absence/gap vocabulary (`OK`, `NOT_DISCLOSED`,
`NOT_APPLICABLE`, `NOT_FOUND`, `NOT_EXTRACTED`) exists only as literal string values inside
`data/finances/external_financials_normalized.csv` and `fx_rates_normalized.csv`. No code
declares, validates, or consumes that vocabulary.

The only policy statements that exist anywhere are the five free-text strings under `policy.*`
in `quality_report.json` — and these are **hardcoded in code** at
[clean_data.py:269-275](src/syn_wallet/clean_data.py#L269-L275), then written out. They are
descriptive prose emitted alongside the output; nothing reads them back.

### 2.4 Keys consumed by nothing / constants that duplicate a config

- **Consumed by nothing:** every field of `quality_report.json`. No module reads the file back;
  `run_pipeline` only writes it, and its existence is used purely as an overwrite guard at
  [clean_data.py:247-251](src/syn_wallet/clean_data.py#L247-L251).
- **Consumed by nothing:** all nine `data/finances/*.csv` files. `grep` across `src/` and
  `tests/` for `finances`, `entities`, `fx`, `external_financials` returns zero hits. The
  finances directory is currently orphaned data.
- **Hardcoded constants that act as an unversioned contract:** the three column tuples
  `TRANSACTIONAL_COLUMNS`, `CROSS_BORDER_COLUMNS`, `TRADE_COLUMNS` at
  [clean_data.py:37-52](src/syn_wallet/clean_data.py#L37-L52), and the `SPECS` tuple at
  [clean_data.py:54-70](src/syn_wallet/clean_data.py#L54-L70) (filenames, identifier column,
  amount column, required-column sets). A schema change in the raw CSVs raises `AssertionError`
  at [clean_data.py:110-113](src/syn_wallet/clean_data.py#L110-L113).
- **Hardcoded numeric constant:** `DECIMAL(30, 16)` at
  [clean_data.py:97](src/syn_wallet/clean_data.py#L97), mirrored in the prose string
  `"Use DECIMAL(30,16); do not round source amounts."` at
  [clean_data.py:274](src/syn_wallet/clean_data.py#L274). These two duplicate each other; a
  change to one will not update the other. They currently agree.
- **Hardcoded expected row counts duplicated in the test:**
  [tests/test_clean_data.py:90-94](tests/test_clean_data.py#L90-L94) hardcodes
  `(2_802_875, 2_791_803, 11_072)`, `(241_117, 240_191, 926)`, `(20_303, 20_215, 88)`. These
  agree with the current `quality_report.json`. Because the raw CSVs are gitignored, this test
  is the only version-controlled record of the expected input shape.
- **No contradiction found** between any config value and any hardcoded constant.

---

## SECTION 3: PIPELINE STATE

### 3.1 Actual execution order

There is exactly one stage. `run_pipeline(input_dir, output_dir, overwrite)`
([clean_data.py:242](src/syn_wallet/clean_data.py#L242)) loops over the three `SPECS` and calls
`clean_dataset` for each; inside `clean_dataset`
([clean_data.py:206](src/syn_wallet/clean_data.py#L206)) the order is:

1. `_assert_schema` — DESCRIBE the CSV, compare column tuple ([L210](src/syn_wallet/clean_data.py#L210)).
2. `CREATE TEMP TABLE raw` — all columns as VARCHAR, `''`→NULL ([L212](src/syn_wallet/clean_data.py#L212)).
3. `CREATE TEMP TABLE canonical` — typed casts **and** `UPPER(currency)` ([L213-215](src/syn_wallet/clean_data.py#L213-L215), expressions built at [L89-105](src/syn_wallet/clean_data.py#L89-L105)).
4. `CREATE TEMP TABLE deduplicated` — `ROW_NUMBER() OVER (PARTITION BY <all business columns>)`, keep rank 1 ([L216-224](src/syn_wallet/clean_data.py#L216-L224)).
5. `CREATE TEMP TABLE cleaned` — add `has_identifier_conflict` ([L225-230](src/syn_wallet/clean_data.py#L225-L230)).
6. `_validate_dataset` — assertions ([L231](src/syn_wallet/clean_data.py#L231)).
7. `_dataset_report`, then `COPY cleaned TO <parquet>` ([L232-236](src/syn_wallet/clean_data.py#L232-L236)).
8. After the loop, write `quality_report.json` ([L278](src/syn_wallet/clean_data.py#L278)).

### 3.2 Stage freshness

| Stage | Inputs | Outputs | Output exists | Newer than inputs | Newer than code |
|---|---|---|---|---|---|
| clean_data (transactional) | data/transactional_banking.csv (2026-08-04 20:21:24) | data/processed/transactional_banking.parquet (2026-08-10 22:50:42) | yes | yes | yes (code 2026-08-10 22:50:28) |
| clean_data (cross-border) | data/cross_border_payments.csv (2026-08-04 20:21:24) | data/processed/cross_border_payments.parquet (2026-08-10 22:50:43) | yes | yes | yes |
| clean_data (trade finance) | data/trade_finance.csv (2026-08-04 20:21:24) | data/processed/trade_finance.parquet (2026-08-10 22:50:43) | yes | yes | yes |
| report | all three | data/processed/quality_report.json (2026-08-10 22:50:43) | yes | yes | yes |
| *(none)* | data/finances/*.csv (2026-08-14 21:11:04) | — | — | — | — |

The processed outputs are current with respect to both their inputs and `clean_data.py`. The
`data/finances/` files were written four days after the last pipeline run and feed no stage.

### 3.3 Where the repo is sitting

The repo is sitting **immediately after the single cleaning stage, with no second stage
written.** Cleaned Parquet exists for all three internal flow datasets; the external-financials
directory has been populated by hand/agent extraction but is not wired into anything. There is
no joining stage, no wallet-sizing stage, no FX-conversion stage, no metric stage, no notebook,
and no dashboard.

**What would break if the next stage ran today**, given that the next stage must join internal
flows to external financials:

1. Any join keyed on FX-converted values would have nothing to convert with — no code reads
   `fx_rates_*.csv`, and the external financials are stored in **native reporting currency**,
   not ZAR (Section 4.9). Seven of 20 entities report in USD, one in EUR, one in GBP. A naive
   join would compare ZAR flow values against USD/EUR/GBP denominators and produce silently
   wrong ratios off by roughly 17-24x for those nine entities.
2. The internal flow window is 2023-07-01 to 2026-06-30 (per `data_analysis.md`; UNVERIFIED —
   the date range was not independently re-measured in this audit). Entity fiscal year-ends
   span 2025-06-30 to 2026-03-31 across five distinct dates. Aligning a fixed 12-month flow
   window to 20 different reporting periods requires a decision that no code currently encodes.
3. Any denominator built from `revenue_south_africa` would be absent for 10 of 20 entities and
   zero-valued for one (E13, a genuine zero), so a share-of-wallet ratio would divide by null or
   by zero for 11 of 20 clients.

### 3.4 Does deduplication execute before currency normalisation?

**Refuted.** Deduplication executes **after** currency normalisation, not before.

The chain is `raw` → `canonical` → `deduplicated`. `UPPER(currency)` is applied when `canonical`
is built at [clean_data.py:213-215](src/syn_wallet/clean_data.py#L213-L215), via the branch at
[clean_data.py:100-101](src/syn_wallet/clean_data.py#L100-L101):

```python
elif column == "currency":
    expression = f"UPPER({quoted}) AS {quoted}"
```

The `ROW_NUMBER() OVER (PARTITION BY ...)` dedup is only built at
[clean_data.py:216-224](src/syn_wallet/clean_data.py#L216-L224), selecting `FROM canonical`.
So the sequence is normalise-then-dedup.

Two clarifications that matter for reading this result:

- The only "currency normalisation" that exists is **case-folding of the `currency` code
  string**. There is no FX rate application anywhere in the codebase, so no monetary value is
  ever re-denominated.
- Because the code normalises first, it catches 260 additional duplicate rows that a
  dedup-first ordering would miss (Section 5.4). Normalise-then-dedup is the ordering that
  removes *more* duplicates here, not fewer.

The same is true of the DECIMAL cast: `TRY_CAST(amount_zar AS DECIMAL(30,16))` also happens in
`canonical`, before dedup, so two source rows differing only as `100.10` vs `100.1000` collapse.

---

## SECTION 4: data/finances

Nine CSV files, all last modified 2026-08-14 21:11:04, all UTF-8, all comma-delimited with a
header row.

### 4.1 File-by-file description

---

**`entities.csv`** — 20 rows, 6 columns. The entity master: one row per client, giving
reporting currency and fiscal year end.

| Column | dtype (inferred) |
|---|---|
| entity_id | VARCHAR |
| entity_name | VARCHAR |
| fy_label | VARCHAR |
| reporting_currency | VARCHAR |
| fiscal_year_end | DATE |
| fye_basis | VARCHAR |

```
E01 | BHP Group        | FY2025 | USD | 2025-06-30 | cited in AFS note text or source_doc title
E11 | Pepkor Holdings  | FY2025 | ZAR | 2025-09-30 | cited in AFS note text or source_doc title
```

As of the 2026-08-15 revision all 20 rows carry a `fiscal_year_end` and all 20 carry
`fye_basis = "cited in AFS note text or source_doc title"`. No blanks, no `UNVERIFIED`.

---

**`external_financials_normalized.csv`** — 380 rows, 17 columns. The primary long-format store:
one row per (entity × field), 20 entities × 19 fields.

| Column | dtype | Column | dtype |
|---|---|---|---|
| entity_id | VARCHAR | gap_reason | VARCHAR |
| entity_name | VARCHAR | source_reliability | VARCHAR |
| fy_label | VARCHAR | source_doc | VARCHAR |
| field | VARCHAR | source_ref | VARCHAR |
| unit_type | VARCHAR | source_url | VARCHAR |
| reporting_currency | VARCHAR | extraction_note | VARCHAR |
| value_numeric | DOUBLE | | |
| value_text | VARCHAR | | |
| status | VARCHAR | | |
| basis | VARCHAR | | |
| bound_recoverable | BIGINT | | |

```
E01 | BHP Group | FY2025 | revenue_total | currency | USD | 51262000000.0 | (blank) | OK
     | as_reported | 0 | (blank) | AFS | 20-F/Annual Report 2025 | OFR 5.3 Financial results
     | https://www.sec.gov/Archives/edgar/data/811809/000119312525183071/d35528d6k.htm | (blank)

E01 | BHP Group | FY2025 | revenue_south_africa | currency | USD | (blank) | Not disclosed
     | NOT_DISCLOSED | as_reported | 0
     | BHP is ASX/LSE primary listed (JSE secondary only); no SA segment revenue disclosed
     | AFS | (blank) | (blank) | (blank) | SA or domestic segment revenue, segmental note
```

---

**`external_financials_wide.csv`** — 20 rows, 21 columns. Wide projection of the numeric subset
of the normalized file: 4 key columns + 17 value columns. All value columns DOUBLE.

Columns: entity_id, entity_name, fy_label, reporting_currency (all VARCHAR), then capex,
cash_and_equivalents, committed_facilities_total, cost_of_sales, debt_current, debt_noncurrent,
employees, finance_costs, fx_forward_notional, gross_debt, inventory, revenue_foreign,
revenue_south_africa, revenue_total, trade_payables, trade_receivables, undrawn_facilities
(all DOUBLE).

```
E01 | BHP Group | FY2025 | USD | capex 9794000000.0 | cash 11894000000.0 | ... | revenue_total 51262000000.0
E02 | Glencore  | FY2025 | USD | capex 6892000000.0 | cash  2945000000.0 | ... | revenue_total 247535000000.0
```

The two text-typed fields present in the normalized file (`debt_maturity_note_page`,
`lenders_named`) are **not** carried into the wide file, hence 17 value columns and not 19.

---

**`data_dictionary.csv`** — 21 rows, 4 columns (field, definition, unit_type, table), all
VARCHAR. Defines 19 `external_financials` fields plus 2 `fx_rates` fields (`avg_zar_rate`,
`closing_zar_rate`).

```
revenue_total        | Total revenue / turnover, income statement       | currency | external_financials
revenue_south_africa | SA / domestic segment revenue, segmental note    | currency | external_financials
```

---

**`data_quality_exceptions.csv`** — 144 rows, 8 columns
(entity_id, fy_label, field, column, rule, value_before, value_after, note), all VARCHAR.
The audit log of corrections applied during extraction.

```
*   | *      | *            | (row)              | DROP_BLANK_ROW        | 420 rows | 420 rows | 0 fully-empty spacer rows removed
E06 | FY2025 | revenue_total| source_doc/url/page| FIX_SOURCE_COL_SHIFT  | True | Claude (web search/fetch) | 2026-12-08 00:00:00 | (blank) | Source columns held verified/extracted_by/verified_date content. Cleared.
```

---

**`fx_rates_normalized.csv`** — 50 rows, 9 columns
(entity_id, entity_name, fy_label, rate_type, foreign_currency, zar_per_unit DOUBLE, status,
needs_review DOUBLE, source_text). Self-reported FX rates lifted from each company's own AFS.

```
E01 | BHP Group | FY2025 | average | (blank) | (blank) | NOT_DISCLOSED | 0.0 | Not disclosed
E02 | Glencore  | FY2025 | average | USD     | 17.88   | OK            | (blank) | ...
```

---

**`fx_rates_fy_window.csv`** — 60 rows, 10 columns
(entity_id, entity_name, fy_label, foreign_currency, fy_start DATE, fy_end DATE, avg_rate DOUBLE,
closing_rate DOUBLE, n_obs BIGINT, status). SARB-derived rates computed over each entity's own
fiscal-year window, for USD/GBP/EUR × 20 entities.

```
E01 | BHP Group | FY2025 | USD | 2024-07-01 | 2025-06-30 | 18.1569 | 17.7758 | 249 | OK
E11 | Pepkor    | FY2025 | USD | (blank)    | (blank)    | (blank) | (blank) |   0 | BLOCKED_NO_FYE
```

**This file is now stale.** The nine `BLOCKED_NO_FYE` rows still block on a missing
`fiscal_year_end` that `entities.csv` now supplies. See Blocker 8.

---

**`fx_rate_crosscheck.csv`** — 17 rows, 7 columns
(entity_id, entity_name, rate_type, self_reported DOUBLE, sarb DOUBLE, pct_diff DOUBLE, flag).
Compares self-reported rates to SARB rates. All 17 rows carry `flag = ok`; the largest absolute
`pct_diff` is -0.85% (Prosus closing).

```
E02 | Glencore | average | 17.88 | 17.8829 | -0.02 | ok
E02 | Glencore | closing | 16.56 | 16.598  | -0.23 | ok
```

---

**`fx_rates_sarb_daily.csv`** — 2,709 rows, 3 columns (date DATE, foreign_currency VARCHAR,
zar_per_unit DOUBLE). Daily SARB reference rates, exactly 903 observations each for EUR, GBP and
USD, spanning 2023-01-03 to 2026-08-14.

```
2023-01-03 | EUR | 18.0113
2023-01-04 | EUR | 17.8745
```

### 4.2 Long/tidy vs wide layout

**Mixed, deliberately.**

- **Long/tidy (entity, field, value):** `external_financials_normalized.csv` (the canonical
  store), `data_quality_exceptions.csv`, `fx_rates_normalized.csv`, `fx_rate_crosscheck.csv`,
  `fx_rates_sarb_daily.csv`.
- **Wide (one column per field):** `external_financials_wide.csv`,
  `fx_rates_fy_window.csv` (semi-wide: `avg_rate` and `closing_rate` are two columns of a
  currency-keyed long table).
- **Neither (reference/master tables):** `entities.csv`, `data_dictionary.csv`.

The long and wide financial files reconcile perfectly: unpivoting all 17 value columns of
`external_financials_wide.csv` and full-outer-joining to `external_financials_normalized.csv` on
(entity_id, field) yields **zero value discrepancies** across all 340 numeric cells. The wide
file is a faithful derived projection.

### 4.3 Entity coverage and name reconciliation

All 20 entity IDs and all 20 entity names in `data/finances/` match the 20 in the internal flow
datasets. A full-outer join of `SELECT DISTINCT entity_name` from
`external_financials_normalized.csv` against `SELECT DISTINCT entity_name` from
`data/processed/transactional_banking.parquet` returns **zero unmatched rows on either side**.

| entity_id | entity_name (identical in both) | sector (flows) |
|---|---|---|
| E01 | BHP Group | mining |
| E02 | Glencore | mining |
| E03 | Anglo American | mining |
| E04 | AngloGold Ashanti | mining |
| E05 | Gold Fields | mining |
| E06 | Valterra Platinum | mining |
| E07 | OUTsurance Group | insurance |
| E08 | Sanlam | insurance |
| E09 | Shoprite Holdings | consumer |
| E10 | Bid Corporation | consumer |
| E11 | Pepkor Holdings | consumer |
| E12 | Clicks Group | consumer |
| E13 | NEPI Rockcastle | real_estate |
| E14 | Prosus | tech |
| E15 | Naspers | tech |
| E16 | MTN Group | telecoms |
| E17 | Vodacom Group | telecoms |
| E18 | The Bidvest Group | industrials_pharma |
| E19 | Aspen Pharmacare | industrials_pharma |
| E20 | Shaftesbury Capital plc | real_estate |

**Name mismatches found: none.** No trailing whitespace, no "Ltd"/"Limited" divergence, no
ticker-vs-name substitution, no case difference. `entity_name` in `entities.csv`,
`external_financials_normalized.csv` and `external_financials_wide.csv` are also mutually
identical.

Note for downstream use: the names are the short trading names, not registered legal names
("Glencore" not "Glencore plc"; "The Bidvest Group" not "The Bidvest Group Limited";
"Shaftesbury Capital plc" is the one name carrying a legal suffix). Any future join to an
external register keyed on legal name will need a mapping table that does not yet exist.

### 4.4 Field coverage matrix

19 fields × 20 entities = 380 cells. "Populated" = `status = 'OK'`; "absent" = any other status.

**By field (worst coverage first):**

| Field | Populated (OK) | Absent | Numeric value present |
|---|---:|---:|---:|
| lenders_named | 4 | 16 | 0 (text field) |
| fx_forward_notional | 6 | 14 | 6 |
| revenue_foreign | 9 | 11 | 9 |
| revenue_south_africa | 10 | 10 | 10 |
| debt_maturity_note_page | 11 | 9 | 0 (text field) |
| committed_facilities_total | 12 | 8 | 12 |
| cost_of_sales | 15 | 5 | 15 |
| employees | 15 | 5 | 15 |
| undrawn_facilities | 15 | 5 | 15 |
| inventory | 17 | 3 | 17 |
| capex | 20 | 0 | 20 |
| cash_and_equivalents | 20 | 0 | 20 |
| debt_current | 20 | 0 | 20 |
| debt_noncurrent | 20 | 0 | 20 |
| finance_costs | 20 | 0 | 20 |
| gross_debt | 20 | 0 | 20 |
| revenue_total | 20 | 0 | 20 |
| trade_payables | 20 | 0 | 20 |
| trade_receivables | 20 | 0 | 20 |
| **Total** | **294** | **86** | **294 rows OK, of which 279 numeric** |

**Worst-covered fields:** `lenders_named` (4/20 = 20.0%), `fx_forward_notional` (6/20 = 30.0%),
`revenue_foreign` (9/20 = 45.0%), `revenue_south_africa` (10/20 = 50.0%),
`debt_maturity_note_page` (11/20 = 55.0%).

**By entity:**

| entity_id | Entity | OK | Absent | Absent fields |
|---|---|---:|---:|---|
| E01 | BHP Group | 15 | 4 | cost_of_sales, lenders_named, revenue_foreign, revenue_south_africa |
| E02 | Glencore | 15 | 4 | fx_forward_notional, lenders_named, revenue_foreign, revenue_south_africa |
| E03 | Anglo American | 14 | 5 | cost_of_sales, fx_forward_notional, lenders_named, revenue_foreign, revenue_south_africa |
| E04 | AngloGold Ashanti | 15 | 4 | fx_forward_notional, lenders_named, revenue_foreign, revenue_south_africa |
| E05 | Gold Fields | 13 | 6 | committed_facilities_total, debt_maturity_note_page, lenders_named, revenue_foreign, revenue_south_africa, undrawn_facilities |
| E06 | Valterra Platinum | 17 | 2 | fx_forward_notional, lenders_named |
| E07 | OUTsurance Group | 14 | 5 | cost_of_sales, debt_maturity_note_page, employees, inventory, lenders_named |
| E08 | Sanlam | 17 | 2 | fx_forward_notional, inventory |
| E09 | Shoprite Holdings | 14 | 5 | committed_facilities_total, debt_maturity_note_page, fx_forward_notional, lenders_named, undrawn_facilities |
| E10 | Bid Corporation | 15 | 4 | committed_facilities_total, debt_maturity_note_page, fx_forward_notional, revenue_foreign |
| E11 | Pepkor Holdings | 17 | 2 | employees, lenders_named |
| E12 | Clicks Group | 14 | 5 | committed_facilities_total, lenders_named, revenue_foreign, revenue_south_africa, undrawn_facilities |
| E13 | NEPI Rockcastle | 18 | 1 | fx_forward_notional |
| E14 | Prosus | 15 | 4 | fx_forward_notional, lenders_named, revenue_foreign, revenue_south_africa |
| E15 | Naspers | 12 | 7 | committed_facilities_total, employees, fx_forward_notional, lenders_named, revenue_foreign, revenue_south_africa, undrawn_facilities |
| E16 | MTN Group | 14 | 5 | committed_facilities_total, cost_of_sales, debt_maturity_note_page, employees, fx_forward_notional |
| E17 | Vodacom Group | 13 | 6 | committed_facilities_total, cost_of_sales, debt_maturity_note_page, employees, fx_forward_notional, lenders_named |
| E18 | The Bidvest Group | 14 | 5 | committed_facilities_total, debt_maturity_note_page, fx_forward_notional, lenders_named, undrawn_facilities |
| E19 | Aspen Pharmacare | 15 | 4 | debt_maturity_note_page, lenders_named, revenue_foreign, revenue_south_africa |
| E20 | Shaftesbury Capital plc | 13 | 6 | debt_maturity_note_page, fx_forward_notional, inventory, lenders_named, revenue_foreign, revenue_south_africa |

Worst-covered entities: E15 Naspers (12/19 = 63.2%), E05 Gold Fields, E17 Vodacom Group and
E20 Shaftesbury Capital plc (13/19 = 68.4% each).

### 4.5 Absence coding

**`external_financials_normalized.csv` — `status` column, all 380 rows:**

| Value | Count |
|---|---:|
| OK | 294 |
| NOT_DISCLOSED | 83 |
| NOT_APPLICABLE | 1 |
| NOT_EXTRACTED | 1 |
| NOT_FOUND | 1 |

**Outside the closed vocabulary `{NOT_APPLICABLE, NOT_DISCLOSED, NOT_FOUND, NOT_COMPARABLE,
AFS_NOT_YET_AUDITED}`:**

- `NOT_EXTRACTED` — 1 occurrence: E08 Sanlam / `fx_forward_notional`. Not a member of the closed
  vocabulary.
- `OK` — 294 occurrences. This is the present-value marker rather than an absence marker, so it
  is not a violation, but it is worth recording that the column mixes a presence code with
  absence codes in a single field.
- `NOT_COMPARABLE` and `AFS_NOT_YET_AUDITED` never appear.

**`fx_rates_normalized.csv` — `status` column, all 50 rows:** `OK` 27, `NOT_DISCLOSED` 17,
`NOT_APPLICABLE` 6. No out-of-vocabulary values.

**`fx_rates_fy_window.csv` — `status` column, all 60 rows:** `OK` 51, `BLOCKED_NO_FYE` 9.
`BLOCKED_NO_FYE` is **outside the closed vocabulary** (E11 Pepkor, E12 Clicks, E13 NEPI
Rockcastle × USD/GBP/EUR). As of 2026-08-15 these 9 rows are **stale, not blocked** — the
fiscal year ends they wait on now exist in `entities.csv`. See Blocker 8.

**`fx_rate_crosscheck.csv` — `flag` column, all 17 rows:** `ok` 17 (lowercase). No absence
values.

**Blanks, NaNs, empty strings, "N/A", "-", 0 used as a missing marker** — a raw character-level
scan of every cell of every file in `data/finances/` for `''`, `'N/A'`, `'NA'`, `'NaN'`, `'-'`,
`'none'`, `'null'`, `'unknown'` (case-insensitive, after stripping) returns:

| File | Column | Value | Count |
|---|---|---|---:|
| data_dictionary.csv | — | — | 0 |
| fx_rates_sarb_daily.csv | — | — | 0 |
| fx_rate_crosscheck.csv | — | — | 0 |
| entities.csv | fiscal_year_end | `''` | 0 |
| data_quality_exceptions.csv | fy_label | `''` | 3 |
| data_quality_exceptions.csv | value_before | `''` | 6 |
| data_quality_exceptions.csv | value_after | `''` | 119 |
| external_financials_normalized.csv | value_numeric | `''` | 101 |
| external_financials_normalized.csv | value_text | `''` | 279 |
| external_financials_normalized.csv | reporting_currency | `''` | 61 |
| external_financials_normalized.csv | source_doc | `''` | 220 |
| external_financials_normalized.csv | source_ref | `''` | 223 |
| external_financials_normalized.csv | source_url | `''` | 332 |
| external_financials_normalized.csv | gap_reason | `''` | 161 |
| external_financials_normalized.csv | extraction_note | `''` | 174 |
| external_financials_normalized.csv | basis | `'unknown'` | 6 |
| external_financials_wide.csv | fx_forward_notional | `''` | 14 |
| external_financials_wide.csv | revenue_foreign | `''` | 11 |
| external_financials_wide.csv | revenue_south_africa | `''` | 10 |
| external_financials_wide.csv | committed_facilities_total | `''` | 8 |
| external_financials_wide.csv | cost_of_sales | `''` | 5 |
| external_financials_wide.csv | employees | `''` | 5 |
| external_financials_wide.csv | undrawn_facilities | `''` | 5 |
| external_financials_wide.csv | inventory | `''` | 3 |
| fx_rates_normalized.csv | foreign_currency | `''` | 23 |
| fx_rates_normalized.csv | zar_per_unit | `''` | 23 |
| fx_rates_normalized.csv | needs_review | `''` | 13 |
| fx_rates_normalized.csv | source_text | `''` | 13 |
| fx_rates_fy_window.csv | fy_start | `''` | 9 |
| fx_rates_fy_window.csv | fy_end | `''` | 9 |
| fx_rates_fy_window.csv | avg_rate | `''` | 9 |
| fx_rates_fy_window.csv | closing_rate | `''` | 9 |

No `'N/A'`, `'NA'`, `'NaN'`, `'-'`, `'none'` or `'null'` string was found anywhere in the
directory. Flags arising from the above:

1. **`external_financials_wide.csv` is the dangerous file.** It encodes all 86 absent cells as
   bare empty strings with **no status column at all**. A consumer loading it with pandas gets
   `NaN` and cannot distinguish NOT_DISCLOSED from NOT_APPLICABLE from NOT_FOUND, nor
   distinguish a real zero from a missing value once `fillna(0)` is applied anywhere downstream.
2. **`basis = 'unknown'`** (6 rows) is a lowercase free-text absence marker in a column that
   otherwise carries a controlled vocabulary (`as_reported` 345, `commentary` 11, `pro_forma` 9,
   `derived` 8, `unknown` 6, `constructed` 1).
3. **`reporting_currency` blank on a currency-typed row:** 60 of the 61 blanks are legitimate
   (20 × `employees` which is `unit_type = count`, 20 × `debt_maturity_note_page` and 20 ×
   `lenders_named` which are `unit_type = text`). The **61st is E01 BHP Group /
   `cost_of_sales`**, which is `unit_type = currency` with `status = NOT_DISCLOSED` — an absent
   cell that has also lost its currency tag.
4. **`value_text` blank on 279 rows** while `value_numeric` is blank on 101 rows: the two are
   complementary (279 + 101 = 380), so exactly one of the two is populated on every row. That is
   internally consistent.
5. **22 of the 83 `NOT_DISCLOSED` rows carry a blank `gap_reason`**, so the reason for absence is
   unrecoverable for those. The single `NOT_EXTRACTED` and the single `NOT_APPLICABLE` also have
   blank `gap_reason`; only the single `NOT_FOUND` row has one.
6. **No cell anywhere uses `0` as a missing marker** — see 4.6.

### 4.6 Genuine zeros

Ten rows in `external_financials_normalized.csv` have `value_numeric = 0`. All ten carry
`status = OK`, i.e. all ten are asserted as real zeros, not missing values. Source references:

| entity_id | Entity | Field | source_doc | source_ref | source_url | Verdict |
|---|---|---|---|---|---|---|
| E05 | Gold Fields | fx_forward_notional | *(blank)* | *(blank)* | *(blank)* | **DEFECT — zero with no source reference of any kind** |
| E06 | Valterra Platinum | debt_noncurrent | *(blank)* | *(blank)* | *(blank)* | **DEFECT — zero with no source reference of any kind** |
| E07 | OUTsurance Group | gross_debt | *(blank)* | *(blank)* | *(blank)* | **DEFECT — zero with no source reference of any kind** |
| E07 | OUTsurance Group | debt_current | *(blank)* | *(blank)* | *(blank)* | **DEFECT — zero with no source reference of any kind** |
| E07 | OUTsurance Group | debt_noncurrent | *(blank)* | *(blank)* | *(blank)* | **DEFECT — zero with no source reference of any kind** |
| E12 | Clicks Group | gross_debt | CGL-YE25-Annual-financial-statements.docx | "No bank/interest-bearing borrowings presented; lease liabilities excluded" | *(blank)* | Valid — document + locator present, no URL |
| E12 | Clicks Group | debt_current | CGL-YE25-Annual-financial-statements.docx | same as above | *(blank)* | Valid — document + locator present, no URL |
| E12 | Clicks Group | debt_noncurrent | CGL-YE25-Annual-financial-statements.docx | same as above | *(blank)* | Valid — document + locator present, no URL |
| E13 | NEPI Rockcastle | revenue_south_africa | NEPI_Rockcastle_Annual_Report_2025_compressed.pdf | "Company profile: operations in CEE" | *(blank)* | Valid — document + locator present, no URL |
| E13 | NEPI Rockcastle | inventory | NEPI_Rockcastle_Annual_Report_2025_compressed.pdf | "p. 310, inventory property" | *(blank)* | Valid — document + locator present, no URL |

**5 of 10 zeros are unsourced defects** (E05 × 1, E06 × 1, E07 × 3), all in debt / FX-notional
fields. The five OUTsurance and Valterra zeros are also the ones that most directly drive a
lending or hedging opportunity signal: a claimed "zero gross debt" that no document backs is
indistinguishable from an extraction failure.

Cross-check against `external_financials_wide.csv`: all ten zeros appear there as literal `0.0`,
where they are typographically indistinguishable from any other numeric value and carry no
status or source at all.

### 4.7 Provenance

> **Out of scope as of 2026-08-15.** The team has decided that source documents and URLs are not
> a quality gate for this project. Everything in this section is retained as **description of
> what the columns contain**, not as a defect list. Do not use it to exclude an entity, downgrade
> a value, or gate a metric. The `source_doc`, `source_ref`, `source_url` and
> `source_reliability` columns can be ignored by downstream code. What still matters is whether
> the *value* is right — see 4.6 (zeros) and 4.12 (identities), which are unaffected.

Denominator = the 294 rows with `status = 'OK'` (non-absent values).

| Measure | Count | % of 294 |
|---|---:|---:|
| Has a non-empty `source_url` | 46 | 15.6% |
| Has a non-empty `source_ref` (page/note locator) | 132 | 44.9% |
| Has **both** `source_url` and `source_ref` | 44 | 15.0% |
| Has neither | 160 | 54.4% |

**15.0% of non-absent values carry both a source URL and a source page/locator.**

`source_reliability` across all 380 rows: `AFS` 216, `UNSOURCED` 159, `AFS_URL_UNSUPPORTED` 3,
`NON_AFS` 2. So **159 of 380 rows (41.8%) are self-declared as unsourced.**

**Distinct source domains** (48 rows carry a URL; 46 of them are `status = OK`, 2 are not):

| Domain | Count | Primary? |
|---|---:|---|
| www.sec.gov | 16 | Primary (SEC EDGAR filing) |
| www.glencore.com | 15 | Primary (issuer IR site) |
| www.angloamerican.com | 5 | Primary (issuer IR site) |
| www.goldfields.com | 3 | Primary (issuer IR site) |
| www.bhp.com | 1 | Primary (issuer IR site) |
| www.shaftesburycapital.com | 1 | Primary (issuer IR site) |
| www.vodacom.com | 1 | Primary (issuer IR site) |
| mtn-investor.com | 1 | Primary (issuer IR site) |
| www.investegate.co.uk | 1 | **Non-primary — RNS aggregator** |
| finance.yahoo.com | 1 | **Non-primary — aggregator** |
| stockanalysis.com | 1 | **Non-primary — aggregator** |
| www.marketscreener.com | 1 | **Non-primary — aggregator** |
| www.tipranks.com | 1 | **Non-primary — aggregator** |

**Entities and fields depending on a non-primary or aggregator domain:**

| Entity | Field | Domain | source_reliability |
|---|---|---|---|
| E16 MTN Group | capex | finance.yahoo.com | AFS_URL_UNSUPPORTED |
| E17 Vodacom Group | **revenue_total** | www.marketscreener.com | AFS_URL_UNSUPPORTED |
| E18 The Bidvest Group | **revenue_total** | www.investegate.co.uk | AFS |
| E18 The Bidvest Group | employees | www.tipranks.com | NON_AFS |
| E19 Aspen Pharmacare | **revenue_total** | stockanalysis.com | AFS_URL_UNSUPPORTED |

Three of the five are `revenue_total` — the field most likely to be a wallet-sizing denominator.
One further row, E06 Valterra Platinum / `employees`, is marked `NON_AFS` with no URL at all, so
its source cannot be inspected.

`www.investegate.co.uk` is marked `AFS` in `source_reliability` despite being a third-party RNS
republisher rather than the issuer or a regulator; that classification is inconsistent with the
`AFS_URL_UNSUPPORTED` label applied to the other three aggregator rows.

### 4.8 Reporting periods

Every entity carries exactly one `fy_label`, in both `entities.csv` and
`external_financials_normalized.csv`, and the two agree for all 20 entities (a join on
entity_id filtering `n.fy_label <> e.fy_label` returns 0 rows).

| entity_id | Entity | fy_label | fiscal_year_end | fye_basis |
|---|---|---|---|---|
| E01 | BHP Group | FY2025 | 2025-06-30 | cited in AFS note text or source_doc title |
| E02 | Glencore | FY2025 | 2025-12-31 | cited in AFS note text or source_doc title |
| E03 | Anglo American | FY2025 | 2025-12-31 | cited in AFS note text or source_doc title |
| E04 | AngloGold Ashanti | FY2025 | 2025-12-31 | cited in AFS note text or source_doc title |
| E05 | Gold Fields | FY2025 | 2025-12-31 | cited in AFS note text or source_doc title |
| E06 | Valterra Platinum | FY2025 | 2025-12-31 | cited in AFS note text or source_doc title |
| E07 | OUTsurance Group | FY2025 | 2025-06-30 | cited in AFS note text or source_doc title |
| E08 | Sanlam | FY2025 | 2025-12-31 | cited in AFS note text or source_doc title |
| E09 | Shoprite Holdings | FY2025 | 2025-06-30 | cited in AFS note text or source_doc title |
| E10 | Bid Corporation | FY2025 | 2025-06-30 | cited in AFS note text or source_doc title |
| E11 | Pepkor Holdings | FY2025 | **2025-09-30** | cited in AFS note text or source_doc title |
| E12 | Clicks Group | FY2025 | **2025-08-31** | cited in AFS note text or source_doc title |
| E13 | NEPI Rockcastle | FY2025 | **2025-12-31** | cited in AFS note text or source_doc title |
| E14 | Prosus | FY2026 | 2026-03-31 | cited in AFS note text or source_doc title |
| E15 | Naspers | FY2026 | 2026-03-31 | cited in AFS note text or source_doc title |
| E16 | MTN Group | FY2025 | 2025-12-31 | cited in AFS note text or source_doc title |
| E17 | Vodacom Group | FY2026 | 2026-03-31 | cited in AFS note text or source_doc title |
| E18 | The Bidvest Group | FY2025 | 2025-06-30 | cited in AFS note text or source_doc title |
| E19 | Aspen Pharmacare | FY2025 | 2025-06-30 | cited in AFS note text or source_doc title |
| E20 | Shaftesbury Capital plc | FY2025 | 2025-12-31 | cited in AFS note text or source_doc title |

**Spread of financial year ends:** all 20 entities are dated, across **five distinct dates**:

| fiscal_year_end | Entities | Count |
|---|---|---:|
| 2025-06-30 | E01, E07, E09, E10, E18, E19 | 6 |
| 2025-08-31 | E12 Clicks Group | 1 |
| 2025-09-30 | E11 Pepkor Holdings | 1 |
| 2025-12-31 | E02, E03, E04, E05, E06, E08, E13, E16, E20 | 9 |
| 2026-03-31 | E14, E15, E17 | 3 |

The dates span **9 months**, from 2025-06-30 to 2026-03-31. `fy_label` takes two values:
FY2025 (17 entities), FY2026 (3 entities). E12 (August) and E11 (September) are the only two
entities on year ends that no other client shares, so period alignment now needs five buckets
rather than three.

**Reporting basis (audited annual vs interim):** there is **no column that states audited /
reviewed / interim status.** The nearest proxy is the `basis` column in
`external_financials_normalized.csv` (`as_reported` 345, `commentary` 11, `pro_forma` 9,
`derived` 8, `unknown` 6, `constructed` 1) and free text in `source_doc`. Reading `source_doc`
strings shows a mixture — "20-F/Annual Report 2025" (E01), "Preliminary Results 2025" (E02, E03),
"FY2025 Reviewed Results" (E05), "FY2026 Reviewed Annual Results" (E17), "FY2025 Reviewed Group
Financial Results" (E19), "FY2025 Audited Results" (E18), and `.docx`/`.pdf` filenames with no
audit descriptor (E11, E12, E13, E14, E15). So **at least four entities (E02, E03, E05, E17,
E19) are sourced from preliminary or reviewed rather than audited statements**, and the closed
vocabulary's `AFS_NOT_YET_AUDITED` status — which exists to record exactly this — is used zero
times. UNVERIFIED: no machine-readable field distinguishes audited from reviewed/preliminary, so
this classification rests on reading free-text `source_doc` strings. **Out of scope as of
2026-08-15** — it is a source-document question. The `basis` column remains in scope and is the
field to use: it distinguishes `as_reported` from `derived`, `pro_forma`, `commentary`,
`constructed` and `unknown`, which is a statement about how the number was built rather than
about who signed it off.

**Entities where more than one period appears without a period column to disambiguate:** none.
Every table that carries financial values also carries `fy_label`, and every entity has exactly
one `fy_label`. `fx_rates_sarb_daily.csv` is a time series with a `date` column, which is
correct. `fx_rate_crosscheck.csv` is the one file with **no `fy_label` column at all** (17 rows,
entity_id + rate_type only); because each entity has a single fiscal year this is currently
unambiguous, but it would silently break if a second year were added.

### 4.9 Reporting currency and ZAR conversion

| Reporting currency | Entities | Count |
|---|---|---:|
| ZAR | E06 Valterra Platinum, E07 OUTsurance Group, E08 Sanlam, E09 Shoprite Holdings, E10 Bid Corporation, E11 Pepkor Holdings, E12 Clicks Group, E16 MTN Group, E17 Vodacom Group, E18 The Bidvest Group, E19 Aspen Pharmacare | 11 |
| USD | E01 BHP Group, E02 Glencore, E03 Anglo American, E04 AngloGold Ashanti, E05 Gold Fields, E14 Prosus, E15 Naspers | 7 |
| EUR | E13 NEPI Rockcastle | 1 |
| GBP | E20 Shaftesbury Capital plc | 1 |

**Nine of 20 entities are non-ZAR reporters** (7 USD, 1 EUR, 1 GBP). `reporting_currency` is
identical in `entities.csv`, `external_financials_wide.csv` and
`external_financials_normalized.csv` for all 20 (a join filtering on divergence returns 0 rows).

**No ZAR conversion has been applied.** Evidence:

1. No column whose name contains `zar` exists in `external_financials_wide.csv` or
   `external_financials_normalized.csv`. (The only `zar`-named column in the directory is
   `zar_per_unit` in the two FX rate files, which holds rates, not converted values.)
2. Spot check: E01 BHP Group `revenue_total = 51,262,000,000.0` with
   `reporting_currency = USD`. BHP's FY2025 revenue is of that order in USD; converted at the
   FY-window average of 18.1569 ZAR/USD it would be ≈ R930.8bn. The stored value is the native
   USD figure.
3. Values are stored **raw in native reporting currency**. Both the `value_numeric` column and
   every wide-file numeric column are in the units of that row's `reporting_currency`.

The FX machinery to perform the conversion has been prepared but not applied. Three
independent rate sources exist:

- `fx_rates_normalized.csv` — the entity's own self-reported average/closing rates (27 of 50 rows
  usable; 17 NOT_DISCLOSED, 6 NOT_APPLICABLE for the three pure-ZAR entities E11/E12/E13).
- `fx_rates_fy_window.csv` — SARB daily rates averaged over each entity's own fiscal-year window
  for USD, GBP and EUR (51 of 60 rows `OK` with 249-252 observations each; 9 rows
  `BLOCKED_NO_FYE`, now stale rather than blocked — see Blocker 8).
- `fx_rates_sarb_daily.csv` — the underlying 903 daily observations per currency.

The nine stale rows are now fully derivable from `fx_rates_sarb_daily.csv`. Re-measured
2026-08-15 against the newly supplied fiscal year ends:

| Entity | FY window | USD avg | EUR avg | GBP avg | Obs per currency |
|---|---|---:|---:|---:|---:|
| E11 Pepkor Holdings | 2024-10-01 → 2025-09-30 | 18.0698 | 19.9693 | 23.5984 | 250 |
| E12 Clicks Group | 2024-09-01 → 2025-08-31 | 18.0858 | 19.8942 | 23.5741 | 249 |
| E13 NEPI Rockcastle | 2025-01-01 → 2025-12-31 | 17.8829 | 20.1810 | 23.5568 | 250 |

Closing rates (last observation on or before the year end) — E11: USD 17.2813, EUR 20.3124,
GBP 23.2408. E12: USD 17.7493, EUR 20.7400, GBP 23.9313. E13: USD 16.5980, EUR 19.4686,
GBP 22.3160. Observation counts (249–250) sit inside the 249–252 range of the existing `OK`
rows, so nothing about these three windows is unusual. **E13 NEPI Rockcastle is a EUR reporter
and therefore does need conversion**; E11 and E12 are ZAR reporters and do not.

`fx_rate_crosscheck.csv` compares self-reported to SARB for the 17 pairs where both exist; all
17 are flagged `ok` with `pct_diff` in the range **-0.85% to +0.47%**, so the two rate bases
agree to within one percent. **Which basis a future conversion should use is not recorded
anywhere**

### 4.10 Bounds

**Point values only.** There is no lower-bound, upper-bound, range, confidence-interval, or
`value_min`/`value_max` column in any file in `data/finances/`. The only bound-adjacent column is
`bound_recoverable` (BIGINT, 0/1) in `external_financials_normalized.csv`: **367 rows = 0,
13 rows = 1**. It is a boolean flag asserting that a bound *could in principle be recovered*
from further work; it stores no bound. All 13 flagged rows are absent-status rows:

| entity_id | Field | status |
|---|---|---|
| E05 Gold Fields | debt_maturity_note_page | NOT_FOUND |
| E06 Valterra Platinum | lenders_named | NOT_DISCLOSED |
| E07 OUTsurance Group | debt_maturity_note_page | NOT_APPLICABLE |
| E07 OUTsurance Group | lenders_named | NOT_DISCLOSED |
| E07 OUTsurance Group | employees | NOT_DISCLOSED |
| E08 Sanlam | fx_forward_notional | NOT_EXTRACTED |
| E11 Pepkor Holdings | lenders_named | NOT_DISCLOSED |
| E12 Clicks Group | committed_facilities_total | NOT_DISCLOSED |
| E14 Prosus | fx_forward_notional | NOT_DISCLOSED |
| E15 Naspers | revenue_south_africa | NOT_DISCLOSED |
| E15 Naspers | revenue_foreign | NOT_DISCLOSED |
| E15 Naspers | committed_facilities_total | NOT_DISCLOSED |
| E15 Naspers | undrawn_facilities | NOT_DISCLOSED |

Note that some absent rows contain a recoverable bound **inside the free text of `value_text`**
that is not machine-readable. Examples: E07 OUTsurance `employees` = `"Not disclosed/7800"`
(a number embedded in a NOT_DISCLOSED cell); E14 Prosus `fx_forward_notional` = "FY2026
contractual forward-exchange cash flows were US$1.360bn inflow and US$1.353bn outflow";
E19 Aspen `undrawn_facilities` = "only uncommitted facilities of R4.166bn and US$64m are
disclosed". These are bounds in prose, invisible to any numeric consumer.

### 4.11 Audit-log ↔ verified-values reconciliation

The audit log is `data_quality_exceptions.csv` (144 rows). The verified-values files are
`external_financials_normalized.csv` (380 rows) and, indirectly,
`external_financials_wide.csv`, `entities.csv` and the FX files.

**Rules recorded in the log:**

| Rule | Count | Target column |
|---|---:|---|
| FIX_SOURCE_COL_SHIFT | 105 | source_doc/url/page |
| NO_SARB_SERIES | 6 | foreign_currency |
| FIX_UNIT_LEAKAGE | 6 | unit |
| RESTORE_PRECISION | 5 | value |
| SPLIT_MULTI_CURRENCY | 4 | value |
| SOURCE_URL_UNSUPPORTED | 3 | source_url |
| MISSING_FYE | 3 | fiscal_year_end |
| LOAD_SARB_SERIES | 3 | (sarb) |
| INVERT_FX_DIRECTION | 2 | value |
| PARSE_NUMERIC | 2 | value |
| STRIP_INLINE_CAVEAT | 1 | value |
| NON_AFS_SOURCE | 1 | source_url |
| DROP_BLANK_ROW | 1 | (row) |
| RATE_NOT_PARSED | 1 | value |
| CHECK_REVENUE_SPLIT | 1 | (check) |

The 3 `MISSING_FYE` entries (E11, E12, E13) were **resolved on 2026-08-15** — `entities.csv` now
carries a `fiscal_year_end` for all three. The log entries remain as history; the exception they
describe no longer exists.

**Log entries per entity:** `*` (whole-file) 10, E01 2, E06 26, E07 23, E08 25, E09 25, E10 24,
E11 1, E12 1, E13 1, E14 2, E16 1, E17 1, E18 1, E19 1. Entities E02, E03, E04, E05, E15 and
E20 have **zero** log entries.

**Orphans, log side (log entry with no matching row in `external_financials_normalized.csv` on
entity_id + field):** 21 rows across 15 (entity, field) pairs.

| entity_id | field | rows |
|---|---|---:|
| E06 | avg_zar_rate | 2 |
| E06 | closing_zar_rate | 2 |
| E07 | avg_zar_rate | 2 |
| E07 | closing_zar_rate | 2 |
| E08 | avg_zar_rate | 2 |
| E08 | closing_zar_rate | 2 |
| E09 | avg_zar_rate | 1 |
| E09 | closing_zar_rate | 2 |
| E10 | avg_zar_rate | 2 |
| E10 | closing_zar_rate | 2 |
| E14 | avg_zar_rate | 1 |
| E14 | closing_zar_rate | 1 |
| E11 | (entity) | 1 |
| E12 | (entity) | 1 |
| E13 | (entity) | 1 |

All 21 are **not true orphans**, for two different reasons. The 18 `avg_zar_rate` /
`closing_zar_rate` rows are declared in `data_dictionary.csv` as belonging to the `fx_rates`
table, so they correctly target `fx_rates_normalized.csv` rather than the financials table. The
3 `(entity)` rows (rule `MISSING_FYE`, entities E11, E12, E13) target the
`entities.fiscal_year_end` column, which is also not a field of the financials table — and those
three exceptions were **closed on 2026-08-15**, when all three entities gained a
`fiscal_year_end`. The log entries were not updated to record the resolution, so the log now
describes an exception that no longer exists.

**Orphans, value side (rows in `external_financials_normalized.csv` with no log entry on
entity_id + field): 279 of 380 rows (73.4%).** This is expected behaviour for a
change-only log and is not by itself a defect: the log records corrections, not every extracted
value. It does mean **the log cannot be used to verify that a value was checked** — absence from
the log is indistinguishable between "never needed correcting" and "never reviewed".

**Verdict:** the two sides do not reconcile as a closed set and were not designed to. There is
no `verified` / `verified_by` / `verified_date` column in the current
`external_financials_normalized.csv` — the 105 `FIX_SOURCE_COL_SHIFT` entries record that such
columns previously existed and were **cleared** because their content had shifted into the
`source_doc`/`source_url`/`source_ref` columns. So the verification trail that would have made a
two-sided reconciliation possible has been deliberately deleted, and the only surviving evidence
of it is the log itself.

### 4.12 Internal consistency checks

Computed from `external_financials_wide.csv`, per entity, with a tolerance of ±0.51 currency
units (the data is stored to whole units). All figures in each entity's own
`reporting_currency`.

| entity_id | Entity | Ccy | gross_debt == debt_current + debt_noncurrent | net_debt == gross_debt − cash | revenue_total == revenue_SA + revenue_foreign | total_assets == equity + total_liabilities |
|---|---|---|---|---|---|---|
| E01 | BHP Group | USD | **pass** | unable | unable | unable |
| E02 | Glencore | USD | **pass** | unable | unable | unable |
| E03 | Anglo American | USD | **pass** | unable | unable | unable |
| E04 | AngloGold Ashanti | USD | **pass** | unable | unable | unable |
| E05 | Gold Fields | USD | **pass** | unable | unable | unable |
| E06 | Valterra Platinum | ZAR | **pass** | unable | **FAIL** (−68,000,000 ZAR) | unable |
| E07 | OUTsurance Group | ZAR | **pass** | unable | **pass** | unable |
| E08 | Sanlam | ZAR | **pass** | unable | **FAIL** (+102,903,000,000 ZAR) | unable |
| E09 | Shoprite Holdings | ZAR | **pass** | unable | **pass** | unable |
| E10 | Bid Corporation | ZAR | **pass** | unable | unable | unable |
| E11 | Pepkor Holdings | ZAR | **pass** | unable | **pass** | unable |
| E12 | Clicks Group | ZAR | **pass** | unable | unable | unable |
| E13 | NEPI Rockcastle | EUR | **pass** | unable | **pass** | unable |
| E14 | Prosus | USD | **pass** | unable | unable | unable |
| E15 | Naspers | USD | **pass** | unable | unable | unable |
| E16 | MTN Group | ZAR | **pass** | unable | **pass** | unable |
| E17 | Vodacom Group | ZAR | **pass** | unable | **pass** | unable |
| E18 | The Bidvest Group | ZAR | **pass** | unable | **FAIL** (+409,454,000 ZAR) | unable |
| E19 | Aspen Pharmacare | ZAR | **pass** | unable | unable | unable |
| E20 | Shaftesbury Capital plc | GBP | **pass** | unable | unable | unable |

**Summary: 20 pass / 0 fail / 0 unable** on the gross-debt identity;
**7 pass / 3 fail / 10 unable** on the revenue split;
**0 pass / 0 fail / 20 unable** on net debt;
**0 pass / 0 fail / 20 unable** on the balance-sheet identity.

Notes on the "unable" verdicts:

- **net debt:** there is no `net_debt` field in the dataset, so the identity has nothing to test
  against. `gross_debt − cash_and_equivalents` is computable for all 20 entities (values range
  from −17,601,000,000 ZAR for Sanlam to +38,541,000,000 USD for Glencore), but that is a
  derivation, not a check.
- **total assets == equity + total liabilities:** the fields `total_assets`, `equity` and
  `total_liabilities` **do not exist in the dataset at all**. The balance sheet is represented
  only by cash, inventory, trade receivables, trade payables and the debt triple. This check is
  unable for all 20 entities and will remain so unless those three fields are extracted.
- **revenue split, "unable" (10 entities):** `revenue_south_africa` and/or `revenue_foreign` is
  absent — E01, E02, E03, E04, E05, E10, E12, E14, E15, E19, E20 have at least one leg missing
  (11 entities have a missing leg; E12 and E20 both miss both legs, and the count of *fully*
  unable rows is 10 because E10 has SA present and foreign absent while E12/E20 have both
  absent — in every case the sum cannot be formed).
- The three revenue-split **failures** are material and are not rounding:
  - **E08 Sanlam:** revenue_total is 102,903,000,000 ZAR *larger* than SA + foreign. The
    `basis` for E08 `revenue_total` is `constructed`, so the total is a build-up rather than a
    reported line, and the segment legs are from a different measurement basis.
  - **E18 The Bidvest Group:** revenue_total exceeds SA + foreign by 409,454,000 ZAR (≈0.3% of
    revenue). `revenue_total` for E18 is sourced from investegate.co.uk (non-primary).
  - **E06 Valterra Platinum:** SA + foreign exceeds revenue_total by 68,000,000 ZAR. `basis`
    for E06 `revenue_total` is `commentary` and for `revenue_foreign` is `derived`, so a derived
    leg is being compared to a commentary total.
- The log file contains exactly one `CHECK_REVENUE_SPLIT` entry, so at least one of these three
  was noticed during extraction; the other two are not recorded.

---

## SECTION 5: INVARIANT CHECK

All measurements below were taken directly from `data/processed/*.parquet` and, where the
counterfactual required it, from the raw CSVs, using DuckDB. No figure was copied from
`quality_report.json`, `data_analysis.md`, or `README.md`.

### 5.1 Post-deduplication totals across the three flow datasets

Method: `SELECT COUNT(*), SUM(<amount>) FROM read_parquet('data/processed/<name>.parquet')`,
summed as DECIMAL(30,16) and rendered as VARCHAR to avoid float error.

| Dataset | Rows | MEASURED total (ZAR, exact) | MEASURED (ZAR bn) | Expected | Match |
|---|---:|---:|---:|---:|---|
| Transactional | 2,791,803 | 403,838,506,594.2760679160315000 | **R403.8385bn** | R403.87bn | **NO — short by R34,843,405.72** |
| Cross-border | 240,191 | 133,235,605,738.9100000000000000 | **R133.2356bn** | R133.24bn | **YES** (rounds to R133.24bn) |
| Trade finance | 20,215 | 38,305,641,003.3500000000000000 | **R38.3056bn** | R38.31bn | **YES** (rounds to R38.31bn) |

**Explanation of the transactional mismatch — it is not an error in either number, it is a
different dedup ordering.** Measuring the raw CSV directly:

- Raw source total (after DECIMAL cast, before any dedup): **R405,442,910,068.7139419689805**
- Value of rows removed by **case-sensitive** dedup (dedup before `UPPER(currency)`):
  **R1,569,057,444.0839934924890**
- 405,442,910,068.7139 − 1,569,057,444.0840 = **R403,873,852,624.6300 = R403.8739bn ≈ R403.87bn**

So **R403.87bn is the total that results if deduplication runs *before* currency
normalisation.** The code runs normalisation first (Section 3.4), removing 260 further rows, and
the actual figure in the Parquet on disk is **R403.8385bn**. The expected figure and the
measured figure differ by exactly the 260-row delta quantified in 5.4.

For completeness: `data_analysis.md` reports a raw (pre-dedup) transactional total of "R405.44bn",
which agrees with the measured R405.4429bn. `quality_report.json` reports a clean total of
`403838506594.2760679160315000`, which agrees exactly with the Parquet measured here.

### 5.2 Transaction IDs carrying conflicting payloads

Two different quantities are in play and they differ:

| Quantity | MEASURED | Match to expected 42,535 |
|---|---:|---|
| Transactional IDs with >1 **distinct payload** in the **raw CSV** (case-sensitive, i.e. before `UPPER(currency)`) | **42,535** | **YES — exact** |
| Transactional IDs with >1 distinct payload **after** `UPPER(currency)` (i.e. what the pipeline sees) | **42,289** | (246 fewer) |
| Transactional ID groups with >1 row in the **written Parquet** | **42,289** | |
| Transactional rows flagged `has_identifier_conflict = true` in the Parquet | **85,010** | |
| Transactional IDs reused at all in the raw CSV (including pure exact duplicates) | **52,984** | |

Method for the first row: `SELECT COUNT(*) FROM (SELECT transaction_id FROM (SELECT DISTINCT *
FROM <typed raw, currency NOT uppercased>) GROUP BY 1 HAVING COUNT(*) > 1)`.

The same measure for the other two datasets, on the written Parquet: cross-border **297**
conflict groups / **594** flagged rows; trade finance **3** conflict groups / **6** flagged rows.

**Does current code collapse or preserve them?** It **preserves** them, with one caveat.

- Preservation: `deduplicated` partitions by *every* business column, so two rows sharing a
  `transaction_id` but differing in any other field both survive
  ([clean_data.py:216-224](src/syn_wallet/clean_data.py#L216-L224)). `cleaned` then adds
  `has_identifier_conflict` ([L228](src/syn_wallet/clean_data.py#L228)) rather than choosing a
  winner. 42,289 groups / 85,010 rows are retained and flagged.
- Caveat: **246 of the 42,535 raw conflict groups are silently collapsed**, because their only
  payload difference was `ZAR` vs `zar`. Uppercasing makes those rows identical and dedup then
  removes one of each. That collapse is correct in substance (they were the same transaction)
  but it is unrecorded — no report field counts it, and `quality_report.json`'s
  `identifier_conflict_groups_retained: 42289` gives no hint that 42,535 existed in the source.

### 5.3 Trade finance value restricted to active/issued instruments

Method: `SELECT SUM(value_zar) FROM read_parquet('data/processed/trade_finance.parquet')
GROUP BY status`.

| status | Rows | Value (ZAR, exact) | Value (ZAR bn) |
|---|---:|---:|---:|
| active | 7,039 | 13,400,318,458.35 | R13.4003bn |
| issued | 2,983 | 5,811,229,850.62 | R5.8112bn |
| settled | 8,591 | 16,334,955,337.17 | R16.3350bn |
| expired | 1,602 | 2,759,137,357.25 | R2.7591bn |
| **active + issued** | **10,022** | **19,211,548,308.93** | **R19.2115bn** |
| **all statuses** | **20,215** | **38,305,641,003.35** | **R38.3056bn** |

**MEASURED R19.2115bn of R38.3056bn. MATCHES** the expected R19.2bn of R38.31bn.
Active+issued is **50.15%** of the total trade-finance book by value and 49.58% by instrument
count.

### 5.4 Row count and value at risk if dedup ran after currency normalisation

The premise needs restating, because the code already normalises first. The two orderings are:

| Ordering | Distinct transactional rows | Rows removed as duplicates | Value removed (ZAR) |
|---|---:|---:|---:|
| Dedup **before** `UPPER(currency)` (case-sensitive) | 2,792,063 | 10,812 | 1,569,057,444.0839934924890 |
| Dedup **after** `UPPER(currency)` (what the code does) | 2,791,803 | 11,072 | 1,604,403,474.4378740529490 |
| **Delta** | **260 rows** | **260** | **35,346,030.3538805604600** |

**MEASURED: 260 rows, R35,346,030.35 (R35.35m). MATCHES** the expected 260 rows / ~R35.3m.

Direction of the risk: these 260 rows are true duplicates that a case-sensitive dedup would
**leave in**, inflating the transactional total by R35.35m. The current code removes them. The
risk described in the invariant is therefore *not* currently realised — but it would be realised
by any downstream stage that re-reads the raw CSVs instead of the Parquet, or that reorders these
two steps. Cross-border and trade finance are unaffected: their raw exact-duplicate count and
their canonical duplicate count are identical (926 and 88 respectively), because neither dataset
has a `currency` column to case-fold.

### 5.5 Does any code path sum or union the transactional and cross-border datasets?

**No violation found.**

`src/` and `tests/` together contain 400 lines of Python. A grep for `UNION`, `union`, `concat`,
`append`, `merge`, `join` across both directories returns zero hits in any data-combining sense.
`clean_dataset` opens a **separate in-memory DuckDB connection per dataset**
([clean_data.py:208](src/syn_wallet/clean_data.py#L208), closed at
[L239](src/syn_wallet/clean_data.py#L239)), so the three datasets are never even co-resident in
one database. `run_pipeline` iterates the specs independently
([clean_data.py:261-265](src/syn_wallet/clean_data.py#L261-L265)) and assembles only a dict of
per-dataset report objects. There is no aggregate, no portfolio total, and no cross-dataset
arithmetic anywhere in the repository.

The risk is latent rather than present: nothing in the code, in `quality_report.json`, or in the
Parquet schemas warns a future consumer that `amount_zar` and `value_zar` must not be added.
`data_analysis.md` line 66 does state the caveat in prose, but no machine-readable guard exists.
UNVERIFIED: the 29,107 shared entity-date-direction cells asserted in `data_analysis.md` line 66
were not independently re-measured in this audit.

### 5.6 Single-product / low-engagement clients

Derived independently from the three Parquet files by aggregating per `entity_id`. No repo list
was consulted. Ordering is by the ratio of transactional value to cross-border value — the lower
the ratio, the more the client's visible activity is FX flow rather than day-to-day
transactional banking.

| entity_id | Entity | Txn rows | Txn value (ZAR bn) | XB rows | XB value (ZAR bn) | TF rows | TF value (ZAR bn) | **Txn ÷ XB value** |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| E17 | **Vodacom Group** | 7,849 | 0.7382 | 16,817 | 8.9995 | 303 | 0.5165 | **0.082** |
| E20 | **Shaftesbury Capital plc** | 1,418 | 0.0373 | 1,774 | 0.3385 | 30 | 0.0208 | **0.110** |
| E13 | **NEPI Rockcastle** | 2,787 | 0.1166 | 2,878 | 0.7121 | 20 | 0.0078 | **0.164** |
| E12 | **Clicks Group** | 9,026 | 0.5899 | 7,825 | 3.0315 | 201 | 0.2357 | **0.195** |
| E06 | **Valterra Platinum** | 794 | 0.1386 | 1,106 | 0.5695 | 204 | 0.3395 | **0.243** |
| E07 | **OUTsurance Group** | 13,770 | 0.8670 | 7,034 | 2.3800 | 191 | 0.2023 | **0.364** |
| E15 | Naspers | 32,707 | 4.1958 | 11,934 | 5.9698 | 88 | 0.1324 | 0.703 |
| E14 | Prosus | 49,695 | 5.8759 | 12,125 | 5.9943 | 38 | 0.1008 | 0.980 |
| E16 | MTN Group | 210,405 | 31.4025 | 31,777 | 19.3166 | 2,413 | 4.4039 | 1.626 |
| E05 | Gold Fields | 21,760 | 4.7630 | 5,069 | 2.8212 | 931 | 1.6811 | 1.688 |
| E02 | Glencore | 20,847 | 12.5200 | 4,499 | 7.1110 | 737 | 3.1321 | 1.761 |
| E04 | AngloGold Ashanti | 23,806 | 5.5112 | 4,639 | 2.5959 | 862 | 1.3964 | 2.123 |
| E10 | Bid Corporation | 222,431 | 33.2471 | 24,440 | 15.3116 | 2,751 | 5.4506 | 2.171 |
| E19 | Aspen Pharmacare | 106,644 | 9.6868 | 11,172 | 4.2230 | 1,127 | 1.2964 | 2.294 |
| E18 | The Bidvest Group | 107,618 | 13.3546 | 9,636 | 4.9808 | 2,342 | 3.7729 | 2.681 |
| E09 | Shoprite Holdings | 202,376 | 31.2539 | 15,806 | 10.1201 | 2,098 | 4.2454 | 3.088 |
| E11 | Pepkor Holdings | 929,773 | 103.0310 | 42,878 | 19.9939 | 3,151 | 4.5263 | 5.153 |
| E03 | Anglo American | 94,164 | 26.1514 | 5,863 | 4.0840 | 1,059 | 2.3373 | 6.403 |
| E01 | BHP Group | 124,728 | 46.5190 | 6,911 | 6.3406 | 1,307 | 3.9275 | 7.337 |
| E08 | Sanlam | 609,205 | 73.8386 | 16,008 | 8.3417 | 362 | 0.5800 | 8.852 |

**My list of anomalously low transactional engagement relative to FX flow, in order of
severity:**

1. **E17 Vodacom Group** — the clearest anomaly on every measure. Transactional value is
   **8.2%** of cross-border value; it is the only client in the portfolio with **more
   cross-border rows (16,817) than transactional rows (7,849)** by a factor of 2.1. Its
   transactional value of R0.7382bn is the third-lowest in the portfolio despite being a
   telecoms group whose sector peer MTN transacts R31.40bn.
2. **E20 Shaftesbury Capital plc** — 11.0%. Smallest client on every dimension (1,418
   transactional rows, R0.0373bn), and also has more cross-border rows than transactional value
   would suggest. A UK-domiciled REIT, so a low domestic footprint is plausible rather than
   necessarily a gap.
3. **E13 NEPI Rockcastle** — 16.4%. 2,787 transactional rows and 2,878 cross-border rows, i.e.
   near-parity. EUR reporter operating in Central/Eastern Europe.
4. **E12 Clicks Group** — 19.5%. This is the most commercially striking entry: a purely
   domestic South African retailer with **more cross-border value (R3.03bn) than transactional
   value (R0.59bn)**, and only 9,026 transactional rows against 202,376 for Shoprite, its direct
   sector comparator. A domestic retailer with a fifth of its bank flow visible domestically is
   the strongest single-signal candidate for lost transactional wallet.
5. **E06 Valterra Platinum** — 24.3%. Only **794 transactional rows across three years** — the
   lowest row count in the entire portfolio — and just **1 payroll transaction** in three years.
6. **E07 OUTsurance Group** — 36.4%. 13,770 transactional rows but only R0.8670bn of value, and
   19 payroll transactions in three years for a group with a disclosed but unverified headcount
   of ~7,800.

**Nothing is single-product.** All 20 clients appear in all three datasets, all 20 use all 5
`leg_type` values and all 5 `channel` values in the transactional data. So "single-product" is
not the failure mode present here; the pattern is uniform product breadth with wildly
heterogeneous depth. The four smallest clients by transactional value (E20 R0.0373bn,
E13 R0.1166bn, E06 R0.1386bn, E12 R0.5899bn) together account for 0.21% of the R403.84bn
transactional book.

**A second, sharper anomaly, visible in payroll:** payroll counts are grossly out of proportion
to client scale for six clients. Payroll rows over three years — E06 Valterra Platinum **1**,
E20 Shaftesbury Capital **2**, E13 NEPI Rockcastle **4**, E17 Vodacom **11**, E12 Clicks **13**,
E07 OUTsurance **19** — against E11 Pepkor 3,630, E08 Sanlam 2,375 and E10 Bid Corporation
2,140. Payroll is the stickiest transactional-banking product; a client with one payroll
instruction in three years is almost certainly running payroll at a competitor.

### 5.7 MTN vs Vodacom

Measured from `data/processed/transactional_banking.parquet`.

**Payroll (`leg_type = 'payroll'`):**

| Entity | Payroll rows | Payroll value (ZAR, exact) |
|---|---:|---:|
| MTN Group (E16) | **2,032** | 24,629,968.98 (R24.63m) |
| Vodacom Group (E17) | **11** | 101,133.11 (R0.101m) |

MTN has **184.7×** as many payroll rows and **243.5×** the payroll value.

**Domestic flow.** "Domestic" is not a field in the data; the closest available definition is
transactional-banking rows on a non-SWIFT channel (EFT, RTC, Internal Transfer, Debit Order).
Both definitions are given:

| Entity | Definition | Rows | Value (ZAR, exact) | Value (ZAR bn) |
|---|---|---:|---:|---:|
| MTN Group | All transactional rows | 210,405 | 31,402,533,346.19 | R31.4025bn |
| Vodacom Group | All transactional rows | 7,849 | 738,191,138.53 | R0.7382bn |
| MTN Group | Non-SWIFT channels only | 189,486 | 28,372,507,947.88 | **R28.3725bn** |
| Vodacom Group | Non-SWIFT channels only | 7,093 | 676,456,207.65 | **R0.6765bn** |

**MTN's domestic (non-SWIFT) flow is 41.9× Vodacom's by value and 26.7× by row count.** On total
transactional value the ratio is 42.5×.

For context these two are direct sector peers (both `sector = telecoms`), and the disparity does
not carry through to cross-border, where Vodacom's R8.9995bn is 46.6% of MTN's R19.3166bn — a
ratio of 2.1×, not 42×. Whatever is driving the transactional gap is specific to domestic
transactional banking, not to overall client size.

UNVERIFIED: no field in any dataset states which flows are domestic South African versus
in-country flows in other African markets, so the non-SWIFT proxy may misclassify intra-Africa
activity.

---

## SECTION 6: METRIC READINESS

### 6.1 Existing metric implementations

**Nothing exists.** Stated plainly:

- There is **no implementation of a share-of-wallet ratio** anywhere in the repository.
- There is **no implementation of a flow gap measure**.
- There is **no implementation of a cross-pillar priority score**.

Evidence: the repository contains four Python files totalling 400 lines
([src/\_\_init\_\_.py](src/__init__.py) 1 line, [src/syn_wallet/\_\_init\_\_.py](src/syn_wallet/__init__.py)
1 line, [src/syn_wallet/clean_data.py](src/syn_wallet/clean_data.py) 299 lines,
[tests/test_clean_data.py](tests/test_clean_data.py) 100 lines) and no notebooks, no R files, no
SQL files, and no dashboard code. A case-insensitive grep across `src/`, `tests/` and the
markdown files for `wallet`, `share_of`, `share of`, `gap`, `priority`, `score`, `ratio` returns
only:

- `src/syn_wallet/__init__.py:1` — the docstring `"""Syn Bank Share of Wallet data utilities."""`
- `README.md:15` — the command `python -m src.syn_wallet.clean_data --overwrite`
- The package directory name `syn_wallet`

All three are naming, not implementation. `clean_data.py` computes only counts, sums and null
tallies for its own quality report; none of those is a business metric. Nothing reads
`data/finances/`, so no denominator is currently connected to any numerator.

The deliverables named in `brief.md` (reproducible notebook, methodology document, GenAI
evidence, executive dashboard, presentation) have no corresponding artefact in the repository.

### 6.2 External-financials denominator availability, per client

**Bottom line under the 2026-08-15 scope: a `revenue_total` denominator is available for all 20
of 20 clients. None is absent.** `revenue_total` carries `status = 'OK'` for every entity, and
source provenance is no longer a gate. There is no entity that needs to be dropped, footnoted,
or down-weighted for want of a citation.

Two things do still constrain the denominator, and neither is about sourcing:

1. **Currency** — 9 of 20 denominators are not in ZAR (Section 4.9). Convert before dividing.
2. **Measurement basis** — the `basis` column records how the figure was arrived at:
   `as_reported` for 14 entities, and for the rest `pro_forma` (E09 Shoprite),
   `constructed` (E08 Sanlam), `commentary` (E06 Valterra, E10 Bid Corporation). E08's
   `constructed` total is the one to watch: it is the entity that also fails the revenue-split
   identity by 102,903,000,000 ZAR (Section 4.12).

The original source-quality split is retained below for reference only. **It is no longer a
readiness classification** — it grades citations, not values.

<details>
<summary>Superseded source-quality split (descriptive only)</summary>

Classification rule originally used:

- **Audited quality** = `revenue_total` has `status = 'OK'` and `source_reliability = 'AFS'`.
- **Non-primary source only** = `revenue_total` is `OK` but `source_reliability` is
  `AFS_URL_UNSUPPORTED`, `NON_AFS`, or `UNSOURCED` (i.e. no auditable primary document is
  attached).
- **Absent entirely** = `revenue_total` is not `OK`.

`revenue_total` is `status = OK` for **all 20 entities**, so nothing is absent on the raw
availability test. The split by source quality is:

| Category | Count | Entities |
|---|---:|---|
| **Available at audited quality** (`source_reliability = AFS`) | **12** | E01 BHP Group, E02 Glencore, E03 Anglo American, E04 AngloGold Ashanti, E05 Gold Fields, E11 Pepkor Holdings, E12 Clicks Group, E13 NEPI Rockcastle, E14 Prosus, E15 Naspers, E16 MTN Group, E18 The Bidvest Group |
| **Available only from a non-primary source** | **8** | E06 Valterra Platinum, E07 OUTsurance Group, E08 Sanlam, E09 Shoprite Holdings, E10 Bid Corporation, E17 Vodacom Group, E19 Aspen Pharmacare, E20 Shaftesbury Capital plc |
| **Absent entirely** | **0** | — |

Detail on the eight non-primary cases:

| Entity | source_reliability | basis | Source |
|---|---|---|---|
| E06 Valterra Platinum | UNSOURCED | commentary | no document, no URL |
| E07 OUTsurance Group | UNSOURCED | as_reported | no document, no URL |
| E08 Sanlam | UNSOURCED | **constructed** | no document, no URL — and this entity also fails the revenue-split check by 102,903,000,000 ZAR |
| E09 Shoprite Holdings | UNSOURCED | **pro_forma** | no document, no URL |
| E10 Bid Corporation | UNSOURCED | commentary | no document, no URL |
| E17 Vodacom Group | AFS_URL_UNSUPPORTED | as_reported | www.marketscreener.com |
| E19 Aspen Pharmacare | AFS_URL_UNSUPPORTED | as_reported | stockanalysis.com |
| E20 Shaftesbury Capital plc | UNSOURCED | as_reported | no document, no URL |

Note also that E18 The Bidvest Group is counted as `AFS` on its own declaration, but its URL is
`www.investegate.co.uk` — an RNS republisher, not the issuer or a regulator. Applying a stricter
primary-source test would move E18 into the second group, giving **11 audited / 9 non-primary /
0 absent**.

</details>

**Period alignment is no longer a constraint for any entity.** As of 2026-08-15 all 20 entities
carry a `fiscal_year_end`, so every denominator can be period-aligned to the flow window. The
mechanics are the only outstanding item: `fx_rates_fy_window.csv` has not been regenerated for
E11, E12 and E13 (Blocker 8), though their windows are fully derivable from
`fx_rates_sarb_daily.csv` (rates given in Section 4.9).

---

## SECTION 7: BLOCKERS

Ordered by downstream damage. Each is a defect actually observed, not a hypothetical.

**Will produce a wrong number silently**

1. **External financials are stored in nine different reporting currencies with no ZAR
   conversion applied, and no code reads the FX tables.**
   `data/finances/external_financials_wide.csv` — 7 rows USD (E01-E05, E14, E15), 1 EUR (E13),
   1 GBP (E20). `data/finances/external_financials_normalized.csv:value_numeric` likewise.
   Any share-of-wallet ratio computed today divides a ZAR numerator by a USD/EUR/GBP denominator
   for 9 of 20 clients, understating their apparent wallet by roughly 17-24× and making them look
   like the bank's best-penetrated accounts. Nothing in the schema signals this; the column is
   just called `revenue_total`. Grep confirms zero code references to `data/finances`.

2. **`external_financials_wide.csv` destroys the absence vocabulary.** All 86 absent cells become
   bare empty strings, with no `status` column in the file. Once loaded into pandas they are
   `NaN`, indistinguishable from each other and, after any `fillna(0)`, indistinguishable from
   the 10 genuine zeros. The distinction between "this client discloses zero debt" and "we could
   not find this client's debt" is exactly the distinction a gap measure depends on.
   `external_financials_normalized.csv` preserves it; the wide file is the one most likely to be
   loaded.

3. **Five zeros in debt and FX-notional fields cannot be cross-checked.** *(Downgraded
   2026-08-15 — the original framing was "carry no source reference", which is now out of scope.
   The residual concern is not the missing citation but that these particular values are both
   high-impact and unconfirmable by any other column.)*
   `data/finances/external_financials_normalized.csv` — E05 Gold Fields `fx_forward_notional`,
   E06 Valterra Platinum `debt_noncurrent`, E07 OUTsurance Group `gross_debt`, `debt_current`
   and `debt_noncurrent`. All five are `status = OK`, so a consumer will treat them as facts.
   A false zero on `gross_debt` reads as "this client has no borrowing need" and suppresses a
   lending opportunity signal entirely. Note that E07's three zeros are mutually consistent
   (`gross_debt = debt_current + debt_noncurrent = 0` passes the identity in Section 4.12), so
   they are at least internally coherent. Treat E06 and E07 zero-debt findings as provisional in
   any lending-opportunity narrative. The other five zeros (E12 × 3, E13 × 2) are unaffected.

4. **Three entities fail the revenue-split identity, silently.**
   `data/finances/external_financials_wide.csv` — E08 Sanlam (`revenue_total` exceeds
   SA + foreign by 102,903,000,000 ZAR), E18 The Bidvest Group (+409,454,000 ZAR), E06 Valterra
   Platinum (−68,000,000 ZAR). The Sanlam gap is ~40% of its stated revenue and its `basis` is
   `constructed`. Only one `CHECK_REVENUE_SPLIT` entry exists in
   `data/finances/data_quality_exceptions.csv`, so at most one of the three was noticed. Any
   geographic wallet split built on these three legs will not reconcile to its own total.

5. **The `expected R403.87bn` transactional total in circulation is the wrong ordering's
   answer.** The Parquet on disk holds R403,838,506,594.28. R403.87bn is what you get if dedup
   runs before `UPPER(currency)`. A downstream stage that re-derives totals from
   `data/transactional_banking.csv` with a naive `SELECT DISTINCT` will land on R403.87bn and
   silently disagree with the Parquet by R34,843,405.72 (260 rows). See
   [clean_data.py:213-224](src/syn_wallet/clean_data.py#L213-L224).

6. **246 identifier-conflict groups are collapsed without being counted.** The source has 42,535
   transactional IDs with conflicting payloads; after `UPPER(currency)` only 42,289 remain, and
   `quality_report.json:19` reports 42,289 with no field recording the 246 that were merged.
   The merge is substantively correct, but the report cannot be reconciled to the source and
   any audit that starts from the raw file will find a 246-group discrepancy it cannot explain.

7. **Nothing prevents a downstream stage from adding transactional and cross-border values.**
   The two Parquet files carry near-identical schemas with differently-named amount columns
   (`amount_zar`, `value_zar`), share all 20 entities and the same date range, and the
   transactional file contains 279,389 rows on the `SWIFT` channel that conceptually overlap
   cross-border. No code, schema field, or report key encodes the prohibition; the only record of
   it is prose in `data_analysis.md:66`. There is currently **no violation in code** — but the
   guard rail does not exist either.

8. **`fx_rates_fy_window.csv` is stale — it still reports three entities as undatable that are
   now dated.** *(Supersedes the original "three entities have no fiscal year end" blocker,
   which the 2026-08-15 `entities.csv` update resolved.)*
   `data/finances/entities.csv` now gives E11 Pepkor Holdings 2025-09-30, E12 Clicks Group
   2025-08-31 and E13 NEPI Rockcastle 2025-12-31, all with a cited `fye_basis`. But
   `data/finances/fx_rates_fy_window.csv` was last written 2026-08-14 21:11:04, before that
   update, and its 9 rows for those entities are still `BLOCKED_NO_FYE` with null `fy_start`,
   `fy_end`, `avg_rate`, `closing_rate` and `n_obs = 0`.
   The two files now **disagree with each other**, and the FX file is the one a conversion step
   would read. Anything joining on it will conclude these three entities cannot be period-aligned
   when they can. The windows are fully derivable from `fx_rates_sarb_daily.csv` (249–250
   observations each; rates given in Section 4.9) — the file simply needs regenerating.
   E13 is a EUR reporter and genuinely needs its rate; E11 and E12 are ZAR reporters and do not.
   The 3 `MISSING_FYE` entries in `data/finances/data_quality_exceptions.csv` are likewise now
   historical rather than open.

9. **`fx_rates_fy_window.csv` uses an out-of-vocabulary status `BLOCKED_NO_FYE` (9 rows), and
   `external_financials_normalized.csv` uses `NOT_EXTRACTED` (1 row, E08 Sanlam
   `fx_forward_notional`).** A validator written against the closed vocabulary
   {NOT_APPLICABLE, NOT_DISCLOSED, NOT_FOUND, NOT_COMPARABLE, AFS_NOT_YET_AUDITED} will either
   reject these rows or, if it uses a permissive `status != 'OK'` test, pass them without
   noticing the vocabulary has drifted.

10. ~~**Eight of 20 revenue denominators are unsourced or aggregator-sourced.**~~
    **WITHDRAWN 2026-08-15 — not a defect under the current scope.** Source documents and URLs
    are not a quality gate for this project. The underlying measurements stand and are retained
    in Section 4.7 as description: 15.0% of the 294 non-absent values carry both a URL and a page
    locator, and 159 of 380 rows (41.8%) are self-declared `UNSOURCED`. Nothing downstream should
    read `source_doc`, `source_ref`, `source_url` or `source_reliability`. What replaces this as
    the real denominator concern is **measurement basis**, not citation: E08 Sanlam's
    `revenue_total` is `constructed` and E09 Shoprite's is `pro_forma` — see Blocker 4 and
    Section 6.2.

11. **A numeric bound is buried in free text inside an absent cell.**
    `data/finances/external_financials_normalized.csv`, E07 OUTsurance Group / `employees`:
    `status = NOT_DISCLOSED` with `value_text = "Not disclosed/7800"`. A parser that strips
    digits from `value_text` would recover 7,800; one that does not will treat headcount as
    unknown. Similar prose bounds exist for E14 Prosus `fx_forward_notional`
    (US$1.360bn / US$1.353bn) and E19 Aspen `undrawn_facilities` (R4.166bn and US$64m).

12. **One currency-typed value has lost its currency tag.**
    `data/finances/external_financials_normalized.csv`, E01 BHP Group / `cost_of_sales`:
    `unit_type = currency` but `reporting_currency` is blank. It is `NOT_DISCLOSED` so no value is
    at risk today, but the invariant "every currency-typed row names its currency" is already
    broken and will not be caught if the value is later filled in.

13. ~~**The verification trail was deleted, not migrated.**~~
    **WITHDRAWN 2026-08-15 — not a defect under the current scope.** The finding was entirely
    about source-citation columns: 105 `FIX_SOURCE_COL_SHIFT` entries in
    `data/finances/data_quality_exceptions.csv` record that `verified` / `extracted_by` /
    `verified_date` content had leaked into `source_doc`/`source_url`/`source_ref` and was
    cleared. Since provenance is out of scope, the missing trail no longer blocks anything.
    Retained as history in Section 4.11.

**Will crash**

14. **The full-data test runs the entire pipeline over a 375 MB CSV.**
    [tests/test_clean_data.py:85-100](tests/test_clean_data.py#L85-L100) calls `run_pipeline` on
    the real `data/` directory. It will raise `FileNotFoundError` at
    [clean_data.py:264](src/syn_wallet/clean_data.py#L264) on any checkout that does not have the
    three gitignored CSVs on disk — which is every fresh clone, since the CSVs are named in
    `.gitignore` and only `data/data.tgz` is tracked. `pytest` fails immediately for a new
    contributor.

15. **`run_pipeline` raises `FileExistsError` by default when outputs exist.**
    [clean_data.py:249-251](src/syn_wallet/clean_data.py#L249-L251). Outputs currently exist, so
    the documented command in `README.md:15` only works because it passes `--overwrite`. Any
    programmatic caller that omits `overwrite=True` crashes rather than no-opping.

**Cosmetic**

16. `.DS_Store` at the repository root and in `data/` are not listed in `.gitignore`; they are
    currently suppressed only by a global ignore configuration outside the repo, so they will
    appear as untracked files for any collaborator without that global setting.

17. `data/finances/fx_rate_crosscheck.csv` has no `fy_label` column, unlike every other
    entity-keyed file in the directory. Unambiguous today because each entity has exactly one
    fiscal year; ambiguous the moment a second year is added.

18. `data/finances/data_quality_exceptions.csv` row 1 reads
    `DROP_BLANK_ROW | 420 rows | 420 rows | 0 fully-empty spacer rows removed` — `value_before`
    equals `value_after` and the note says zero rows were removed, so the entry records a no-op.

### Anything else a reader needs to know

- **Scale of the project versus its deliverables.** `brief.md` requires a reproducible notebook,
  a methodology document, GenAI evidence, an executive dashboard with client drill-downs and an
  opportunity heatmap, AI briefing notes for at least three clients, and a judging presentation.
  The repository contains a 299-line cleaning script and a 100-line test. None of the six
  deliverables exists in any form.

- **`brief.md` contradicts the supplied data in two places** (this is also noted in
  `data_analysis.md:67`, and I confirmed both independently): it describes 50 clients where the
  data has 20, and it lists "trading data" and "a mapped set of public financial statement
  inputs" as provided, neither of which is in `data/`. The `data/finances/` directory appears to
  be the team's own substitute for the missing financial-statement inputs, assembled by web
  extraction rather than supplied.

- **The raw inputs are not in version control, but they are recoverable.** The three CSVs the
  pipeline reads are gitignored; `data/data.tgz` (68.06 MB) is tracked. **VERIFIED 2026-08-15:**
  the archive contains exactly those three files and all three match on SHA-256
  (`transactional_banking.csv` `a827e867817303e1a4e32806489ae75d5e9a35b60f0381041041e37e68d1fb74`,
  `cross_border_payments.csv` `94c2329e2ceea50c85f88af32a0beeaac9bb8cea49d748277b15d551a1d95d51`,
  `trade_finance.csv` `2abe186bf6b8722e36a9590e19ac175ee23e6e792b8122752401fd55d02f06d1`).
  Restore with `tar -xzf data/data.tgz -C data/`. **The pipeline is reproducible from a clean
  clone.**

- **`data/finances/` is entirely disconnected.** It was written 2026-08-14 21:11:04 (with
  `entities.csv` revised 2026-08-15), four days after the last pipeline run, and zero lines of
  code reference it. Its internal quality is substantially higher than a first glance suggests —
  the long and wide files reconcile with zero discrepancies across 340 numeric cells, all 20
  entity names match the flow data exactly, the gross-debt identity passes 20/20, all 20 entities
  are now dated, and the FX cross-check agrees to within 0.85% — but none of that quality is
  reachable from code today.

- **Things this audit did not verify, stated explicitly:**
  - UNVERIFIED: the 2023-07-01 to 2026-06-30 date range. *(Since resolved — re-measured
    2026-08-15 against `data/processed/transactional_banking.parquet`: min 2023-07-01, max
    2026-06-30, 1,096 distinct dates. The `data_analysis.md` claim is correct.)*
  - UNVERIFIED: the 29,107 shared entity-date-direction cells between the transactional and
    cross-border datasets asserted in `data_analysis.md:66`.
  - UNVERIFIED: the percentile and median figures in `data_analysis.md` (they are self-declared
    as reservoir-sample estimates).
  - UNVERIFIED: whether the extracted financial values are factually correct against the
    underlying company reports. I verified internal consistency and structural integrity — I did
    not open any annual report to check a figure. Under the 2026-08-15 scope decision this is
    accepted rather than outstanding.
  - UNVERIFIED: the exact global git ignore configuration suppressing `.DS_Store`.
    *(Since resolved — `.DS_Store` and `data/.DS_Store` are now listed in `.gitignore`, so the
    global configuration no longer matters.)*
  - ~~UNVERIFIED: the contents of `data/data.tgz`~~ — resolved above.
  - ~~UNVERIFIED: the audited-versus-reviewed classification in Section 4.8~~ — out of scope;
    it rested on reading free-text `source_doc` strings.
