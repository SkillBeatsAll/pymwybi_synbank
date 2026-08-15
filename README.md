# Standard Bank & SU Hackathon 2026

- Team: **Put your money where your byte is**
- Members: Vihan Allan, Joel Cedras, Viajul Moodley, Rahul Maharaj

## Running everything

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt

tar -xzf data/data.tgz -C data/                               # restore raw inputs
.venv/bin/python -m src.syn_wallet.clean_data --overwrite     # stage 1: cleaning
.venv/bin/python -m src.syn_wallet.build_features --overwrite # stage 2: feature layer
.venv/bin/python -m src.syn_wallet.build_wallet --overwrite   # stage 3: wallet engine
.venv/bin/python -m pytest                                    # full suite
.venv/bin/python -m analysis.feature_layer_walkthrough        # tour of the feature layer
.venv/bin/python -m analysis.wallet_model_report              # regenerate MODEL_REPORT.md
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

## Stage 3 — wallet and opportunity engine (`src/syn_wallet/build_wallet.py`)

Five product pillars, each with its own economic model, its own denominator and
its own confidence. Full methodology, formulas, coefficients, diagnostics and
three worked client examples are in **[MODEL_REPORT.md](MODEL_REPORT.md)**,
which is generated from the outputs rather than hand-written.

| Pillar | Estimate | Denominator basis | Share? |
|---|---|---|---|
| Transactional / Cash Management | Collections + supplier payments the client must bank | `total_addressable_market` (accounting identity) | yes |
| FX / Global Markets | Exposure × peer settlement intensity + disclosed hedging | `peer_benchmark_addressable` | yes |
| Trade Finance | Import + export documentary + guarantees, sub-modelled | `peer_benchmark_addressable` | yes |
| Lending | Refinancing + undrawn + working capital + capex funding | `financing_opportunity` | **no** — Syn Bank has no loan book |
| Investment Banking | Ranked mandate-likelihood signal, no rand amount | `signal_only` | **no** |

### Outputs (`data/processed/`)

| File | Grain | Contents |
|---|---|---|
| `wallet_estimates.parquet` | 100 rows (20 × 5) | Observed, estimate, share, gap, confidence, opportunity score, ranks, flags, generated explanation |
| `opportunities.parquet` | 100 rows | The ranked banker-facing view, best first |
| `wallet_components.parquet` | long | Per-component breakdown with the driver behind each and whether it was disclosed or imputed |
| `model_diagnostics.parquet` | long | Model weaknesses at client, product and sector scope, with severity |
| `portfolio_summary.parquet` | 5 rows | Product-level totals, shares and confidence distribution |
| `model_assumptions.parquet` / `model_benchmarks.parquet` / `model_sector_rules.parquet` | — | Every coefficient with its basis and rationale; benchmarks re-measured each run |
| `wallet_confidence_detail.parquet` | 100 rows | The five confidence factors per client × product |
| `model_report.json` / `worked_examples.json` | — | Machine-readable run report and three full audit trails |

### The rules this stage holds to

- **No pricing.** Every figure is a flow or balance magnitude, never bank
  revenue. There is no fee, margin or basis-point assumption in the engine.
- **No invented competitor wallet.** A gap is addressable business *not observed
  in Syn Bank's supplied data* — never confirmed competitor-held business.
- **No pillar blending.** SWIFT-channel transactional volume is excluded from
  the cash numerator and not added to the FX numerator, so it is counted in
  neither. The amount is published per client.
- **No machine learning.** Every estimate is transparent arithmetic over
  declared assumptions, recomputable by hand from the component breakdown.
- **No silent zeros.** A missing driver resolves through a documented cascade or
  stays NULL; a NULL denominator gives a NULL share, not a division.
- **No absurd shares.** Where the modelled wallet falls below activity already
  flowing, it is floored at observed, flagged, and the unfloored value retained
  as `estimate_modelled_zar`.

## Stage 4 — dashboard and GenAI layer (`dashboard/app.py`, `src/syn_wallet/extract_competitor_evidence.py`, `src/syn_wallet/generate_briefing_note.py`)

Consumes `opportunities.parquet` directly — no transform layer between the wallet
engine and the UI. Portfolio summary, client drill-down and an opportunity heatmap
across all five pillars, plus a grounded briefing-note generator whose only source
of fact is a whitelisted JSON slice of the computed tables (never free generation).
Competitor-lender evidence is extracted from each client's AFS borrowings-note text
via `prompts/competitor_evidence_prompt.md`, distinguishing a bank actually named as
a lender from one named in some other capacity.

```bash
.venv/bin/streamlit run dashboard/app.py
```

If `data/processed/opportunities.parquet` doesn't exist yet (stage 3 not run), the
dashboard falls back to a schema-matched dummy file and says so with a banner —
never treat those numbers as real.
