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
.venv/bin/python -m src.syn_wallet.build_wallet --overwrite --sensitivity   # stage 3
.venv/bin/python -m src.syn_wallet.build_intelligence --overwrite          # stage 4
.venv/bin/python -m pytest                                    # 277 tests
.venv/bin/python -m analysis.feature_layer_walkthrough        # tour of the feature layer
.venv/bin/python -m analysis.wallet_model_report              # → MODEL_REPORT.md
.venv/bin/python -m analysis.model_sensitivity_report         # → MODEL_SENSITIVITY.md
.venv/bin/python -m analysis.model_final_report               # → MODEL_FINAL_REPORT.md
.venv/bin/python -m analysis.commercial_intelligence_report   # → COMMERCIAL_INTELLIGENCE_REPORT.md
```

`--sensitivity` rebuilds the engine 36 times to price every arguable coefficient
and takes a few seconds. Drop it for a fast rebuild of the model itself.

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

**Three Share of Wallet pillars and two opportunity signals.** Share of Wallet is
a claim about a denominator — *of the activity this client must transact
somewhere, what fraction runs through Syn Bank* — and only three pillars can
support one. The other two publish opportunity, never share.

The final, stable analytical contract is **[MODEL_FINAL_REPORT.md](MODEL_FINAL_REPORT.md)**:
methodology, terminology, every formula, the benchmark rules, both opportunity
rankings, the published schema, and what a dashboard may and may not show.
**[MODEL_SENSITIVITY.md](MODEL_SENSITIVITY.md)** prices every arguable
coefficient across 36 model runs. **[MODEL_REPORT.md](MODEL_REPORT.md)** carries
the per-client derivations and three worked examples. All three are generated
from the outputs rather than hand-written.

| # | Pillar | Role | Class | Estimate | Share? |
|---|---|---|---|---|---|
| 1 | Transactional / Cash Management | share of wallet | CORE | Addressable Cash Flow: revenue + cost of sales (accounting identity) | yes |
| 2 | FX / Global Markets | share of wallet | CORE | Exposure × peer settlement intensity + disclosed hedging | yes |
| 3 | Trade Finance | share of wallet | CORE | Import + export documentary + guarantees, sub-modelled | yes |
| 4 | Lending | opportunity signal | SUPPORTING | Refinancing + undrawn + working capital + capex funding | **no** — Syn Bank has no loan book |
| 5 | Investment Banking / Capital Markets | opportunity signal | SIGNAL_ONLY | Ranked mandate-likelihood signal, no rand amount | **no** |

The CORE / SUPPORTING / SIGNAL_ONLY class is **assigned by measurement at build
time**, not hardcoded, and published in `product_classification.parquet` so the
application layer reads it from the data.

### Addressable Cash Flow is not a wallet

`addressable_cash_flow_zar` is the client's own operating turnover — money it
must bank somewhere. `cash_management_wallet_zar`, the fee income a bank would
earn on it, is **NULL for every client and never derived**: Syn Bank discloses no
pricing, so any rand figure would rest on an invented basis-point assumption.
Two columns, two names, so the flow figure cannot be read as bank revenue.

### Peer benchmarks exclude the client they estimate

Where no accounting identity fixes a coefficient it is measured from the
client's peers at the 75th percentile — **with that client removed from the
population**. Including it is circular in both directions: a heavily penetrated
client raises the benchmark it is then measured against; a dormant one drags it
down and makes its own share look healthy. A sector population is used wherever
it reaches three peers after that exclusion, and the portfolio otherwise, with
the reason recorded per client. `model_benchmarks.parquet` carries one row per
client × metric with its level, sample size, median, P75, maximum and fallback
reason.

### Two opportunity rankings

| Ranking | Question it answers | Construction |
|---|---|---|
| `commercial_opportunity_score` | Where is the largest commercially meaningful opportunity? | 0.45 × within-product gap percentile + 0.30 × confidence + 0.25 × headroom |
| `opportunity_intensity` | Where is Syn Bank particularly under-penetrated relative to the client's scale? | `opportunity_zar / addressable_cash_flow_zar` — one ratio, no weights, no fitted coefficients |

They disagree, and they are meant to. Show both; never average them.

### Outputs (`data/processed/`)

**The analytical contract** — the only two tables the application layer reads:

| File | Grain | Contents |
|---|---|---|
| `opportunity_engine.parquet` | 100 rows (20 × 5) | The canonical grain: observed, addressable, opportunity, share, both scores, both ranks, benchmark provenance, diagnostics, generated explanation |
| `client_opportunity_profile.parquet` | 20 rows | Each pillar's headline side by side, plus top opportunity and recommended next product. **No column sums the pillars, and a build-time assertion prevents one appearing.** |

Supporting detail:

| File | Grain | Contents |
|---|---|---|
| `wallet_estimates.parquet` | 100 rows | The full internal estimate table the contract is projected from |
| `opportunities.parquet` | 100 rows | The ranked banker-facing view, best first |
| `wallet_components.parquet` | long | Per-component breakdown with the driver behind each and whether it was disclosed or imputed |
| `model_diagnostics.parquet` | long | Model weaknesses at client, product and sector scope, with severity |
| `portfolio_summary.parquet` | 5 rows | Product-level totals, shares and confidence distribution |
| `product_classification.parquet` / `product_confidence.parquet` | 5 rows each | The measured usability class, and mean/median confidence with HIGH/MEDIUM/LOW and major-flag percentages |
| `model_assumptions.parquet` / `model_benchmarks.parquet` / `model_benchmark_metrics.parquet` / `model_sector_rules.parquet` | — | Every coefficient with its basis and rationale; peer coefficients re-measured per client each run |
| `wallet_confidence_detail.parquet` | 100 rows | The five confidence factors per client × product |
| `model_sensitivity*.parquet` (with `--sensitivity`) | 3,600 rows + summaries | Every client × product row under all 36 scenarios, plus per-scenario and per-product comparisons and the robustness verdicts |
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
- **No self-benchmarking.** No client contributes to the peer population that
  sets its own coefficient, and no sector benchmark is built from fewer than
  three peers after that exclusion.
- **No unpriced assumption.** Every arguable coefficient is swept across 36 model
  runs, and each pillar carries a published robustness verdict.

### What the sensitivity sweep found

| Pillar | Verdict | Opportunity range across all 36 scenarios |
|---|---|---|
| Cash Management | **ROBUST** — untouched by every scenario | R14.25tn, identical throughout |
| Lending | **ROBUST** — rank ρ ≥ 0.997, under 5% drift | R1.32tn – R1.44tn (1.1×) |
| Trade Finance | assumption-sensitive | R39.28bn – R157.64bn (4.0×) |
| FX / Global Markets | assumption-sensitive | R78.37bn – R583.70bn (7.4×) |
| Investment Banking | no rand magnitude; signal ordering identical throughout | — |

Nine or ten of the base model's top ten opportunities survive every scenario.
The FX and trade **rand totals** should be presented as ranked opportunities with
a stated range, never as a single number — that is the honest consequence of
having no disclosed total for either activity, so the denominator *is* the
coefficient.

## Stage 4 — commercial intelligence (`src/syn_wallet/build_intelligence.py`)

The deterministic semantic layer. It answers the question a Corporate &
Investment Banking relationship manager actually asks: *which client should I
focus on, for which product, why, how strong is the evidence, and what should I
investigate next?*

**No LLM is called here.** Every sentence is a template filled from a published
field, so identical inputs always produce identical words. Its only inputs are
the analytical contract plus `model_sensitivity.parquet`; it recomputes nothing.

Full detail in
**[COMMERCIAL_INTELLIGENCE_REPORT.md](COMMERCIAL_INTELLIGENCE_REPORT.md)**,
generated from the outputs.

### Selecting the primary opportunity

Not the biggest rand number. The five pillars produce rand on incomparable bases
and their evidence quality differs by a factor of three, so selection discounts
the model's commercial score by what is known about it:

```
selection_score = commercial_opportunity_score
                × role_weight        CORE 1.00 / SUPPORTING 0.85 / SIGNAL_ONLY 0.55
                × confidence_weight  HIGH 1.00 / MEDIUM 0.80 / LOW 0.55
                × (1 − 0.20 if a HIGH-severity diagnostic is open)
                × (1 − 0.10 if the estimate is benchmark-sensitive)
```

A LOW-confidence FX row scoring 0.75 lands at 0.41; a HIGH-confidence lending row
scoring 0.60 lands at 0.51 and wins, whatever the rand amounts are. Each client
gets a **primary**, a **secondary** and a **supporting signal**.

### Opportunity status

| Status | Banker action | Rule |
|---|---|---|
| `PRIORITY` | Recommend investigation | HIGH confidence, score ≥ 0.65, no HIGH-severity diagnostic |
| `INVESTIGATE` | Consider investigation | Score ≥ 0.45 and not LOW confidence |
| `MONITOR` | Monitor / validate before pursuing | Everything else, and every SIGNAL_ONLY row |
| `NO_HEADROOM_DEMONSTRATED` | Retention conversation | Headroom under 5% of the addressable figure, or not sizeable |

**A LOW-confidence opportunity can never reach PRIORITY.** The only route is a
named entry in `PRIORITY_OVERRIDES` carrying a written reason — a decision a
person signs. The shipped registry is empty, and tests assert both.

### Outputs (`data/processed/`, Parquet + JSON)

| File | Grain | Contents |
|---|---|---|
| `client_opportunity_intelligence.parquet` | 20 rows | The full client profile: every pillar side by side, three selected slots, confidence and sensitivity per pillar, one-sentence summary |
| `portfolio_opportunity_intelligence.parquet` | long | Twelve sections of portfolio intelligence and dashboard-safe metrics |
| `banker_questions.parquet` | 100 rows | Client-specific questions, each parameterised by that client's own figures |
| `opportunity_explanations.parquet` | 100 rows | WHAT / WHY / EVIDENCE / CONFIDENCE / LIMITATION / NEXT ACTION per client × pillar |
| `client_opportunity_cards.parquet` | 20 rows | The compact list view |
| `opportunity_selection_detail.parquet` | 100 rows | Every selection factor, status and reason, so any decision can be re-derived |
| `opportunity_sensitivity_summary.parquet` | 100 rows | Base, low, high, range, rank stability and flag per client × pillar |

### Terminology, enforced in code

Cash management gets **Addressable Cash Flow** and never "fee pool", "fee
wallet", "bank revenue" or "revenue opportunity". FX and trade get
**peer-benchmark addressable**. Lending gets **financing opportunity** and never
share-of-wallet language. Investment banking gets **opportunity signal** and
never a rand figure. A test checks every generated string against the forbidden
list and fails the build.

---

There is still **no dashboard and no GenAI layer** — those are the next stages.
Both should read the stage 4 tables, and both should first read
[MODEL_FINAL_REPORT.md](MODEL_FINAL_REPORT.md) §12, which lists what may and may
not go on a screen.
