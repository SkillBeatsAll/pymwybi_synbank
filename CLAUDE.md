# CLAUDE.md — The Wallet Twin (Syn Bank Share of Wallet Intelligence Engine)

**This file was corrected on [DATE] after a repo audit found the previous version described an
aspirational file layout that didn't match what's actually committed.** Everything below reflects
what is really in the repo, verified by reading the actual files, not assumed from an earlier
plan. If you're Claude Code picking this up: trust this file's "What's actually in the repo"
section over any code comments or prior conversation that contradicts it.

Team: **"Put your money where your byte is"** — Vihan Allan, Joel Cedras, Viajul Moodley
("Via"), Rahul Maharaj.

Current focus: **Person B (external financials / GenAI) and Person D (dashboard / product)**.
Person A/C's cleaning + modelling scope is referenced below for full context, but is not what
you're being asked to drive right now.

---

## 1. The competition

**Standard Bank Data School Hackathon 2026** — "Syn Bank Share of Wallet Intelligence
Challenge." Submission deadline is the end of the hackathon window — treat 6pm on the final day
as the real deadline, not 11:59pm, to leave buffer for upload issues.

**Syn Bank** is a fictional South African corporate/investment bank with 20 JSE-listed
corporate clients (the brief text says 50 — the supplied data has 20; go with the 20 actually
in the data). It is never the client's sole bank. The task: figure out how much of each
client's *total* banking activity Syn Bank actually captures, and where the rest is going.

### The three core questions the brief asks
1. What proportion of the wallet is Syn Bank currently capturing?
2. Rank the most attractive revenue growth opportunities (gap between total wallet and current
   share).
3. Show a meaningful GenAI use case — not cosmetic.

### Required deliverables
- Reproducible Python notebook (ingest → transform → model → visualise)
- Documented methodology (assumptions, wallet-sizing logic, limitations)
- Evidence of GenAI integration (prompts, workflow, code)
- `requirements.txt` / reproducible environment
- Streamlit dashboard: portfolio summary, client drill-downs, opportunity heatmap, AI briefing
  notes for ≥3 clients
- 1-page PDF explaining the solution
- PowerPoint for the judging panel (problem, methodology, AI component, results, next steps)
- Team name + members on every document. One submission per team.

### Scoring
| Criterion | Weight |
|---|---|
| Business Insight & Commercial Acumen | 40% |
| Analytical Rigor | 30% |
| Gen AI Application | 20% |
| Presentation & Storytelling | 10% |

Judges explicitly probe methodology in Q&A — see section 9, have those answers ready.

---

## 2. The design decision that defines this solution — READ THIS FIRST

**We were never given fee rates, pricing, spreads, or revenue data — not in the internal
datasets, not in the brief.** We do not invent them. A model that multiplies flows by an
assumed "15 basis points" is presenting a made-up number as an answer and will not survive
Q&A.

Instead we measure Share of Wallet as **Rands of client banking activity on both sides**:

```
Share of Flow = Activity observed through Syn Bank ÷ Total addressable activity
                implied by the client's own audited financial statements
```

Every input traces to either an audited financial-statement figure or an observed internal
transaction. There is no unsourced rate anywhere in the model. Lead with this in the
presentation — it's the strongest, most defensible thing about the submission.

---

## 3. What's actually in the repo (verified, not assumed)

### Raw internal data — `data/data.tgz` (68MB, committed)

**All three internal datasets exist.** They are not missing — they're compressed together in
`data/data.tgz` and need extracting into `data/` (that's the path `clean_data.py` and
`.gitignore` expect — **not** `data/raw/`, which doesn't exist in this repo).

```bash
tar -xzf data/data.tgz -C data/
```

Contains: `trade_finance.csv`, `transactional_banking.csv`, `cross_border_payments.csv`.

| Dataset | Rows | Date range | Entities / sectors |
|---|---|---|---|
| Transactional banking | 2,802,875 | 2023-07-01 to 2026-06-30 | 20 / 7 |
| Cross-border payments | 241,117 | 2023-07-01 to 2026-06-30 | 20 / 7 |
| Trade finance | 20,303 | 2023-07-01 to 2026-06-30 | 20 / 7 |

Sectors: `consumer, industrials_pharma, insurance, mining, real_estate, tech, telecoms`.

A **full data audit already exists**: `data_analysis.md`. Read it before touching the raw data
— it documents every data-quality issue below with exact counts, so don't re-derive these from
scratch.

### Known data-quality issues (already documented in `data_analysis.md`)
- Transactional currency casing inconsistent: `ZAR` vs `zar`.
- Duplicate-ID groups with **genuinely conflicting payloads** (can't be auto-deduped by keeping
  "latest"): 42,535 in transactional, 297 in cross-border, 3 in trade finance.
- Transactional SWIFT rows and cross-border payment rows **cannot be reconciled** against each
  other — no exact matches on entity/date/direction/amount/beneficiary/reference. Never sum
  them together.
- Intercompany sweeps (~R201bn) dominate raw transactional volume — this is internal treasury
  movement, not revenue-generating activity, and must be excluded from the cash_mgmt observed-
  flow numerator.
- Never sum transactional + cross-border + trade-finance into one "total flow" — they're three
  separate measures (cash-flow volume, cross-border volume, trade-finance exposure/stock).

### Cleaning pipeline — `src/syn_wallet/clean_data.py`

**Not** `src/clean_layer0.py` — that path doesn't exist in this repo. The real pipeline is
DuckDB-based, fully built and tested:

```bash
python -m src.syn_wallet.clean_data --overwrite
python -m pytest    # includes tests/test_clean_data.py — runs real files through
                     # full reconciliation with exact expected row counts
```

What it does:
- Removes exact duplicate canonical rows only
- Uppercases currency codes
- **Flags** (does not quarantine to a separate file) identifier-conflict rows via a
  `has_identifier_conflict` boolean column — this differs from an earlier plan that described
  quarantining to `*_quarantine.csv`; the flag-column approach is what's actually implemented
- Outputs typed, ZSTD-compressed **Parquet** (not CSV) to `data/processed/` (gitignored — not
  yet generated in a fresh checkout; run the pipeline to produce it)

**Status: pipeline is built and tested, but has not yet been run in the current working tree.**
Run it first, confirm `pytest` is green, before building anything downstream.

### External financial statements — `data/finances/`

This is considerably further along than a from-scratch build, and does **not** use an xlsx
extraction workbook — it's already a clean set of CSVs:

| File | What it is |
|---|---|
| `entities.csv` | 20 entities — confirms BHP/Glencore/Anglo American report in USD, NEPI Rockcastle in EUR, Shaftesbury Capital in GBP, and Prosus/Naspers/Vodacom have March year-ends (FY2026 latest) |
| `external_financials_normalized.csv` | 380 rows (20 entities × 19 fields), long format, with `status` (`OK`/`NOT_DISCLOSED`), `source_doc`, `source_url`, `source_ref`, `extraction_note`, `source_reliability` per row |
| `external_financials_wide.csv` | Same data, pivoted wide — one row per entity |
| `data_dictionary.csv` | 21 fields total (19 in `external_financials` + `avg_zar_rate`/`closing_zar_rate` in `fx_rates`) |
| `fx_rates_sarb_daily.csv` | 2,710 rows — daily SARB FX rates |
| `fx_rates_normalized.csv`, `fx_rates_fy_window.csv`, `fx_rate_crosscheck.csv` | A full FX conversion layer for the USD/EUR/GBP reporters |
| `data_quality_exceptions.csv` | 144 logged extraction fixes/exceptions with attribution |

**This FX rate layer is real SARB daily data, not a single per-entity rate pulled from an AFS
disclosure note.** That's a meaningfully more robust approach than picking one disclosed rate
per entity — use `fx_rates_fy_window.csv` to get the right average/closing rate for each
entity's actual fiscal year window (remember: March year-end entities are on a different window
than the rest).

### Known company-specific quirks (already correctly reflected in `entities.csv` — verify, don't re-derive)
- **Insurers (Sanlam, OUTsurance)** — IFRS 17, no conventional revenue/cost of sales/inventory.
  Addressable_trade = 0, drive Pillar 1 off gross written premium or total income.
- **BHP, Glencore, Anglo American** — USD reporters, no SA segment disclosed. Convert via the
  FX layer; flag geographic attribution as a stated limitation.
- **NEPI Rockcastle** — EUR reporter.
- **Shaftesbury Capital** — GBP reporter. `import_intensity = 0` — near-zero trade finance here
  is a finding, not a gap; don't rank it as an opportunity.
- **Prosus, Naspers, Vodacom** — March year-end, latest published is FY2026. Note the mixed
  fiscal-year basis in limitations.

### `requirements.txt` — currently minimal

Only has `duckdb` and `pytest`. **Needs expansion** before dashboard/notebook work can run:
`pandas`, `streamlit`, `plotly`, an LLM SDK (`anthropic` or `openai`), `numpy`, `scipy` (Monte
Carlo), `jupyter`/`nbformat` if the notebook is built locally.

### `.gitignore` — mid-edit, unstaged

Someone was expanding ignore rules for Python/Jupyter/Streamlit/OS cruft when this was last
checked. Confirm it's committed and covers `data/processed/` (Parquet output) and any
`.streamlit/secrets.toml` before adding API keys anywhere.

---

## 4. What does NOT exist yet (confirmed by directory listing)

- `notebooks/` — no notebook, no Layers 1–4 model
- `dashboard/` — **no Streamlit app at all**, not even a dummy-data placeholder
- `prompts/` — no GenAI prompts (extraction, competitor evidence, or briefing notes)
- `METHODOLOGY.md`, the 1-page PDF, the PowerPoint
- The locked hand-off files: `internal_features.csv`, `external_financials.csv` (in the exact
  contract schema below — `data/finances/external_financials_wide.csv` is NOT this file, it
  needs a small transform), `wallet_results.csv`
- The Monte Carlo uncertainty engine

---

## 5. The locked hand-off contract (schemas — do not rename columns)

**`internal_features.csv`**
`entity_id, entity_name, sector, pillar, observed_flow_zar, exposure_days, product_breadth, recency_days, trend_pct`
- Not started. Build from `data/processed/*.parquet` once the cleaning pipeline has been run.

**`external_financials.csv`**
`entity_id, entity_name, fy_label, field, value_zar, unit, confidence, source_page`
- Not started as this exact file. Needs a transform script: read
  `data/finances/external_financials_wide.csv` (or the normalized long version) + the FX rate
  layer (`fx_rates_fy_window.csv`), convert every USD/EUR/GBP figure to ZAR using the correct
  fiscal-year-window rate, and derive `confidence` from `status` + `source_reliability`
  (`OK` + high reliability → high confidence; `NOT_DISCLOSED` → 0). This is real, usable input
  data — the transform is genuinely just a schema/unit mapping, not an extraction problem.

**`wallet_results.csv`**
`entity_id, pillar, addressable_p10, addressable_p50, addressable_p90, observed, share_p50, unaddressed_p50, confidence, opportunity_score, rank`
- Not started. Output of the Layers 1–4 model (notebook, not yet built).

---

## 6. The model — five layers (full reference, needed even for B/D work to know what downstream consumers expect)

**Layer 0 — Governed data layer.** `src/syn_wallet/clean_data.py`, built and tested, not yet
run in this working tree.

**Layer 1 — Total addressable activity (external side).** Every driver is a published AFS line
item; only the *intensity* parameters are judgement calls (Monte-Carloed in Layer 3).

```
Pillar 1 — Cash Management & Payments (Rand, annual flow)
  Addressable_cash = revenue_total + cost_of_sales + capex + finance_costs

Pillar 2 — Trade Finance (Rand, annual flow)
  Addressable_trade = cost_of_sales × import_intensity        # import leg
                     + revenue_foreign × export_intensity      # export leg

Pillar 3 — FX / Global Markets (Rand, annual turnover)
  Addressable_fx = revenue_foreign
                  + cost_of_sales × import_intensity
                  + fx_forward_notional   # use directly where disclosed

Pillar 4 — Lending & DCM (Rand, CREDIT EXPOSURE — a STOCK, not a flow)
  Addressable_credit = gross_debt + undrawn_facilities
  competitor_held = portion attributable to lenders_named
```

**Layer 2 — Observed activity (internal side).** Symmetry rule: each pillar's numerator must be
in the same unit as its Layer-1 denominator. Exclude `intercompany_sweeps` from cash_mgmt. Use
cross-border only for FX, never combined with transactional SWIFT rows. Lending/DCM is
structurally unobservable in the internal data — flag as UNOBSERVABLE with low confidence,
never score it as zero share.

**Layer 3 — Share of Flow with uncertainty.** Triangular-distribution Monte Carlo (~10,000
iterations) over three declared structural parameters (`import_intensity`, `export_intensity`,
flow-inclusion factor), report P10/P50/P90.

**Layer 4 — Opportunity ranking.**
```
OpportunityScore = 0.5·norm(Unaddressed_P50) + 0.3·Confidence + 0.2·Propensity
```
Normalise `Unaddressed` **within each pillar** — pillars have very different magnitudes.

**Layer 5 — GenAI, three load-bearing uses (Person B owns this):**
1. **Statement extraction** — largely already done via `data/finances/*` (source citations
   exist as `source_url`/`source_ref`/`source_page`-equivalent columns). What's missing: turning
   the extraction *process* into a documented, reusable prompt for the `/prompts` GenAI-evidence
   deliverable.
2. **Competitor evidence** — named lender banks from AFS borrowings notes + SENS/DealMakers
   searches. Not started.
3. **Briefing notes** — 5–6 sentence call-prep note per client, grounded only in computed
   tables (no free generation — a judge will test this live for hallucination). Not started;
   this is what the dashboard's briefing-note button should eventually call.

### Sanity check to run once the model exists
Three years of collections + supplier payments across all 20 clients totals roughly R204bn
(~R68bn/yr) against the combined revenue of 20 JSE-listed majors — Share of Flow should land in
the **low single digits**. If any client comes out at 60%+ share, there's a bug — almost
certainly intercompany sweeps left in the numerator, or an annualisation error.

---

## 7. Role scope right now: Person B and Person D

### Person B — External data / GenAI
Owns: finishing the `external_financials.csv` transform (section 5), the FX conversion using
the real SARB rate layer, the extraction-pipeline documentation for `/prompts`, competitor-
lender evidence, and the grounded briefing-note prompt.
Must not get pulled into: the modelling maths (Layers 1–4) — that's Person C's.

### Person D — Product & Story
Owns: the Streamlit dashboard (currently doesn't exist — build the shell first, with three
tabs: portfolio summary, client drill-down, opportunity heatmap), charts, slides, the 1-pager,
`requirements.txt`, `README.md`, final submission packaging.
Must not get pulled into: model internals.

Since `wallet_results.csv` doesn't exist yet, **Person D should build the dashboard against a
clearly-labelled dummy file matching the exact `wallet_results.csv` schema** (section 5) so the
UI work isn't blocked on the model. Never let dummy numbers leak into a real slide, PDF, or
briefing note — label the dummy file unambiguously (e.g. `wallet_results_DUMMY.csv`) and flag
it visibly in the dashboard UI itself.

---

## 8. Immediate priorities, in order

1. **Extract `data/data.tgz` into `data/`** and confirm the three raw CSVs are present with
   the expected row counts from section 3.
2. **Run `src/syn_wallet/clean_data.py --overwrite`, then `pytest`.** Confirm green before
   building anything downstream — the pipeline is built and tested but not yet executed here.
3. **Expand `requirements.txt`** — add `pandas`, `streamlit`, `plotly`, `numpy`, `scipy`, an
   LLM SDK.
4. **Person B:** build the `external_financials.csv` transform from `data/finances/*` + the FX
   rate layer.
5. **Person B:** build `internal_features.csv` is actually Person A's job, but if it's blocking
   your GenAI/briefing-note work, coordinate rather than duplicating — check whether it exists
   before rebuilding it.
6. **Person D:** build the dashboard shell against dummy `wallet_results.csv`.
7. Layers 1–4 notebook (Person C), Monte Carlo, real `wallet_results.csv`.
8. Wire the dashboard to real output, build the three GenAI prompts, write
   `METHODOLOGY.md`/1-pager/deck last, from what's actually built.

---

## 9. Things never to do (hard rules)

- Never invent a fee rate, spread, or margin.
- Never sum transactional + cross-border + trade-finance into one number.
- Never treat active/issued guarantees or LCs as settled cash flows.
- Never treat a missing Syn Bank product as proof a competitor holds it — score it
  UNOBSERVABLE, not zero.
- Never impute a missing `counterparty_country`.
- Never auto-resolve a duplicate-ID conflict by keeping "latest" — use the
  `has_identifier_conflict` flag and route to a human decision.
- Never mix a stock (Pillar 4 credit exposure) into a flow total (Pillars 1–3).
- Never build a supervised ML model here — there's no ground-truth wallet label to train
  against.
- Never let a dummy/placeholder file (dashboard data, mocked responses) reach a slide, the PDF,
  or a briefing note without being clearly relabelled as real first.
- Never trust a file layout or status claim in this doc (or any prior conversation) over what's
  actually in the repo — if in doubt, `ls` and read the file.

---

## 10. Judge Q&A — have these answers ready

1. *"How do you get to a Rand wallet without knowing what the bank charges?"* → We deliberately
   don't guess a rate. We size total addressable banking activity from audited statements and
   measure what share flows through Syn Bank. Every opportunity is Rands of unaddressed client
   activity — Syn Bank can price that internally far better than we can guess it.
2. *"What are your assumptions, then?"* → Three structural parameters: import intensity, export
   intensity, and the flow-inclusion factor. All three are declared, all three are
   Monte-Carloed, every figure shown is a P10–P90 band.
3. *"How do you know a gap means a competitor has it?"* → We don't assume it. Gaps are labelled
   three ways: confirmed competitor-held (named lender in the borrowings note), likely
   unclaimed, or unobservable. Lending is entirely unobservable in the supplied data and carried
   at low confidence, never scored zero.
4. *"Why no machine learning?"* → No ground-truth wallet label to train against — a supervised
   model would fit noise and present it as prediction.
