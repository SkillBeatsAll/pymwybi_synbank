# Standard Bank & SU Hackathon 2026

- Team: **Put your money where your byte is**
- Members: Vihan Allan, Joel Cedras, Viajul Moodley, Rahul Maharaj

## Running everything

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt

tar -xzf data/data.tgz -C data/                              # restore raw inputs
.venv/bin/python -m src.syn_wallet.clean_data --overwrite    # stage 1: cleaning
.venv/bin/python -m src.syn_wallet.build_features --overwrite # stage 2: feature layer
.venv/bin/python -m pytest                                   # 57 tests
.venv/bin/python -m analysis.feature_layer_walkthrough       # guided tour of the outputs
```

`data/processed/` is gitignored: every artefact below is regenerated from
`data/data.tgz` plus `data/finances/`, so a clean clone reproduces the whole
pipeline.

## Stage 1 — cleaning (`src/syn_wallet/clean_data.py`)

The raw CSV files in `data/` are immutable inputs. The cleaning pipeline writes
typed, ZSTD-compressed Parquet plus `quality_report.json` to `data/processed/`.

It removes only exact duplicate canonical records, standardises the
transactional `currency` code to uppercase **before** deduplicating, and retains
records with conflicting identifiers, marked with `has_identifier_conflict`.
Nothing is imputed, rounded, or silently resolved.

Parquet is used because the raw transactional CSV alone is about 375 MB and is
repeatedly scanned during analysis. The output is compressed, typed, and much
faster to query while retaining the source business fields.

## Stage 2 — client feature layer (`src/syn_wallet/build_features.py`)

Turns the cleaned Parquet and the prepared external financials in
`data/finances/` into a modelling-ready table, one row per client.

| Module | Responsibility |
|---|---|
| `config.py` | Paths, vocabularies, and the two load-bearing policies: the FX basis per field and the never-sum-the-pillars rule |
| `sources.py` | Registers every input as a DuckDB view; builds the entity dimension |
| `fx.py` | Fiscal-year ZAR rates from SARB daily observations, plus crosschecks |
| `external_features.py` | Canonical ZAR external financial table and its wide projection |
| `internal_features.py` | Per-pillar, per-scope flow features and the corridor detail |
| `ratios.py` | The 24 declared intensity ratios and the year-on-year trends |
| `validation.py` | 49 assertions covering joins, FX, period alignment, aggregates and nulls |
| `build_features.py` | Orchestration, output writing, run report |

### Outputs (`data/processed/`)

| File | Grain | Contents |
|---|---|---|
| `client_features.parquet` | 20 rows × 279 columns | The modelling table: identity, external financials in ZAR, internal features per scope, trends, ratios |
| `client_master.parquet` | 20 rows | The client spine: identity, fiscal period, FX rates, external financials in native currency and ZAR |
| `external_financials_zar.parquet` | 380 rows (20 × 19) | Long store: native value, ZAR value, rate used, rate type, conversion basis, `status`, `basis`, `gap_reason` |
| `client_corridor_breakdown.parquet` | ~2,750 rows | Full counterparty-country and currency-pair distributions per client and scope |
| `feature_report.json` | — | Policy, FX crosscheck, coverage counts, and every validation result |

### FX policy

Nine of the twenty clients report in USD, EUR or GBP. Every external monetary
value is converted to ZAR before any ratio is taken, on one basis for all
twenty clients: **SARB daily mid-rates windowed by each entity's own fiscal
year**, taken from `fx_rates_sarb_daily.csv` and `entities.csv`.

- **Flow measures** (revenue, cost of sales, finance costs, capex) convert at
  the **fiscal-year average** rate.
- **Stock measures** (inventory, receivables, payables, debt, cash, facilities,
  FX notional) convert at the **fiscal-year-end closing** rate.
- ZAR reporters convert at exactly 1.0, basis `no_conversion`.

The rule lives in `config.FX_BASIS_BY_FIELD` and is asserted field by field in
`tests/test_fx.py`. The derived rates reproduce all 51 `OK` rows of
`fx_rates_fy_window.csv` exactly and fill the nine rows that file left blocked;
they agree with the entities' own published rates to well within 1.5%.

### Feature scopes

Metric suffixes mark the window a measure was aggregated over:

| Suffix | Window | Use |
|---|---|---|
| `_36m` | 2023-07-01 → 2026-06-30 | Full internal history |
| `_fy` | The client's own fiscal year | **The only scope divided by an external denominator** |
| `_r12m` / `_p12m` | Trailing years to 2026-06-30 / 2025-06-30 | Year-on-year trend |

All 20 fiscal-year windows fall inside the flow window, so no client is
period-aligned against partial internal data.

### What this stage deliberately does not do

- No share of wallet, total wallet, or competitor wallet is estimated.
- No fee, margin or basis-point assumption is applied. Syn Bank is fictional and
  has no disclosed pricing, so any rand of "revenue" would be invented.
- The transactional and cross-border pillars are never summed. 279,389
  transactional rows sit on the `SWIFT` channel and conceptually overlap
  cross-border payments by an amount the supplied fields cannot resolve.
- No explained absence is imputed to zero. "Discloses zero debt" and "we could
  not find this client's debt" stay distinguishable.
