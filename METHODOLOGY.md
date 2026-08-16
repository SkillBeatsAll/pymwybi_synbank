# Syn Bank Share of Wallet Intelligence Engine — Methodology

**Team *Put your money where your byte is*** — Vihan Allan, Joel Cedras, Viajul Moodley, Rahul Maharaj
Standard Bank & Stellenbosch University Hackathon, 2026

| | |
|---|---|
| Analytical methodology | `wallet-1.1.0` |
| Commercial intelligence layer | `intelligence-1.0.0` |
| Copilot / prompts | `copilot-1.1.0` / `copilot-prompt-1.1.0` |
| Portfolio | 20 JSE-listed corporates, `E01`–`E20`, 7 sectors |
| Estimates published | 20 clients × 5 pillars = 100 |
| Test suite | **523 tests, all passing** |
| Companion notebook | [`SynBank_Share_of_Wallet_Analysis.ipynb`](SynBank_Share_of_Wallet_Analysis.ipynb) |

This document is the formal methodology for the system implemented in `src/syn_wallet/`. It is
the reference form of the argument the notebook makes by running it. There is **one**
methodology: the notebook, the dashboard, the generated briefings and this document all
describe the same engine and read the same two published tables.

Generated companion documents, each written by a script in `analysis/` that reads the Parquet
back off disk: `docs/MODEL_FINAL_REPORT.md` (the analytical contract), `docs/MODEL_SENSITIVITY.md` (the
36-run sweep), `docs/MODEL_REPORT.md` (per-client derivations), `docs/COMMERCIAL_INTELLIGENCE_REPORT.md`,
`docs/GENAI_DESIGN.md`, `docs/GENAI_PROMPTS.md`, `docs/AUDIT_REPORT.md` (data quality).

---

# 1. Executive Summary

A large corporate does not bank in one place. Syn Bank observes only the portion of a client's
banking activity that settles through Syn Bank, and from inside its own ledger it cannot
distinguish a well-served client from an almost-entirely-absent one.

This engine puts a **defensible denominator** next to the observed activity, built from the
client's own published financial statements, and refuses to invent one where none exists.

**Five product pillars, three of which can support a Share of Wallet claim.**

| Pillar | Role | Class | Publishes |
|---|---|---|---|
| 1. Transactional / Cash Management | share of wallet | `CORE` | Addressable Cash Flow, observed, share, opportunity |
| 2. FX / Global Markets | share of wallet | `CORE` | peer-benchmark addressable, observed, share, opportunity |
| 3. Trade Finance | share of wallet | `CORE` | peer-benchmark addressable, observed, share, opportunity |
| 4. Lending | opportunity signal | `SUPPORTING` | a rand financing need. **No share.** |
| 5. Investment Banking / Capital Markets | opportunity signal | `SIGNAL_ONLY` | a ranked signal and a category. **No rand, no share.** |

**Headline portfolio position**, each client measured on its own fiscal year:

| Pillar | Class | Observed | Addressable | Opportunity | Share | Mean confidence | Sensitivity verdict |
|---|---|---|---|---|---|---|---|
| Transactional / Cash Management | `CORE` | R58.81bn | R14.31tn | R14.25tn | 0.41% | 0.86 | `ROBUST` |
| FX / Global Markets | `CORE` | R45.18bn | R381.34bn | R336.15bn | 11.85% | 0.35 | `ASSUMPTION_SENSITIVE` — opportunity spans 7.4× |
| Trade Finance | `CORE` | R12.70bn | R128.66bn | R115.96bn | 9.87% | 0.34 | `ASSUMPTION_SENSITIVE` — opportunity spans 4.0× |
| Lending | `SUPPORTING` | n/a | R1.39tn | R1.39tn | no share | 0.61 | `ROBUST` |
| Investment Banking | `SIGNAL_ONLY` | n/a | n/a | n/a | no share | 0.38 | ordering robust, no rand |

**These five rows are never added together and there is no sixth.** A build-time assertion
prevents a cross-pillar total appearing in the published client profile.

**What the engine deliberately does not produce.** No fee, margin, basis-point or bank-revenue
figure anywhere — Syn Bank is fictional and discloses no pricing, so any such number would be
invented. No lending or investment-banking share of wallet — there is no observed numerator to
divide. No portfolio total — the transactional and cross-border pillars overlap by an
unresolvable amount. And no claim that an unserved gap is held by a competitor.

---

# 2. Business Problem

## 2.1 The blind spot

Corporate clients use multiple banking providers. A treasury team routes collections through
one bank, payroll through another, hedges with a third and syndicates its debt across five. Syn
Bank's transaction systems record only what passes through Syn Bank.

The consequence is that **observed activity carries no information about the size of the
relationship**. A client sending R2.5bn a year through Syn Bank could be a small client fully
served, or one of the largest commodity traders in the world of whose operating turnover Syn
Bank touches 0.03%. In this portfolio it is the second. A relationship manager working from
internal data alone cannot tell, and the coverage conversation defaults to whatever the client
volunteers.

## 2.2 The objective

1. **Estimate addressable activity** — how much banking activity each client must transact
   somewhere, derived from its disclosed economics.
2. **Measure Share of Wallet** wherever a defensible denominator exists.
3. **Identify and rank growth opportunities**, on two axes that answer two different questions.
4. **Make the result actionable** through a Generative AI copilot that explains validated
   analytical results and never computes one.

## 2.3 Four terms held strictly apart

| Term | Column | Definition |
|---|---|---|
| **Observed activity** | `observed_zar` | The in-scope activity Syn Bank actually handled for this client in the fiscal year, measured from the internal datasets. **Never an estimate.** |
| **Estimated addressable activity** | `addressable_zar`, `addressable_cash_flow_zar` | The activity the client must transact somewhere, across all of its banks. For cash it is an accounting identity; for FX and trade it is a peer-benchmarked exposure. **A flow magnitude belonging to the client, not to any bank.** |
| **Opportunity** | `opportunity_zar` | Addressable activity not observed in Syn Bank's data. |
| **Fee wallet** | `cash_management_wallet_zar` | The fee income a bank would earn on that flow. **NOT ESTIMABLE, published as NULL for every client, permanently.** |

The fee wallet exists as a named, permanently NULL column so that it is *visibly absent*
rather than an omission a reader fills in with the flow figure.

## 2.4 Opportunity is not competitor ownership

> `opportunity_zar` is addressable activity **not observed in Syn Bank's supplied data**. It is
> not evidence a competitor holds it, not lost revenue, and not win-back. The client may
> transact it elsewhere, may transact it through a channel Syn Bank does not record, or may not
> transact it at all in a given year.

The one *observed* competitor signal in the data is the `memo` field: 4,184 rows across the
three internal datasets describing facility drawdowns, bridging finance, loan drawdown proceeds
and syndicate participation settlements passing through Syn Bank accounts on credit Syn Bank
did not extend. That is evidence, and it is reported as evidence in the lending narrative
(§9.4). Everything else is a gap.

A sixteen-phrase blocklist (`intelligence.config.FORBIDDEN_PHRASES`) is checked by test against
every deterministically generated string and by the validator against every sentence the
language model writes: *fee pool, fee wallet, bank revenue, revenue opportunity, revenue
wallet, competitor-held, competitor held, held by competitors, win back, win-back, lost
revenue, guaranteed revenue, confirmed revenue, total opportunity across, lending share of
wallet, investment banking share of wallet.*

---

# 3. Data Architecture

```
data/*.csv                      3 raw internal flow datasets (gitignored; data/data.tgz restores them)
  └─► clean_data.py             stage 1  ──► data/processed/*.parquet + quality_report.json
data/finances/*.csv             9 prepared external financial files
  └─► build_features.py         stage 2  ──► client_features.parquet (20 × 289)
        │                                     client_master · external_financials_zar
        │                                     entity mapping · fiscal alignment · ZAR conversion
        └─► build_wallet.py     stage 3  ──► opportunity_engine.parquet          ◄── CONTRACT
              │                               client_opportunity_profile.parquet ◄── CONTRACT
              │                               + 12 supporting tables + model_report.json
              │                               + 4 sensitivity tables (36 full model runs)
              └─► build_intelligence.py stage 4 ──► client_opportunity_intelligence
                    │                                portfolio_opportunity_intelligence
                    │                                banker_questions · opportunity_explanations
                    └─► copilot/      stage 5  router → retrieval → context → LLM → validation → audit
                          └─► api/ + dashboard/  stage 6  service projects; the browser never computes
```

## 3.1 The analytical contract

Everything upstream of `src/syn_wallet/wallet/contract.py` is free to change shape. Everything
downstream — dashboard, GenAI narrative, this notebook, any export — reads **only** the two
tables that module declares, so a model change that would break an application breaks a test
first.

**`opportunity_engine.parquet`** — grain: one row per client × product, 100 rows, 37 columns.
Column names are deliberately product-neutral (`addressable_zar`, `opportunity_zar`) because
the five pillars' rand figures mean different things; the `estimate_basis` column, not the
column name, says which.

**`client_opportunity_profile.parquet`** — grain: one row per client, 20 rows. Each pillar's
headline side by side. **No column sums the pillars, and none can be built by summing them.**

## 3.2 The one arithmetic prohibition

The three internal pillars are **never summed into a portfolio total**. 279,389 transactional
rows sit on the `SWIFT` channel and conceptually overlap cross-border payments; the overlap
cannot be resolved from the supplied fields, because there is no product-lineage key and the
two datasets share entity, date and direction cells without exact payload matches. Adding them
double-counts an unknown amount.

`contract.assert_no_pillar_summation()` runs inside `build_client_profiles()` on **every
build**, not only in the test suite, and fails loudly if any column equals the row-wise total
of the pillar columns. `api/service.py::_assert_no_cross_pillar_total()` runs over every
dashboard payload. The copilot's validator rejects the claim in digits *and* in words.

---

# 4. Data Preparation

## 4.1 Internal Data

Three flow datasets, all spanning **2023-07-01 to 2026-06-30** (1,096 distinct dates), read
from cleaned Parquet and never from the raw CSV.

| Dataset | Clean rows | Total (ZAR, exact) |
|---|---:|---:|
| `transactional_banking` | 2,791,803 | R403,838,506,594.2760679160315000 |
| `cross_border_payments` | 240,191 | R133,235,605,738.91 |
| `trade_finance` | 20,215 | R38,305,641,003.35 |

**Cleaning policy.** `clean_dataset()` builds `raw → canonical → deduplicated → cleaned`.
`UPPER(currency)` is applied when the canonical relation is built — **before** the
`ROW_NUMBER() OVER (PARTITION BY <all business columns>)` de-duplication. Normalising first
catches 260 additional true duplicate rows worth R35,346,030.35 that a case-sensitive de-dup
leaves in. The ordering is load-bearing and must not be reversed. Anyone re-deriving the
transactional total from the raw CSV with a naive `SELECT DISTINCT` will land on R403.87bn and
silently disagree with the Parquet by R34,843,405.72.

**Exact duplicates are removed. Conflicting records are preserved and flagged.** An identifier
reused with a *different* payload is a data-quality fact about the source system, not a row to
silently pick a winner for. 42,289 transactional conflict groups are retained with
`has_identifier_conflict = true` on 85,010 rows (cross-border 297 groups / 594 rows; trade
finance 3 / 6), so any downstream analysis can exclude them explicitly and say that it did.

**Three apparent signals are generation artefacts and are not modelled.** Day-of-week row
counts are flat (max ÷ min = 1.011 — no weekday, month-end or payment-cycle structure); channel
mix is near-identical across all twenty clients (widest client-to-client spread on any channel:
3.6 percentage points); and `commodity_or_contract_type` is drawn independently of `sector`, so
no supply-chain exposure may be inferred from it.

## 4.2 External Financial Data

The canonical store is **`data/finances/external_financials_normalized.csv`** — long format, 20
entities × 19 fields = 380 rows, carrying `status`, `basis` and `gap_reason` alongside every
value.

**`external_financials_wide.csv` is display-only and must never be an analytical input.** It has
no `status` column, and its 86 absent cells are bare empty strings — indistinguishable from
each other and, after any `fillna(0)` downstream, indistinguishable from the 10 genuine zeros.
*This client discloses zero debt* and *we could not find this client's debt* are opposite
statements, and a gap measure depends entirely on telling them apart. The wide file reconciles
to the normalized store across all 340 numeric cells, so it is a faithful projection — but a
projection that has thrown away the one column the model needs.

**Absence is recorded with a reason, never as a zero.** Only `status = 'OK'` cells are usable.
The status vocabulary has drifted beyond its original closed set (`NOT_EXTRACTED` and
`BLOCKED_NO_FYE` now appear), so validators accept the union rather than failing on a
legitimate new reason code.

**Measurement basis is the denominator-quality signal that matters.** `basis` distinguishes
`as_reported` from `pro_forma`, `constructed`, `commentary` and `derived`. A soft basis is
carried into the model as an `internal_consistency` penalty in the confidence score and as a
`revenue_denominator_soft_basis` diagnostic — never as a reason to drop a row.

**Scope decision.** `source_doc`, `source_ref`, `source_url` and `source_reliability` are out of
scope by team decision. No entity is excluded, footnoted or down-weighted because its citation
is thin. What matters about a value is its currency, its `basis`, and whether it satisfies the
internal identities.

### Known input defects, carried as flags rather than corrected

| Defect | Detail | Handling |
|---|---|---|
| Revenue-split identity fails for 3 clients | E08 Sanlam (`revenue_total` exceeds SA + foreign by R102.9bn, `basis = constructed`), E18 The Bidvest Group (+R409m), E06 Valterra Platinum (−R68m) | `internal_consistency` penalty + `revenue_split_identity_failed` diagnostic |
| Five unverifiable zeros | E05 `fx_forward_notional`; E06 `debt_noncurrent`; E07 `gross_debt`, `debt_current`, `debt_noncurrent` — all `status = OK`, so they read as facts | Treated as provisional in any lending narrative. A false zero on `gross_debt` suppresses a lending signal entirely |
| Four clients disclose no cost of sales | Their supplier-payment and import components rest on a peer ratio applied to revenue | `driver_source = 'portfolio_benchmark'` recorded per component; cuts `input_completeness`; `cogs_imputed` diagnostic |
| No balance-sheet totals exist | `total_assets`, `equity`, `total_liabilities` are absent from the data entirely | The balance-sheet identity cannot be checked for any entity. The gross-debt identity passes 20/20 |

## 4.3 Entity Mapping

Entity ids, names and sectors are **identical across all four sources** — the three flow
datasets and `data/finances/`. Zero name mismatches, no whitespace or `Ltd`/`Limited`
divergence. The join is exact and requires no fuzzy matching and no mapping table.

`sources.entity_dimension_sql()` is the single production join:

- `entities.csv` is authoritative for `fy_label`, `reporting_currency` and `fiscal_year_end`.
- `sector` exists only on the flow datasets, where it is identical across all three, and is
  taken from their union.
- Integrity is asserted: 20 entities, 0 sector conflicts, 0 name conflicts, 0 missing fiscal
  year ends, 0 fiscal years falling outside the internal flow window.

Names are short trading names, not registered legal names; a join to an external register by
legal name would need a mapping table that does not exist.

## 4.4 Fiscal Period Alignment

Each client is aligned to **its own** twelve-month window:

```
fy_start = fiscal_year_end − 1 year + 1 day
fy_end   = fiscal_year_end
```

All 20 entities carry a `fiscal_year_end`, across **five distinct dates spanning nine months**:
2025-06-30 (6 entities), 2025-08-31 (E12 Clicks), 2025-09-30 (E11 Pepkor), 2025-12-31 (9),
2026-03-31 (3).

**This is the decision the data forces.** There is no single portfolio "fiscal year". Aligning
everyone to one calendar window would measure a different twelve months of trading against each
client's published accounts — a denominator from one period opposite a numerator from another.
Aligning each client to its own window is the only choice that keeps numerator and denominator
on the same period.

The consequence is stated rather than hidden: **cross-client rand comparisons carry the caveat
that the twelve months are not the same twelve months.** Every window falls inside the 36-month
internal flow window, so no client is period-aligned against partial internal data.

Internal features are computed over four scopes: the full 36-month window (`_36m`), each
client's fiscal year (`_fy`, the scope every wallet estimate uses), and trailing 12-month pairs
(`_r12m`, `_p12m`) for year-on-year trend.

## 4.5 FX Conversion

**Nine of twenty clients report in a currency other than ZAR** — 7 USD, 1 EUR, 1 GBP. Internal
flow values are all ZAR. Dividing a ZAR numerator by an unconverted foreign denominator
understates those nine clients' wallets by roughly 17–24× and makes them look like the bank's
best-penetrated accounts.

### The policy

Declared as a dictionary in `src/syn_wallet/config.py::FX_BASIS_BY_FIELD` so it cannot drift
out of the documentation:

| Item class | Rate | Fields |
|---|---|---|
| **Income statement / cash flow** (flow) | fiscal-year **average** | `revenue_total`, `revenue_south_africa`, `revenue_foreign`, `cost_of_sales`, `finance_costs`, `capex` |
| **Balance sheet / facility register** (stock) | fiscal-year-end **closing** | `inventory`, `trade_receivables`, `trade_payables`, `gross_debt`, `debt_current`, `debt_noncurrent`, `cash_and_equivalents`, `fx_forward_notional`, `committed_facilities_total`, `undrawn_facilities` |
| **Non-monetary** | never converted | `employees`, `debt_maturity_note_page`, `lenders_named` |

A flow accrues across the year, so an average rate is the only unbiased translation of it. A
stock exists at one instant — the reporting date — so it translates at the rate on that date.
Mixing the two is the classic translation error.

### The rates

**Basis: SARB daily mid-rates averaged over each entity's own fiscal-year window.**
`fx_rates_sarb_daily.csv` supplies 903 observations per currency (USD, EUR, GBP) from
2023-01-03 to 2026-08-14; `entities.csv` supplies all 20 fiscal year ends. Every entity gets a
rate from the same method, with no per-entity source switching.

- `avg_rate` — arithmetic mean of daily mid-rates across the fiscal year.
- `closing_rate` — last observation on or before the fiscal year end.
- ZAR reporters convert at exactly 1.0 with basis `no_conversion`. No FX rate is applied to
  them, and **no internal ZAR flow value is ever re-denominated.**

Two prepared files are used as **cross-checks, not inputs**:

- `fx_rates_fy_window.csv` — same method, but stale: its nine `BLOCKED_NO_FYE` rows for E11,
  E12 and E13 predate the fiscal year ends now in `entities.csv`. The derivation reproduces all
  51 of its `OK` rows to within a 5×10⁻⁴ ZAR tolerance — a *reproduction* check, not a
  reconciliation tolerance — and fills the nine gaps.
- `fx_rates_normalized.csv` — each entity's self-reported average/closing rates (27 of 50
  usable), confirming the derived rates agree with what the clients published to within a
  1.5% tolerance. The prepared `fx_rate_crosscheck.csv` records agreement within 0.85%.

The FX machinery is **deterministic**: same inputs, same rates, every run.

---

# 5. Product Definitions

Share of Wallet is a claim about a denominator: *of the activity this client must transact
somewhere, what fraction runs through Syn Bank*. Three pillars can support that claim. Two
cannot, and calling their output a share would mean inventing the denominator.

## 5.1 Pillar roles

| Pillar | `pillar_role` | Why |
|---|---|---|
| Cash Management | `share_of_wallet` | Revenue + cost of sales is an accounting-identity denominator |
| FX / Global Markets | `share_of_wallet` | A peer benchmark applied to a disclosed exposure gives a defensible, stated denominator |
| Trade Finance | `share_of_wallet` | Same |
| Lending | `opportunity_signal` | **No observed numerator.** Syn Bank's datasets contain no loan book |
| Investment Banking | `opportunity_signal` | **No observed numerator and no defensible rand denominator.** No deal record, no mandate log, no pipeline |

## 5.2 Product classes — assigned by measurement, not hardcoded

```
no rand estimate for anybody           → SIGNAL_ONLY
rand estimate but no computable share  → SUPPORTING
share computable for ≥ 50% of clients  → CORE
```

Classification runs at build time in `wallet/opportunity.py::classify_products()`. A future run
in which the lending data gained an observed numerator would reclassify itself rather than
silently contradict a dashboard.

| Class | Meaning | How to present it |
|---|---|---|
| **`CORE`** | Rand denominator and observed numerator both exist for the majority of the portfolio | A share of wallet is computable and may be a headline number — with its confidence band in the same visual unit |
| **`SUPPORTING`** | A rand amount exists but no observed numerator does | Show the rand amount as an opportunity indicator alongside a CORE pillar, **never as a share** |
| **`SIGNAL_ONLY`** | No rand amount is estimable at all | Show the ranked signal and the category, **never a currency figure** |

## 5.3 Estimate bases — not interchangeable, never compared as one number

| Product | `estimate_basis` | What the rand figure is |
|---|---|---|
| Cash Management | `addressable_cash_flow` | The client's whole disclosed operating payment-and-collection turnover, across all of its banks |
| FX / Global Markets | `peer_benchmark_addressable` | The client's own economic driver scaled by the intensity a well-penetrated peer in this portfolio achieves |
| Trade Finance | `peer_benchmark_addressable` | Same |
| Lending | `financing_opportunity` | A financing-need indicator built from disclosed debt structure |
| Investment Banking | `signal_only` | Nothing. No rand amount is estimated |

---

# 6. Cash Management Methodology

## 6.1 Definitions

```
Addressable Cash Flow
  addressable_cash_flow_zar = revenue_total_zar + cost_of_sales_zar
                              ^ collections        ^ supplier payments

Observed Activity
  observed_zar = domestic collections + domestic supplier payments, fiscal year
                 (SWIFT-channel volume excluded)

Share
  share = observed_zar / addressable_cash_flow_zar          capped at 1.0

Opportunity
  opportunity_zar = addressable_cash_flow_zar − observed_zar      floored at 0

Fee wallet
  cash_management_wallet_zar = NULL          permanently, for every client
```

## 6.2 Why the coefficients are 1.0

Both are **accounting identities on an `accounting_identity` basis**, not coefficients to tune:

- **`collections_banked_share = 1.0`** — revenue is ultimately received into a bank account, so
  the collections a corporate generates across all of its banks equals its revenue.
- **`supplier_payment_share_of_cogs = 1.0`** — cost of sales is settled in cash to suppliers
  through a bank account. Timing differences shift a payment between periods but not the annual
  total in a steady state.

Because both are identities, **no scenario in the 36-run sensitivity grid can move this
pillar**: 0% drift, rank correlation 1.000.

Where reported revenue is not a cash measure — insurance revenue includes investment return and
non-cash reserve movements — the sector applicability weight (0.55) and a
`revenue_not_a_cash_measure` diagnostic carry the caveat, rather than a fabricated haircut.

## 6.3 Exclusions, and why each one is excluded from *both* sides

| Excluded | Reason | What is published instead |
|---|---|---|
| **Payroll** | No employee-cost field exists in the external data, and observed payroll volume is a token R61m across the whole portfolio for a year at ~R11k per transaction. Sizing a payroll wallet would require inventing a cost per head | A **mandate signal**: instructions per 1,000 employees, feeding confidence and the narrative |
| **Tax** | No tax charge is disclosed; observed tax volume is R163m across the portfolio for a year | An engagement signal only |
| **Intercompany sweeps** | Treasury sweep volume has no external anchor. Including observed sweeps in the numerator while the denominator cannot cover them would inflate share | Reported as `out_of_scope_observed_zar`, so the excluded amount stays visible |
| **SWIFT-channel volume** | Conceptually overlaps the cross-border pillar by an amount the supplied fields cannot resolve | Excluded from the cash numerator **and not added to FX**, so no rand is counted twice in either direction. Published per client as `overlap_excluded_zar` |

The principle is the same in every case: **putting an invented denominator opposite a token
numerator manufactures a fake gap.** An excluded leg is reported, not deleted.

## 6.4 This is a flow magnitude, not bank revenue

**Preferred phrasing throughout: "Addressable Cash Flow" or "Cash Management Addressable
Flow".** Never "cash management wallet", never "revenue opportunity", never "fee pool".

Addressable Cash Flow is the client's own annual operating turnover — money it must push through
*a* bank account somewhere. Syn Bank's share of it says how much of that turnover currently
settles through Syn Bank. It says nothing about what Syn Bank would earn, because the fee wallet
on that flow is not estimable from this data.

---

# 7. FX Methodology

## 7.1 Economic drivers

No disclosure states any client's total cross-border settlement volume across all of its banks,
so there is no identity to appeal to. The denominator is built from the client's own disclosed
exposure, scaled by measured peer intensity.

```
export_settlement = revenue_foreign_zar      × peer_p75(xb_inbound  / revenue_foreign)
import_settlement = cost_of_sales_zar        × peer_p75(xb_outbound / cost_of_sales)
hedging_execution = fx_forward_notional_zar  × 1.0 roll per year

addressable_zar = export_settlement + import_settlement + hedging_execution
                  floored at observed_zar
observed_zar    = cross-border volume, fiscal year
share           = observed_zar / addressable_zar
opportunity_zar = addressable_zar − observed_zar
```

**Inbound and outbound are separated** because they are driven by different economics. Money
coming *in* is export settlement, benchmarked against **foreign revenue**; money going *out* is
import settlement, benchmarked against **cost of sales**. Netting them would put one coefficient
against a driver it does not describe.

**Foreign revenue is used as exposure, not as volume.** It measures how much of the client's
business is transacted across a border; the peer coefficient converts that exposure into an
expected settlement volume.

**Disclosed FX forwards are added where a client publishes a notional.** The coefficient is
`fx_forward_rolls_per_year = 1.0` on a `structural` basis: a disclosed forward book has to be
executed at least once to exist. Corporates typically roll shorter-dated hedges several times a
year, but no forward tenor is disclosed, so **one roll is a deliberate floor**. Where no
notional is disclosed the component is NULL and a `no_disclosed_hedging` diagnostic fires.

## 7.2 Flooring at observed

If the exposure model produces less than the client's observed activity, the wallet is floored
at observed and the estimate carries `wallet_floored_at_observed`. This means the disclosed
exposure drivers do not describe that client's cross-border business at all — a far larger
problem than an imputed input — so confidence is **halved** (`FLOOR_PENALTY = 0.50`), and the
explanation states that no headroom can be demonstrated and none is claimed.

## 7.3 Peer benchmarks

See §11. In summary: 75th-percentile observed intensity, **leave-one-out**, sector population
preferred with a hard three-peer floor and a portfolio fallback with a four-peer floor.

## 7.4 Confidence

FX averages **0.345 mean confidence, 0% HIGH, 30% MEDIUM, 70% LOW**, with 30% of clients
carrying a HIGH-severity diagnostic. The reason is structural, not a failure: `evidence_directness`
for a peer-benchmark coefficient is 0.60, and it multiplies rather than votes, so the pillar
cannot exceed 0.60 confidence even where every input is disclosed. Imputed cost of sales,
missing hedging disclosure and flooring push it lower.

## 7.5 Sensitivity

**The FX denominator *is* the coefficient.** With no disclosed total to anchor to, changing the
peer statistic changes the answer directly. Across the 36-scenario grid the portfolio FX
**opportunity** total spans **7.4×** (R78.37bn – R583.70bn) and the worst within-pillar rank
correlation is 0.510.
Verdict: `ASSUMPTION_SENSITIVE`. **18 of 20 client estimates are flagged `SENSITIVE`.**

This is not a defect. It is the honest consequence of having no disclosure to anchor to. The
required presentation is: **rank these clients, quote the range, name the benchmark population,
never quote a point estimate.**

---

# 8. Trade Finance Methodology

## 8.1 Three sub-models

```
import_documentary = cost_of_sales_zar    × peer_p75(tf_import     / cost_of_sales)
export_documentary = revenue_foreign_zar  × peer_p75(tf_export     / revenue_foreign)
guarantees         = revenue_total_zar    × peer_p75(tf_guarantees / revenue)

addressable_zar = the applicable components, floored at observed_zar
observed_zar    = instruments dated in the fiscal year, ALL FOUR statuses
```

- **Import documentary** — letters of credit and import collections against the goods a client
  buys, driven by cost of sales.
- **Export documentary** — export collections and export letters of credit against the goods a
  client sells abroad, driven by foreign revenue.
- **Guarantees** — financial and performance guarantees, driven by revenue as a proxy for the
  scale of contractual commitments a business of that size carries.

## 8.2 The observed scope, stated rather than assumed

The four trade-finance statuses are **not equivalent cash flows** and are never summed as one
number without saying so. The numerator here is the value of instruments **dated inside the
fiscal year across all four statuses**, because the denominator is *annual issuance demand*.
This is the one place the four statuses are legitimately summed, and it is declared as
`trade_observed_scope` in the assumption registry. The live book (`active + issued`) is reported
alongside it, never inside it.

Full-window breakdown: active 7,039 instruments / R13.40bn; issued 2,983 / R5.81bn; settled
8,591 / R16.33bn; expired 1,602 / R2.76bn.

## 8.3 Sector treatment

"Should an insurer be scored for import letters of credit?" is a modelling judgement, not a data
fact, so it is declared in `assumptions.SECTOR_RULES` rather than allowed to emerge from an
aggregation.

| Sector | Applicability | Suppressed sub-models | Reason |
|---|---|---|---|
| insurance | **0.30** | `import_documentary`, `export_documentary` | An insurer buys no goods, so cost of sales and inventory are not trade drivers. Only guarantees apply — genuine ones for a financial services group |
| real_estate | **0.30** | `import_documentary`, `export_documentary` | A property group holds no tradeable inventory and imports no goods. Guarantees remain: rental deposits, construction performance bonds, utility guarantees |
| tech | 0.60 | — | Digital and marketplace revenue settles electronically rather than under documentary credit, but device and hardware procurement keeps the drivers relevant |
| telecoms | 0.60 | — | Trade finance applies to network equipment and handset procurement, a minority of the cost base, and neither telecoms client discloses cost of sales |
| all others | 1.00 | — | The drivers apply without adjustment |

A suppressed sub-model is **NULL, never zero**, and applicability of 0.30 pushes confidence
into LOW — so a reader is told that only a third of the model ran.

## 8.4 Confidence and sensitivity

Trade averages **0.337 mean confidence, 0% HIGH, 15% MEDIUM, 85% LOW**, 25% with a
HIGH-severity diagnostic. Across the grid the portfolio **opportunity** total spans **4.0×**
(R39.28bn – R157.64bn), worst rank correlation 0.848. Verdict: `ASSUMPTION_SENSITIVE`. Rank these; quote
the range.

---

# 9. Lending Methodology

## 9.1 Why this is a financing opportunity and not a wallet or a share

A share of wallet is a numerator divided by a denominator. **Syn Bank's supplied datasets
contain no loan book.** There is no observed lending numerator for any client — not a small one,
not a zero, none at all — so there is nothing to divide.

The engine publishes a **rand-denominated financing need** instead, and labels it a financing
opportunity signal. `share` is NULL for all twenty clients, `observed_zar` is NULL for all
twenty, `share_basis` reads `no_observed_activity_in_dataset`, and a test asserts that no
lending share of wallet can appear anywhere in the system.

## 9.2 Components

```
refinancing        = debt_current_zar        × 1.0    structural
undrawn_facilities = undrawn_facilities_zar  × 1.0    structural
working_capital    = working_capital_zar     × peer_median(debt_current / working_capital)
capex_funding      = capex_zar               × 0.30   JUDGEMENT

opportunity_zar = the sum of the components (NULL components excluded)
```

- **Refinancing** is structural, not estimated: debt classified as current is contractually
  repayable within twelve months, so the whole balance is a financing decision inside the
  horizon. **This is not a claim that Syn Bank could win it.**
- **Undrawn facilities** are, by disclosure, committed capacity another lender is already
  providing that the client is not using. The full balance is a contestable facility at its
  next renewal.
- **Working capital** = inventory + trade receivables − trade payables, scaled by the peer
  median debt-funded share. Negative working capital yields a zero component and a
  `negative_working_capital` diagnostic.
- **Capex funding** uses `capex_debt_funded_share = 0.30` — **the one underived coefficient in
  the engine.** Capex is funded from a mix of operating cash flow and new debt, and no
  cash-flow-statement field exists to split it. 0.30 is a deliberately conservative third. A
  `capex_judgement_dominates` diagnostic fires wherever this component exceeds half of a
  client's estimate, and the published component breakdown lets a reviewer set it to zero and
  re-read the number.

## 9.3 Confidence and sensitivity

Lending averages **0.607 mean confidence, 30% HIGH, 55% MEDIUM, 15% LOW**. Directness is high
because two of four components are structural (0.90). Across the grid the total moves under 5%
with rank correlation ≥ 0.997 — verdict `ROBUST`, even when the capex coefficient moves by a
third in either direction.

## 9.4 The observed competitor-credit evidence

Everything above is a need *inferred* from disclosure. The `memo` field is different: it is
**observed** evidence of lending activity Syn Bank is not the lender on, visible in Syn Bank's
own ledger. All populated memos reduce to four phrase templates — `Settlement re: facility
drawdown`, `Bridging facility settlement`, `Loan drawdown proceeds`, `Syndicate participation
settlement` — and appear only on `supplier_payments` and `intercompany_sweeps` legs, never on
payroll, tax or collections.

It is reported as a supporting signal on the lending narrative and **never converted into a rand
estimate**, because a settlement amount is not a facility size. Four clients — Sanlam, BHP
Group, Pepkor Holdings, Anglo American — have zero memos, which is a signal in its own right,
in either direction.

---

# 10. Investment Banking Methodology

## 10.1 Why this is signal-only

Nothing in the supplied data indicates a planned issue, disposal or acquisition. There is no
deal record, no mandate log and no pipeline. Sizing an investment-banking wallet from a balance
sheet alone would be pure invention.

```
signal_score = 0.20 × scale               (percentile rank of revenue within the portfolio)
             + 0.25 × leverage            (percentile rank of net debt / revenue)
             + 0.25 × near_term_maturity  (percentile rank of debt_current / gross_debt)
             + 0.20 × capex_intensity     (percentile rank of capex / revenue)
             + 0.10 × syndicate_breadth   (percentile rank of lenders named)

observed_zar = NULL   addressable_zar = NULL   opportunity_zar = NULL   share = NULL
```

**NULL, never zero.** *We cannot size this* and *this is worth nothing* are opposite statements,
and a `fillna(0)` anywhere downstream would merge them. `contract.NO_RAND_DENOMINATOR` enforces
the NULLs at build time and a test asserts them.

## 10.2 Mandate categories

A category is assigned **only when a disclosed threshold is met**; otherwise none is assigned
and the model says so.

| Threshold | Value | Category it can trigger |
|---|---|---|
| `ib_near_term_maturity_threshold` | 0.30 of gross debt classified current | debt capital markets |
| `ib_capex_intensity_threshold` | 0.10 capex / revenue | corporate finance / project funding |
| `ib_leverage_threshold` | 0.50 net debt / revenue | refinancing or restructuring |
| `ib_cost_of_debt_threshold` | 0.09 finance costs / gross debt | supports the refinancing conversation |

All four are `judgement` basis, which is why `evidence_directness = 0.35` caps the pillar's
confidence: mean **0.382, 0% HIGH, 25% MEDIUM, 75% LOW**.

## 10.3 Presentation rule

Investment banking is never recommended as a "next product to investigate" — it would send a
banker to a page with no number on it — and can never rise above `MONITOR` status. Its ordering
is unchanged across all 36 scenario runs (`NO_RAND_MAGNITUDE_ORDERING_ROBUST`), because there is
no rand magnitude to be sensitive.

---

# 11. Benchmark Methodology

Where no accounting identity fixes a coefficient, it is **measured, not invented**.

## 11.1 The 75th percentile

`benchmark_percentile = 0.75`. The maximum would let a single outlier define every client's
wallet; the median would define the wallet as *average* performance and understate the
opportunity. The upper quartile is "what a well-penetrated peer achieves". §14 measures what
the median and the 80th percentile would do instead.

## 11.2 Leave-one-out

**The client being estimated is excluded from the population that sets its own coefficient.**

Including it is circular in both directions: a heavily penetrated client raises the benchmark it
is then measured against, flattening its own apparent gap; a client with no activity drags the
benchmark down and makes its own share look healthy. With twenty clients a single company is 5%
of the portfolio and up to a third of its sector, so the circularity is **material, not
theoretical**.

`PeerBenchmarks.leave_one_out_p75(client_id, metric)` is the primitive. Tests assert directly on
the populations that no client appears in its own, and the published `model_benchmarks.parquet`
carries `leave_one_out` and `self_in_population` flags on all 120 client × metric rows.

**The driver-imputation cascade is leave-one-out by construction.** Where a client does not
disclose cost of sales or foreign revenue, the value is imputed from a sector median — but only
clients that *disclosed* the field contribute to that median, and only clients that did *not*
disclose it consume one, so a client can never impute its own driver from itself.

## 11.3 Sector first, portfolio fallback, hard sample floors

```
IF the client's sector has ≥ 3 peer observations AFTER excluding the client:
        use the sector P75
ELSE:   use the portfolio P75  (itself requiring ≥ 4 contributors after exclusion)
IF neither population is large enough:
        publish no coefficient — the component is NULL, never a borrowed number
```

`MIN_SECTOR_SAMPLE_FOR_BENCHMARK = 3` — below three peers, one company's intensity would set the
sector's frontier and the estimate would be a restatement of that company rather than a sector
norm.
`MIN_BENCHMARK_SAMPLE = 4` — below four contributors an upper-quartile intensity is an anecdote,
and the coefficient is published as unavailable rather than as a number.

## 11.4 Which population each metric used

| Metric | Pillar | Population n | On sector | On portfolio | No coefficient | Population P75 |
|---|---|---:|---:|---:|---:|---:|
| `fx_inbound_per_foreign_revenue` | FX | 9 | 0 | 20 | 0 | 0.0846 |
| `fx_outbound_per_cost_of_sales` | FX | 12 | 10 | 10 | 0 | 0.0148 |
| `trade_import_per_cost_of_sales` | Trade | 12 | 10 | 10 | 0 | 0.0068 |
| `trade_export_per_foreign_revenue` | Trade | 6 | 0 | 20 | 0 | 0.0283 |
| `trade_guarantees_per_revenue` | Trade | 20 | 10 | 10 | 0 | 0.0017 |
| `lending_current_debt_per_working_capital` | Lending | 12 | 10 | 10 | 0 | 0.3175 |

**The two foreign-revenue metrics land on the portfolio for every client, and that is the rule
working rather than failing.** Only nine of twenty clients disclose foreign revenue at all,
spread across six sectors with at most two each, so no sector can reach three peers. The rule
declines to build a sector benchmark exactly where it would have been one or two companies.

## 11.5 Full traceability

Every client × metric coefficient records `benchmark_level`, `benchmark_n`, `benchmark_value`,
`benchmark_median`, `benchmark_p75`, `benchmark_max`, `sample_entities`, `leave_one_out`,
`self_in_population` and `fallback_reason`. Any single estimate can be reconstructed from
`model_benchmarks.parquet`. Every published row carries `benchmark_level` and `benchmark_n`
onto the dashboard, because *"P75 of 4 mining peers, this client excluded"* is the sentence that
makes the number defensible.

---

# 12. Confidence Methodology

## 12.1 The formula

```
input_quality = 0.35 × input_completeness
              + 0.25 × sector_applicability
              + 0.20 × observation_support
              + 0.20 × internal_consistency

confidence    = input_quality × evidence_directness

bands: HIGH ≥ 0.70    MEDIUM ≥ 0.45    LOW below
```

## 12.2 Input quality — how good the inputs are

| Factor | Weight | What it measures |
|---|---:|---|
| `input_completeness` | 0.35 | Fraction of the pillar's economic drivers the client actually disclosed, rather than being imputed from peers |
| `sector_applicability` | 0.25 | Whether the model's economic logic applies to this sector at all — an insurer scored for import letters of credit should not read as confident |
| `observation_support` | 0.20 | How much internal activity backs the estimate, **log-scaled** against the busiest client in the pillar. Log rather than linear, because the difference between 800 and 8,000 transactions is real evidence while the difference between 600,000 and 900,000 is not. The reference point is measured from the portfolio, so no threshold is invented |
| `internal_consistency` | 0.20 | Whether related disclosures agree: the gross-debt identity, the revenue split, and whether the revenue denominator is as-reported or constructed. A NULL identity flag means the identity could not be *checked* and is treated as neutral, not as a failure |

## 12.3 Method directness — and why it is a ceiling, not a vote

`evidence_directness` describes how sound the **method** is, and **multiplies** the weighted sum
above rather than being averaged into it.

| Coefficient basis | Directness |
|---|---:|
| `accounting_identity` | 1.00 |
| `structural` | 0.90 |
| `portfolio_benchmark` | 0.60 |
| `judgement` | 0.35 |

It is value-weighted across a pillar's components, then **scaled by the fraction of expected
components that were actually realised** — because weighting purely by component *value* hides a
missing component, which carries zero weight and so cannot pull the average down. Half a model
is half as direct. It is then **halved again** (`FLOOR_PENALTY = 0.50`) where the wallet had to
be floored at observed activity.

**Why this design.** An earlier additive version of this score rated all twenty
investment-banking estimates HIGH — a pillar whose every threshold is an undrawn judgement —
purely because the balance-sheet fields behind it were fully disclosed. **Having every input a
model needs cannot make a weak model confident.**

## 12.4 Portfolio confidence by product

| Pillar | Class | Mean | Median | HIGH | MEDIUM | LOW | Major flag |
|---|---|---:|---:|---:|---:|---:|---:|
| Transactional / Cash Management | `CORE` | 0.860 | 0.925 | 80% | 20% | 0% | 10% |
| FX / Global Markets | `CORE` | 0.345 | 0.393 | 0% | 30% | 70% | 30% |
| Trade Finance | `CORE` | 0.337 | 0.335 | 0% | 15% | 85% | 25% |
| Lending | `SUPPORTING` | 0.607 | 0.611 | 30% | 55% | 15% | 10% |
| Investment Banking | `SIGNAL_ONLY` | 0.382 | 0.397 | 0% | 25% | 75% | 0% |

"Major flag" is the fraction of clients carrying at least one **HIGH-severity** diagnostic for
that pillar — the *do not quote before review* class, not the merely noteworthy one.

> **The confidence spread is the model's most important finding about itself.** Cash management
> rests on identities; FX and trade rest on peer coefficients applied to partly imputed drivers.
> A dashboard that shows the FX rand figure at the same visual weight as the cash rand figure is
> misrepresenting the model.

---

# 13. Opportunity Scoring

Two rankings, because bankers ask two different questions. **They disagree, and they are meant
to. Show both; never average them.**

## 13.1 Commercial Opportunity Score — *where is the largest opportunity?*

```
commercial_opportunity_score = 0.45 × gap_percentile_within_product
                             + 0.30 × confidence
                             + 0.25 × (1 − share)         commercial headroom
```

- **Percentile rather than raw rand**, so a trillion-rand revenue base cannot dominate on scale
  alone and the five different estimate bases stay comparable.
- **Confidence sits inside the score**, so a large opportunity resting on an imputed denominator
  ranks below a smaller one built from disclosures.
- **Headroom is neutral at 0.5 where no share exists**, rather than rewarding or penalising a
  number that does not exist. A client where Syn Bank already handles most of the addressable
  activity is a retention conversation, not a growth opportunity.

## 13.2 Opportunity Intensity — *where are we most obviously absent?*

```
opportunity_intensity = opportunity_zar / addressable_cash_flow_zar
```

**No weights and no fitted coefficients at all.** One denominator per client — the client's own
Addressable Cash Flow — which is identity-anchored, available for all twenty, and identical
across the five pillars, so a client's five intensities are directly comparable to each other.
This is what lets a small company with a proportionally enormous gap outrank a giant with a
proportionally small one, which a within-product percentile cannot do.

## 13.3 Why both exist

| | Commercial Opportunity Score | Opportunity Intensity |
|---|---|---|
| Question | Where is the largest, best-evidenced opportunity? | Where is Syn Bank most disproportionately absent? |
| Denominator | Percentile within the product | The client's own Addressable Cash Flow |
| Includes confidence | Yes, 0.30 weight | No — it is a pure ratio |
| Biased toward | Large, well-evidenced accounts | Small clients with outsized gaps |

**The disagreement is the point.** Shaftesbury Capital plc's lending position is intensity rank
1 (3.86× its entire annual operating cash flow — the most disproportionate position in the
portfolio) and commercial rank 67, because the rand amount is small next to Glencore's. Both
rankings are right about different questions, and a banker with only one of them would miss one
of the two clients.

## 13.4 A known degeneracy, stated

**Opportunity intensity is near-degenerate for cash management**, where it reduces to `1 − share`
exactly and every client scores 0.91–1.00. The cross-pillar intensity leaderboard is therefore
dominated by cash by construction and **must not be used as a prioritisation tool**. Use
`intensity_rank_in_product`, or filter to one pillar.

Similarly, **the cash ranking is close to a size ranking** — the honest consequence of an
identity-anchored denominator, since the biggest payment flows really are the biggest
opportunities. The ordering carries little client-specific information beyond scale, and a
`product_ranking_tracks_company_size` diagnostic says so in the data.

## 13.5 The commercial intelligence layer

Stage 4 is a **deterministic semantic layer** over the contract. No LLM, no new numbers; every
sentence is a template filled from a published field.

**Primary opportunity selection** is not "the biggest rand number":

```
selection_score = commercial_opportunity_score
                × role_weight            CORE 1.00 · SUPPORTING 0.85 · SIGNAL_ONLY 0.55
                × confidence_weight      HIGH 1.00 · MEDIUM 0.80 · LOW 0.55
                × (1 − 0.20 if a HIGH-severity diagnostic is open)
                × (1 − 0.10 if the estimate is benchmark-sensitive)
```

Each client gets a primary, a secondary and a supporting signal.

**Opportunity status:**

| Status | Requirement |
|---|---|
| `PRIORITY` | Score ≥ 0.65, **HIGH confidence**, headroom ≥ 5% of addressable, and no open HIGH-severity diagnostic |
| `INVESTIGATE` | Score ≥ 0.45 |
| `MONITOR` | Everything else. A `SIGNAL_ONLY` pillar can rise no higher than this, by construction |
| `NO_HEADROOM_DEMONSTRATED` | Headroom below 5% of the addressable figure |

**`PRIORITY` requires HIGH confidence.** No combination of size and score promotes a LOW- or
MEDIUM-confidence row. The only route is a named entry in `config.PRIORITY_OVERRIDES` with a
written reason; **the shipped registry is empty**, and tests assert both the rule and the
emptiness. This is what stops a large, weakly evidenced FX number becoming a call-list item.

**Cash management wins the primary slot for 19 of 20 clients.** This is honest, not a defect: it
is the only pillar with HIGH confidence across the portfolio. The layer says so out loud in its
`primary_concentration` section and directs the reader to the secondary slot for client-specific
differentiation. The selection score is not re-weighted to manufacture variety.

---

# 14. Sensitivity Analysis

The engine was **rebuilt 36 times, end to end** — not perturbed, not approximated. Each run
produced a full set of 100 estimates; the sweep measures what moved.

## 14.1 The grid

| Knob | Values tested | What it decides | Published value |
|---|---|---|---|
| `benchmark_percentile` | **median (0.50)** · **P75 (0.75)** · **P80 (0.80)** | The peer statistic that defines a well-penetrated peer | 0.75 |
| `leave_one_out` | **True** · **False** (self-inclusive) | Whether the estimated client is excluded from the population that sets its own coefficient | True |
| `benchmark_scope` | **sector_preferred** · **portfolio_only** | Whether a sector benchmark may be formed at all | sector_preferred |
| `capex_debt_funded_share` | **0.20** · **0.30** · **0.40** | The one underived coefficient in the engine | 0.30 |

3 × 2 × 2 × 3 = **36 full model runs**, 3,600 rows of sensitivity output. Base scenario:
`p75_loo_sector_capex30`.

## 14.2 Verdicts

Ranges below are the **portfolio opportunity total** (`total_gap_zar`) across all 36 runs.

| Pillar | Verdict | Worst rank correlation | Worst total drift | Lowest – highest opportunity total | Ratio |
|---|---|---:|---:|---|---:|
| Transactional / Cash Management | **`ROBUST`** | 1.000 | 0% | R14.25tn – R14.25tn | **1.0×** |
| Lending | **`ROBUST`** | 0.997 | 5% | R1.32tn – R1.44tn | **1.1×** |
| Trade Finance | **`ASSUMPTION_SENSITIVE`** | 0.848 | 66% | R39.28bn – R157.64bn | **4.0×** |
| FX / Global Markets | **`ASSUMPTION_SENSITIVE`** | 0.510 | 77% | R78.37bn – R583.70bn | **7.4×** |
| Investment Banking | **`NO_RAND_MAGNITUDE_ORDERING_ROBUST`** | 1.000 | no rand figure | — | — |

## 14.3 What each verdict permits

- **Cash Management — quote the rand figure.** Both coefficients are accounting identities, so
  there is nothing in the grid to turn. 0% drift across all 36 runs. **20 of 20 client estimates
  flagged `STABLE`.**
- **Lending — quote the rand figure, naming the capex component.** Moving the one judgement
  coefficient by a third in either direction moves the total under 5%.
- **FX and Trade — quote the rank and the range, never a point estimate.** With no disclosed
  total to anchor to, the choice of peer statistic *is* the answer. **18 of 20 FX estimates are
  flagged `SENSITIVE`.**
- **Investment Banking — quote the ordering only.** There is no rand figure to be sensitive, and
  the ordering is unchanged across every run.

## 14.4 What survives regardless

**The identity of the top ten opportunities: 9 or 10 of 10 survive every one of the 36
scenarios** (mean 9.7). The list a banker would actually work is stable even where the rand
amounts on it are not.

The dominant lever on FX and trade is `benchmark_percentile`, exactly as expected when the
denominator is the coefficient. `capex_debt_funded_share` is the only knob that touches lending.
Nothing touches cash management.

## 14.5 How sensitivity is surfaced, not hidden

Every rand estimate published by the commercial intelligence layer carries `estimate_low`,
`estimate_base`, `estimate_high`, `estimate_range_pct`, `rank_base`, `rank_swing`,
`rank_stability` and `sensitivity_flag` — and **the wording of the generated explanation changes
with the flag.**

Classification thresholds: `(high − low) / base` ≤ 0.10 → `STABLE`, ≤ 0.35 → `MODERATE`, above →
`SENSITIVE`. Within-pillar rank swing ≤ 2 → `STABLE`, ≤ 5 → `MODERATE`, above → `SENSITIVE`.

The dashboard renders this as a **range mark**: a moving figure is drawn as a band, a fixed
figure as a lone dot. Cash is a dot. FX and trade are wide bands.

---

# 15. Validation

## 15.1 Automated tests — 523, all passing

| Suite | Tests | What it protects |
|---|---:|---|
| `test_copilot.py` | 137 | Router, retrieval, context, prompts, validation, fallback, audit, demos |
| `test_copilot_adversarial.py` | 70 | Prompt injection, unknown clients, requests for a portfolio total, requests to compute |
| `test_intelligence.py` | 58 | Selection, status rules, explanations, questions, forbidden phrases |
| `test_wallet_outputs.py` | 46 | The published contract tables and their column guarantees |
| `test_wallet_model.py` | 42 | Pillar models, components, sector rules, confidence |
| `test_api.py` | 39 | Dashboard payloads and the cross-pillar guard |
| `test_opportunity_engine.py` | 35 | Rankings, classification, NULL policy |
| `test_benchmarks.py` | 21 | Leave-one-out, sample floors, sector fallback |
| `test_sensitivity.py` | 18 | The 36-run sweep and its determinism |
| `test_feature_pipeline.py` | 18 | Stage 2 end to end |
| `test_fx.py` | 13 | Conversion basis, rate derivation, cross-checks |
| `test_internal_features.py` | 13 | Internal aggregations across all four scopes |
| `test_external_features.py` | 11 | Status handling, NULL preservation, identities |
| `test_clean_data.py` | 2 | Stage 1 (full-data tests auto-skip when the CSVs are absent) |

## 15.2 Reconciliation

- **Feature layer:** 49 validation checks run inside `build_features`, recorded in
  `feature_report.json`. Entity counts, join integrity, required non-NULL columns, FX
  cross-checks.
- **FX:** derived rates reproduce all 51 `OK` rows of `fx_rates_fy_window.csv` to within
  5×10⁻⁴ ZAR and agree with 27 client-published rates to within a 1.5% tolerance.
- **External financials:** the wide file reconciles to the normalized store with zero
  discrepancies across all 340 numeric cells.
- **Cleaning:** `quality_report.json` records source rows, duplicates removed, conflict groups
  retained, and exact `DECIMAL(30,16)` totals before and after.
- **The notebook re-derives and asserts.** §4.3 recomputes FX conversions from the published
  rate and asserts them against the stored feature layer to within R1; §12 recomputes
  `input_quality` from the four factors and asserts `input_quality × evidence_directness ==
  confidence` for all 100 estimates.

## 15.3 Determinism

The engine is deterministic: **identical inputs produce byte-identical outputs**, and tests
assert it for the estimates, the contract tables and the sensitivity sweep. The copilot decodes
at `temperature = 0.2`, `top_p = 0.95`, `seed = 42`, non-streaming — a banker asking the same
question twice must get the same answer.

## 15.4 Benchmark exclusion

Tests assert directly on the *populations*, not merely on the outputs, that no client appears in
the population that sets its own coefficient. The published `model_benchmarks.parquet` carries
`leave_one_out` and `self_in_population` on all 120 rows, and the notebook re-asserts
`not self_in_population.any()`.

## 15.5 NULL handling

**A product with no defensible rand denominator keeps NULL in every rand column. Never zero.**
*We cannot size this* and *this is worth nothing* are opposite statements, and a `fillna(0)`
anywhere downstream would merge them. `contract.NO_RAND_DENOMINATOR` enforces it at build time;
tests assert it; the dashboard's charting is configured not to default missing to zero.

The same principle runs back through the pipeline: an external cell with `status ≠ OK` stays
NULL and carries its reason; a suppressed trade sub-model is NULL, not zero; a component whose
driver was not disclosed is NULL, not zero.

## 15.6 Dashboard and API contract

- **The browser never calculates.** `api/service.py` projects published columns and formats
  every rand figure server-side. The moment a front end derives a currency value it can disagree
  with the model, and the model is the one under audit.
- **`_assert_no_cross_pillar_total()` runs on every payload**, not just in tests. No page can
  show a total across the five pillars.
- The service layer reads only the two contract tables and the stage-4 projections. Adding a
  column upstream cannot break a page; removing one breaks a test first.
- No build step, no CDN, no webfont — the dashboard runs with no network.

---

# 16. GenAI Architecture

## 16.1 The pipeline

```
question
  ─► router       classify intent, resolve client / product / sector      DETERMINISTIC
  ─► retrieval    filter and rank in pandas over seven published tables   DETERMINISTIC
  ─► context      render selected rows; enumerate EVERY figure in them    DETERMINISTIC
  ─► LLM          write prose over that context, and nothing else         GENERATIVE
  ─► validation   reject any answer with an unsupported figure or claim   DETERMINISTIC
  ─► audit        record the whole chain; secrets never written           DETERMINISTIC
  ─► answer
```

Every step before the model is deterministic, and every step after it is a check.

> **The deterministic engine remains the financial source of truth. The LLM does not calculate
> financial figures — it explains validated analytical results.**
>
> The model never sees a raw dataset, never performs arithmetic, and never decides a ranking. If
> the LLM ranked, the ranking would be unverifiable and would silently disagree with the numbers
> printed beside it.

## 16.2 Retrieval and context generation

Retrieval filters and ranks **in pandas** over seven stage 3–4 tables before the model is
invoked: client intelligence, pillar rows, explanations, banker questions, sensitivity,
diagnostics and the portfolio view. The router resolves intent, client, product and sector from
the question text against a known vocabulary.

Context size is controlled at source rather than by truncating a blob. Caps: 9,000 tokens hard
ceiling, 8 clients, 12 product rows, 4 questions, 6 diagnostics, 20 portfolio rows. Truncation,
when it happens, is reported on the bundle.

**An article is never a client name.** `The Bidvest Group` indexes on `bidvest`. Indexing on
`the` made E18 match nearly every question and poisoned every downstream retrieval — the bug is
fixed and tested, and a naive first-token index must not be reintroduced.

## 16.3 Provider

| | |
|---|---|
| Primary | **DeepSeek**, `deepseek-chat`, `https://api.deepseek.com`, key `DEEPSEEK_API_KEY` |
| Fallback | **NVIDIA NIM**, `z-ai/glm-5.2`, `https://integrate.api.nvidia.com/v1`, key `NVIDIA_API_KEY` |
| Decoding | temperature 0.2 · top_p 0.95 · seed 42 · non-streaming |
| Output ceiling | 16,384 tokens, one retry at 32,768 |
| Timeout | 240s |

Both providers speak the OpenAI chat-completions protocol, so the client code is identical and
only the endpoint, key and model name differ — adding a third is a data change, not a code
change. `deepseek-chat` is the default because this stage only *writes* prose; the reasoning was
already done by the deterministic layers. A reasoning model is fully supported
(`SYN_COPILOT_MODEL=deepseek-v4-flash`) and measurably improves rule adherence, but pays for
capacity this architecture deliberately does not need.

Non-streaming is a design choice, not an omission: **validation needs the whole answer before
any of it is shown.**

**Secrets never touch disk.** The key is read from the environment at call time, never written
to the audit log and never returned in a result object. `audit._assert_no_secret` inspects every
record before writing, and a test asserts it. `.env` is gitignored; `.env.example` is the
committed template and holds no real values.

## 16.4 Hallucination and claim controls

**A prompt is a request. A check is a guarantee.** Everything in the system prompt can be
ignored by a model having a bad day, so every answer is inspected before a banker sees it.

**Rejected — the answer is discarded:**

| Control | Detail |
|---|---|
| **Unsupported figures** | Every rand amount and percentage in the answer must appear in an **allow-list built while the context was rendered**. Matching has rounding tolerance (`R278.7bn` is supported by `R278.72bn`) but cannot admit a figure that rounds to nothing in the context |
| **Cross-pillar totalling in digits** | Fails automatically, because upstream never produced a cross-pillar total — so the number cannot be in the allow-list, however plausible it looks |
| **Cross-pillar totalling in words** | `"total across all pillars"`, `"combined opportunity across"`, `"total wallet across"`, `"aggregate opportunity of"` |
| **Asserted forbidden claims** | The same 16-phrase blocklist as the deterministic layer, imported from `intelligence.config` so the two cannot drift apart. *Asserted*, not merely mentioned — a sentence denying the claim is exactly what the prompt asks for |

**Warned — logged and surfaced, answer kept:** share-of-wallet language near lending or
investment banking (a proximity heuristic that cries wolf on correct methodology sentences); a
rand figure in a sentence mentioning investment banking; first-person calculation tells.

The split is the whole design. Discarding an answer is expensive — the banker loses fluent prose
and gets a template — so rejection is reserved for what would make the answer **wrong**.

**A rejected answer is discarded, not patched.** The banker gets the deterministic fallback and
a notice saying why, and the violation goes into the audit log — so a reviewer can see how often
the model misbehaved rather than having to trust that it did not.

## 16.5 Fallback

The copilot **works with no API key at all**, because judging happens on someone else's laptop.

| Mode | When | What the banker is told |
|---|---|---|
| `llm` | Generated and validated | Nothing — the answer stands |
| `demo_stored_response` | No key, but the question matches a prepared demo | "Demo response — a stored answer generated from the same analytical outputs" |
| `fallback_no_api_key` | No key and no stored answer | "Demo / AI unavailable — assembled deterministically. Every figure is real; only the prose is templated" |
| `fallback_service_error` | Genuinely unreachable: no network, DNS failure, refused key, 5xx, rate limit, timeout | "The language-model service could not be reached" |
| `fallback_answer_truncated` | The service **answered** but ran past its output limit | "The answer ran past its output limit" — a distinct mode, because telling a judge the service was unreachable when it responded in 40 seconds is the kind of wrong that costs more than the missing prose |
| `fallback_validation_failed` | The answer contained an unsupported figure or a forbidden claim | "The generated answer contained a figure or a phrase that is not supported by the retrieved context, so it was discarded. The rejection is recorded in the audit log" |

`--demo` is an explicit flag (`SYN_COPILOT_DEMO=1`), not "unset the key": `copilot/config.py`
loads `.env` at import, so anything popped would be put straight back.

Stored demo answers record a digest of the context they were generated against. If the analytical
outputs have since changed, the notice says so rather than quietly glossing over it.

## 16.6 Adversarial testing

`tests/test_copilot_adversarial.py` (70 tests) and `analysis/adversarial_suite.py` cover prompt
injection, questions about clients that do not exist, requests for a portfolio total, requests
to compute something new, requests to convert a flow into a fee, and attempts to elicit
competitor-ownership language. Results are recorded in `docs/ADVERSARIAL_QA_REPORT.md`.

---

# 17. Limitations

## On what the figures mean

- **Cash management publishes Addressable Cash Flow, not a fee wallet.** It is the client's own
  annual operating turnover. The fee income a bank would earn on it is **not estimable** from
  this data and is published as a permanently NULL column. **No pricing exists anywhere in this
  repository and none may be inferred** — no rates config, no economics config, no fee schedule.
- **A gap is addressable activity not observed in Syn Bank's supplied data.** It is never
  evidence that a competitor holds it.
- **The five pillars are not additive and there is no portfolio total.**

## On the estimates

- **FX and Trade are peer-benchmark estimates, not disclosed totals.** No disclosure states
  either activity's true size, so the denominator *is* the coefficient.
- **FX and Trade are assumption-sensitive** — their portfolio opportunity totals span 7.4× and
  4.0× across the grid. Rank them; state the range; never quote a point estimate.
- **If the whole portfolio is under-penetrated, every peer-benchmark wallet is understated
  together**, and no amount of internal data would reveal it. The benchmark client is near 100%
  share by construction. The portfolio median FX and trade shares of 26.1% and 18.9% are more
  likely to indicate a denominator that is too small than a bank that holds a quarter of every
  client's cross-border business.
- **Lending has no observed Syn Bank numerator**, so no share of wallet exists and none is
  computed. What is published is a financing-need indicator built from disclosed debt structure.
- **Investment Banking is signal-only** — a ranked mandate likelihood and a category. No rand
  amount, no share.

## On the inputs

- **`capex_debt_funded_share = 0.30` is the one underived coefficient in the engine.** Moving it
  by a third in either direction moves the lending total under 5%, and a diagnostic fires
  wherever it drives more than half of a client's estimate.
- **Nine of twenty clients report in a currency other than ZAR** and are converted at SARB rates
  averaged over each entity's own fiscal-year window. A different FX basis would move their
  figures; the three prepared rate sources agree to within 0.85%.
- **Fiscal year ends span nine months across five distinct dates**, so "the fiscal year" is not
  one window across the portfolio, and cross-client rand comparisons carry that caveat.
- **Three clients fail the revenue-split identity and several disclose an unverifiable zero
  debt.** Both are carried through as flags rather than corrected.
- **Four clients disclose no cost of sales**, so their supplier-payment and import components
  rest on a peer ratio applied to revenue.
- **`total_assets`, `equity` and `total_liabilities` do not exist in the data**, so the
  balance-sheet identity cannot be checked for any entity.

## On the metrics

- **Opportunity Intensity is near-degenerate for cash management**, where it reduces to
  `1 − share` and every client scores 0.91–1.00. Use `intensity_rank_in_product`.
- **The cash ranking is close to a size ranking** — the honest consequence of an
  identity-anchored denominator. A `product_ranking_tracks_company_size` diagnostic says so in
  the data.
- **Three fields in the source data are generation artefacts** and are deliberately not
  modelled: day-of-week distribution, channel mix, and `commodity_or_contract_type`.

---

# 18. Reproducibility

## 18.1 Environment

```bash
git clone <repo> && cd "Standard Bank Hackathon"
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Python 3.11+ (`StrEnum` is used in the configuration layer); developed and tested on 3.14. Core dependencies: `duckdb`, `pandas`, `pytest`. Optional per stage: `openai`
(stage 5), `fastapi`/`uvicorn`/`httpx` (stage 6), `matplotlib`/`ipykernel`/`jupyterlab` (the
notebook). The full test suite passes without any of the optional groups.

## 18.2 Data pipeline

```bash
tar -xzf data/data.tgz -C data/                                   # restore the 3 raw CSVs
.venv/bin/python -m src.syn_wallet.clean_data --overwrite         # stage 1  cleaning
.venv/bin/python -m src.syn_wallet.build_features --overwrite     # stage 2  feature layer
```

## 18.3 Wallet model and sensitivity

```bash
.venv/bin/python -m src.syn_wallet.build_wallet --overwrite --sensitivity   # stage 3
```

`--sensitivity` rebuilds the engine 36 times (a few seconds). Drop it for a fast model rebuild;
the sensitivity Parquet files are then left untouched rather than regenerated.

## 18.4 Commercial intelligence

```bash
.venv/bin/python -m src.syn_wallet.build_intelligence --overwrite           # stage 4
```

## 18.5 Generated reports

```bash
.venv/bin/python -m analysis.wallet_model_report              # -> docs/MODEL_REPORT.md
.venv/bin/python -m analysis.model_sensitivity_report         # -> docs/MODEL_SENSITIVITY.md
.venv/bin/python -m analysis.model_final_report               # -> docs/MODEL_FINAL_REPORT.md
.venv/bin/python -m analysis.commercial_intelligence_report   # -> docs/COMMERCIAL_INTELLIGENCE_REPORT.md
.venv/bin/python -m analysis.genai_prompts_report             # -> docs/GENAI_PROMPTS.md
.venv/bin/python -m analysis.genai_design_report              # -> docs/GENAI_DESIGN.md
```

## 18.6 Copilot and dashboard

```bash
export DEEPSEEK_API_KEY=...                                   # optional; omit to run offline
.venv/bin/python -m src.syn_wallet.build_copilot_demos --overwrite
.venv/bin/python -m src.syn_wallet.serve                      # http://127.0.0.1:8000
.venv/bin/python -m src.syn_wallet.serve --demo               # force deterministic answers
```

## 18.7 Tests

```bash
.venv/bin/python -m pytest                                    # 523 tests
```

## 18.8 The notebook

```bash
.venv/bin/jupyter lab SynBank_Share_of_Wallet_Analysis.ipynb
```

The bootstrap cell at the top of the notebook restores the raw CSVs and runs stages 1–4 if their
outputs are missing, so **opening the notebook on a clean clone and choosing *Run All* is
sufficient.** Nothing is rebuilt if it already exists.

`run_pipeline` raises `FileExistsError` when outputs exist unless `--overwrite` is passed.

---

# 19. Final Interpretation

## What a banker should take from this

**1. The size of the account is not the size of the relationship, and now both are visible.**
A banker looking at Glencore previously saw R2.50bn of activity with no way to tell whether that
was most of the account or a rounding error. It is 0.03% — against R8.75tn of operating turnover
the client must move through *some* bank. That single ratio changes what the meeting is about:
not "are you happy with the service", but "where does the rest of your settlement run, and what
would have to change".

**2. Know which number to say out loud, and which to say as a range.**
The cash figure rests on an accounting identity and does not move across 36 model configurations
— quote it. The FX and trade figures span 7.4× and 4.0× — rank them, quote the range, and name
the benchmark population. Every estimate carries its confidence band and its range, so the
banker never has to guess which kind of number they are holding.

**3. "We have no lending relationship" is not an absence of information.**
There is no loan book in the data, so no share is computed. But the disclosed debt structure
sizes the financing decisions falling inside twelve months, and 4,184 memo lines are *observed*
evidence of facility drawdowns and syndicate settlements moving through Syn Bank accounts on
credit Syn Bank did not extend. That is a specific, evidenced reason to have a conversation
about a specific facility at a specific renewal.

**4. Two rankings find two different client lists.**
Shaftesbury Capital sits at commercial rank 67 and intensity rank 1, with a financing need 3.86×
its annual turnover. A banker working only the commercial ranking would never reach it; a banker
working only intensity would over-weight it against genuinely larger opportunities. The coverage
decision is better for having both, and neither is averaged into the other.

**5. Ask a better question, not a longer one.**
Every selected opportunity ships with the questions that follow from it, generated from the same
fields — so the number on the page and the question in the meeting cannot drift apart. The
copilot writes the briefing; the engine owns every figure in it.

## Why the refusals are the reason to trust the rest

The engine will not convert a flow into a fee, because no pricing exists. It will not total five
incommensurable pillars, because two of them overlap by an amount the data cannot resolve. It
will not call a gap competitor-held, because a gap is an absence of evidence rather than evidence
of an absence. It will not let a language model invent a figure, because every figure the model
writes must trace back to a number the deterministic engine produced. And it will not promote a
large, weakly evidenced opportunity to `PRIORITY`, because no combination of size and score can
outvote a LOW confidence band.

**A model that will not say anything it cannot defend is a model a banker can take into a client
meeting.**
