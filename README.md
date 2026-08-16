# Standard Bank & SU Hackathon 2026

- Team: **Put your money where your byte is**
- Members: Vihan Allan, Joel Cedras, Viajul Moodley, Rahul Maharaj

## Quickstart — Docker (recommended)

The image restores the raw data and runs the whole pipeline at build time — cleaning →
features → wallet model (with `--sensitivity`) → commercial intelligence — so the container
starts instantly and only ever serves pre-built Parquet.

```bash
docker build -t syn-wallet .
docker run -p 8000:8000 syn-wallet
```

Open <http://localhost:8000>.

By default the image bakes in `.env` (including any copilot key you've set locally) so it can
generate live prose; without a key it falls back to demo/deterministic answers automatically,
so this is safe either way. To build a key-free image and pass the key only at run time:

```bash
docker build --build-arg BAKE_ENV=0 -t syn-wallet .
docker run -p 8000:8000 -e DEEPSEEK_API_KEY=... syn-wallet
```

## Quickstart — local Python

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt

tar -xzf data/data.tgz -C data/                               # restore raw inputs
.venv/bin/python -m src.syn_wallet.clean_data --overwrite     # stage 1: cleaning
.venv/bin/python -m src.syn_wallet.build_features --overwrite # stage 2: feature layer
.venv/bin/python -m src.syn_wallet.build_wallet --overwrite --sensitivity   # stage 3
.venv/bin/python -m src.syn_wallet.build_intelligence --overwrite          # stage 4
.venv/bin/python -m pytest                                    # 523 tests
.venv/bin/python -m analysis.feature_layer_walkthrough        # tour of the feature layer
.venv/bin/python -m analysis.wallet_model_report              # → docs/MODEL_REPORT.md
.venv/bin/python -m analysis.model_sensitivity_report         # → docs/MODEL_SENSITIVITY.md
.venv/bin/python -m analysis.model_final_report               # → docs/MODEL_FINAL_REPORT.md
.venv/bin/python -m analysis.commercial_intelligence_report   # → docs/COMMERCIAL_INTELLIGENCE_REPORT.md

# Stage 5, the copilot. Works without a key; set one for generated prose.
cp .env.example .env && $EDITOR .env          # add DEEPSEEK_API_KEY (optional)
.venv/bin/python -m src.syn_wallet.ask --list-models           # confirm the model name
.venv/bin/python -m src.syn_wallet.build_copilot_demos --overwrite   # stored demo answers
.venv/bin/python -m analysis.genai_prompts_report             # → docs/GENAI_PROMPTS.md
.venv/bin/python -m analysis.genai_design_report              # → docs/GENAI_DESIGN.md
.venv/bin/python -m analysis.adversarial_qa_report            # → docs/ADVERSARIAL_QA_REPORT.md
.venv/bin/python -m analysis.adversarial_qa_report --offline  #   ...without a key

# Stage 6 — the dashboard.
.venv/bin/python -m src.syn_wallet.serve                      # → http://127.0.0.1:8000

# The submission notebook. Its first cell runs stages 1–4 if their outputs are missing,
# so "Run All" on a clean clone is sufficient.
.venv/bin/jupyter lab SynBank_Share_of_Wallet_Analysis.ipynb
```

`--sensitivity` rebuilds the engine 36 times to price every arguable coefficient and takes a
few seconds; drop it for a fast rebuild of the model itself. `data/processed/` is gitignored —
every artefact above is regenerated from `data/data.tgz` plus `data/finances/`, so a clean
clone reproduces the whole pipeline.

## Submission deliverables

| File | What it is |
|---|---|
| [`SynBank_Share_of_Wallet_Analysis.ipynb`](SynBank_Share_of_Wallet_Analysis.ipynb) | The reproducible notebook: ingestion → transformation → modelling → visualisation → GenAI, in 19 sections. Imports the production modules and reads the published Parquet; nothing is re-implemented or hand-typed. |
| [`METHODOLOGY.md`](METHODOLOGY.md) | The formal technical and business methodology: assumptions, wallet-sizing logic, benchmarks, confidence, sensitivity, validation, GenAI architecture and limitations. |

Both describe the same engine as `docs/MODEL_FINAL_REPORT.md` and the dashboard — one
methodology, one analytical contract, one set of numbers. Every generated reference document —
the analytical contract, the sensitivity sweep, the copilot design and prompts, the data audit —
lives in [`docs/`](docs/README.md).

## Stage 1 — cleaning (`src/syn_wallet/clean_data.py`)

The raw CSV files in `data/` are immutable inputs. The cleaning pipeline writes typed,
ZSTD-compressed Parquet plus `quality_report.json` to `data/processed/`.

It removes only exact duplicate canonical records, standardises the transactional `currency`
code to uppercase **before** deduplicating, and retains records with conflicting identifiers,
marked with `has_identifier_conflict`. Nothing is imputed, rounded, or silently resolved.

Parquet is used because the raw transactional CSV alone is about 375 MB and is repeatedly
scanned during analysis — the output is compressed, typed, and much faster to query.

## Stage 2 — client feature layer (`src/syn_wallet/build_features.py`)

Turns the cleaned Parquet and the prepared external financials in `data/finances/` into a
modelling-ready table, one row per client.

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

### Stage 2 outputs (`data/processed/`)

| File | Grain | Contents |
|---|---|---|
| `client_features.parquet` | 20 rows × 279 columns | The modelling table: identity, external financials in ZAR, internal features per scope, trends, ratios |
| `client_master.parquet` | 20 rows | The client spine: identity, fiscal period, FX rates, external financials in native currency and ZAR |
| `external_financials_zar.parquet` | 380 rows (20 × 19) | Long store: native value, ZAR value, rate used, rate type, conversion basis, `status`, `basis`, `gap_reason` |
| `client_corridor_breakdown.parquet` | ~2,750 rows | Full counterparty-country and currency-pair distributions per client and scope |
| `feature_report.json` | — | Policy, FX crosscheck, coverage counts, and every validation result |

### FX policy

Nine of the twenty clients report in USD, EUR or GBP. Every external monetary value is
converted to ZAR before any ratio is taken, on one basis for all twenty clients: **SARB daily
mid-rates windowed by each entity's own fiscal year**, taken from `fx_rates_sarb_daily.csv` and
`entities.csv`.

- **Flow measures** (revenue, cost of sales, finance costs, capex) convert at the
  **fiscal-year average** rate.
- **Stock measures** (inventory, receivables, payables, debt, cash, facilities, FX notional)
  convert at the **fiscal-year-end closing** rate.
- ZAR reporters convert at exactly 1.0, basis `no_conversion`.

The rule lives in `config.FX_BASIS_BY_FIELD` and is asserted field by field in `tests/test_fx.py`.
The derived rates reproduce all 51 `OK` rows of `fx_rates_fy_window.csv` exactly and fill the
nine rows that file left blocked; they agree with the entities' own published rates to well
within 1.5%.

### Feature scopes

Metric suffixes mark the window a measure was aggregated over:

| Suffix | Window | Use |
|---|---|---|
| `_36m` | 2023-07-01 → 2026-06-30 | Full internal history |
| `_fy` | The client's own fiscal year | **The only scope divided by an external denominator** |
| `_r12m` / `_p12m` | Trailing years to 2026-06-30 / 2025-06-30 | Year-on-year trend |

All 20 fiscal-year windows fall inside the flow window, so no client is period-aligned against
partial internal data.

### What this stage deliberately does not do

- No share of wallet, total wallet, or competitor wallet is estimated.
- No fee, margin or basis-point assumption is applied. Syn Bank is fictional and has no
  disclosed pricing, so any rand of "revenue" would be invented.
- The transactional and cross-border pillars are never summed. 279,389 transactional rows sit
  on the `SWIFT` channel and conceptually overlap cross-border payments by an amount the
  supplied fields cannot resolve.
- No explained absence is imputed to zero. "Discloses zero debt" and "we could not find this
  client's debt" stay distinguishable.

## Stage 3 — wallet and opportunity engine (`src/syn_wallet/build_wallet.py`)

**Three Share of Wallet pillars and two opportunity signals.** Share of Wallet is a claim about
a denominator — *of the activity this client must transact somewhere, what fraction runs
through Syn Bank* — and only three pillars can support one. The other two publish opportunity,
never share.

The final, stable analytical contract is **[docs/MODEL_FINAL_REPORT.md](docs/MODEL_FINAL_REPORT.md)**:
methodology, terminology, every formula, the benchmark rules, both opportunity rankings, the
published schema, and what a dashboard may and may not show.
**[docs/MODEL_SENSITIVITY.md](docs/MODEL_SENSITIVITY.md)** prices every arguable coefficient
across 36 model runs. **[docs/MODEL_REPORT.md](docs/MODEL_REPORT.md)** carries the per-client
derivations and three worked examples. All three are generated from the outputs rather than
hand-written.

| # | Pillar | Role | Class | Estimate | Share? |
| --- | --- | --- | --- | --- | --- |
| 1 | Transactional / Cash Management | share of wallet | CORE | Addressable Cash Flow: revenue + cost of sales (accounting identity) | yes |
| 2 | FX / Global Markets | share of wallet | CORE | Exposure × peer settlement intensity + disclosed hedging | yes |
| 3 | Trade Finance | share of wallet | CORE | Import + export documentary + guarantees, sub-modelled | yes |
| 4 | Lending | opportunity signal | SUPPORTING | Refinancing + undrawn + working capital + capex funding | **no** — Syn Bank has no loan book |
| 5 | Investment Banking / Capital Markets | opportunity signal | SIGNAL_ONLY | Ranked mandate-likelihood signal, no rand amount | **no** |

The CORE / SUPPORTING / SIGNAL_ONLY class is **assigned by measurement at build time**, not
hardcoded, and published in `product_classification.parquet` so the application layer reads it
from the data.

`addressable_cash_flow_zar` (the client's own operating turnover) and
`cash_management_wallet_zar` (the fee income a bank would earn on it, **NULL for every client,
never derived** — no disclosed pricing) are kept as two separate columns so the flow figure
can never be misread as bank revenue.

Where no accounting identity fixes a coefficient it is measured from the client's peers at the
75th percentile — **with that client removed from the population**, so a heavily penetrated
client can't inflate the benchmark it is then measured against. A sector population is used
wherever it reaches three peers after that exclusion, and the portfolio otherwise, with the
reason recorded per client in `model_benchmarks.parquet`.

### Two opportunity rankings

| Ranking | Question it answers | Construction |
|---|---|---|
| `commercial_opportunity_score` | Where is the largest commercially meaningful opportunity? | 0.45 × within-product gap percentile + 0.30 × confidence + 0.25 × headroom |
| `opportunity_intensity` | Where is Syn Bank particularly under-penetrated relative to the client's scale? | `opportunity_zar / addressable_cash_flow_zar` — one ratio, no weights, no fitted coefficients |

They disagree, and they are meant to. Show both; never average them.

### Stage 3 outputs (`data/processed/`)

**The analytical contract** — the only two tables the application layer reads:

| File | Grain | Contents |
|---|---|---|
| `opportunity_engine.parquet` | 100 rows (20 × 5) | The canonical grain: observed, addressable, opportunity, share, both scores, both ranks, benchmark provenance, diagnostics, generated explanation |
| `client_opportunity_profile.parquet` | 20 rows | Each pillar's headline side by side, plus top opportunity and recommended next product. **No column sums the pillars, and a build-time assertion prevents one appearing.** |

Supporting detail: `wallet_estimates.parquet` (full internal estimates), `opportunities.parquet`
(ranked banker-facing view), `wallet_components.parquet` (per-component driver breakdown),
`model_diagnostics.parquet` (model weaknesses, severity-tagged), `portfolio_summary.parquet`,
`product_classification.parquet` / `product_confidence.parquet`, `model_assumptions.parquet` /
`model_benchmarks.parquet` / `model_benchmark_metrics.parquet` / `model_sector_rules.parquet`,
`wallet_confidence_detail.parquet`, `model_sensitivity*.parquet` (with `--sensitivity`: 3,600
rows + summaries across 36 scenarios), and `model_report.json` / `worked_examples.json`.

### The rules this stage holds to

- **No pricing.** Every figure is a flow or balance magnitude, never bank revenue.
- **No invented competitor wallet.** A gap is addressable business *not observed in Syn Bank's
  supplied data* — never confirmed competitor-held business.
- **No pillar blending.** SWIFT-channel transactional volume is excluded from the cash
  numerator and not added to the FX numerator, so it is counted in neither. The amount is
  published per client.
- **No machine learning.** Every estimate is transparent arithmetic over declared assumptions,
  recomputable by hand from the component breakdown.
- **No silent zeros.** A missing driver resolves through a documented cascade or stays NULL; a
  NULL denominator gives a NULL share, not a division.
- **No absurd shares.** Where the modelled wallet falls below activity already flowing, it is
  floored at observed, flagged, and the unfloored value retained as `estimate_modelled_zar`.
- **No self-benchmarking.** No client contributes to the peer population that sets its own
  coefficient, and no sector benchmark is built from fewer than three peers after that exclusion.
- **No unpriced assumption.** Every arguable coefficient is swept across 36 model runs, and each
  pillar carries a published robustness verdict.

### What the sensitivity sweep found

| Pillar | Verdict | Opportunity range across all 36 scenarios |
|---|---|---|
| Cash Management | **ROBUST** — untouched by every scenario | R14.25tn, identical throughout |
| Lending | **ROBUST** — rank ρ ≥ 0.997, under 5% drift | R1.32tn – R1.44tn (1.1×) |
| Trade Finance | assumption-sensitive | R39.28bn – R157.64bn (4.0×) |
| FX / Global Markets | assumption-sensitive | R78.37bn – R583.70bn (7.4×) |
| Investment Banking | no rand magnitude; signal ordering identical throughout | — |

Nine or ten of the base model's top ten opportunities survive every scenario. The FX and trade
**rand totals** should be presented as ranked opportunities with a stated range, never as a
single number — that is the honest consequence of having no disclosed total for either
activity, so the denominator *is* the coefficient.

## Stage 4 — commercial intelligence (`src/syn_wallet/build_intelligence.py`)

The deterministic semantic layer. It answers the question a Corporate & Investment Banking
relationship manager actually asks: *which client should I focus on, for which product, why,
how strong is the evidence, and what should I investigate next?*

**No LLM is called here.** Every sentence is a template filled from a published field, so
identical inputs always produce identical words. Its only inputs are the analytical contract
plus `model_sensitivity.parquet`; it recomputes nothing.

Full detail in **[docs/COMMERCIAL_INTELLIGENCE_REPORT.md](docs/COMMERCIAL_INTELLIGENCE_REPORT.md)**,
generated from the outputs.

Selection is not the biggest rand number — the five pillars produce rand on incomparable bases
and their evidence quality differs by a factor of three, so a discounted score picks the
primary, secondary and supporting signal per client:

```text
selection_score = commercial_opportunity_score
                × role_weight        CORE 1.00 / SUPPORTING 0.85 / SIGNAL_ONLY 0.55
                × confidence_weight  HIGH 1.00 / MEDIUM 0.80 / LOW 0.55
                × (1 − 0.20 if a HIGH-severity diagnostic is open)
                × (1 − 0.10 if the estimate is benchmark-sensitive)
```

| Status | Banker action | Rule |
|---|---|---|
| `PRIORITY` | Recommend investigation | HIGH confidence, score ≥ 0.65, no HIGH-severity diagnostic |
| `INVESTIGATE` | Consider investigation | Score ≥ 0.45 and not LOW confidence |
| `MONITOR` | Monitor / validate before pursuing | Everything else, and every SIGNAL_ONLY row |
| `NO_HEADROOM_DEMONSTRATED` | Retention conversation | Headroom under 5% of the addressable figure, or not sizeable |

**A LOW-confidence opportunity can never reach PRIORITY.** The only route is a named entry in
`PRIORITY_OVERRIDES` carrying a written reason; the shipped registry is empty, and tests assert
both.

### Outputs (`data/processed/`, Parquet + JSON)

`client_opportunity_intelligence.parquet` (20 rows, full client profile), `portfolio_opportunity_
intelligence.parquet` (twelve sections of portfolio intelligence), `banker_questions.parquet`
(100 rows, client-specific), `opportunity_explanations.parquet` (100 rows, WHAT / WHY / EVIDENCE
/ CONFIDENCE / LIMITATION / NEXT ACTION), `client_opportunity_cards.parquet` (20 rows, compact
list view), `opportunity_selection_detail.parquet` (every selection factor and reason),
`opportunity_sensitivity_summary.parquet` (base/low/high/range/rank stability per client × pillar).

Terminology is enforced by a test, not convention: cash management gets **Addressable Cash
Flow**, never "fee pool" / "fee wallet" / "bank revenue" / "revenue opportunity"; lending gets
**financing opportunity**, never share-of-wallet language; investment banking gets **opportunity
signal**, never a rand figure.

## Stage 5 — Client Opportunity Copilot (`src/syn_wallet/copilot/`)

A generative layer built so the language model can only **write**, never calculate. Design in
**[docs/GENAI_DESIGN.md](docs/GENAI_DESIGN.md)**; the actual prompts, generated from the module
that sends them, in **[docs/GENAI_PROMPTS.md](docs/GENAI_PROMPTS.md)**.

```text
question → router → retrieval → context → LLM → validation → audit → answer
           ↑ deterministic ─────────────┘         └── checks ──┘
```

The path that does **not** exist is raw CSV → LLM → financial calculation. No module under
`copilot/` can reach a raw dataset; retrieval reads seven stage 3–4 tables and nothing else.

| Stage | What it does |
|---|---|
| Router | Classifies one of 8 intents and resolves client / product / sector by keyword and entity matching. No LLM. |
| Retrieval | Filters, ranks and caps rows in pandas. The ranking is the model's, never the LLM's. |
| Context | Renders selected rows as labelled lines, pre-formatted, token-budgeted — and enumerates every figure into an allow-list. |
| LLM | DeepSeek (`deepseek-chat`) or NVIDIA NIM, temperature 0.2, seed pinned. Writes prose over that context and nothing else. |
| Validation | Rejects any answer containing a figure not in the allow-list, a banned phrase, a share attached to lending or IB, or a rand attached to IB. |
| Audit | One JSONL line per answer: question, retrieved IDs, full context, model, prompt version, verdict, answer. Never a secret. |

Supported questions: client briefing · opportunity explanation · portfolio query · product
query · sensitivity question · meeting preparation · executive summary · methodology question.

### Configuration

Copy `.env.example` to `.env` and set one key. `.env` is gitignored; the example carries no real
values, and a test asserts both that it names every variable the code reads and that it
contains no key.

| Variable | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek, the configured primary |
| `NVIDIA_API_KEY` | NVIDIA NIM, the alternative |
| `SYN_COPILOT_PROVIDER` | `deepseek` or `nvidia`. Unset = first one with a key |
| `SYN_COPILOT_MODEL` | Override the model. Unset = the provider's default |
| `SYN_COPILOT_BASE_URL` | Override the endpoint, for a proxy or self-host |

Both providers speak the OpenAI chat-completions protocol, so one client class covers both. A
value set in your shell beats the file, so a one-off override needs no edit.

Without a key the copilot serves stored demo answers where it has them and deterministic
answers otherwise, labelled **Demo / AI unavailable** — the figures are identical either way,
since the language model was never the thing producing them. The same fallback catches a
service error or a rejected answer, and says which happened.

An invented figure is *detected*, not merely discouraged: every rand and percentage the model
writes must appear in the context it was given, including a cross-pillar total, which by
construction was never produced upstream. A failing answer is discarded, the banker gets the
deterministic one, and the violation goes to the audit log.

## Stage 6 — the dashboard (`src/syn_wallet/serve.py`)

**Syn Bank Coverage Desk.** A five-page executive dashboard for CIB relationship managers,
answering one question first: *where should a banker focus next?*

```bash
.venv/bin/python -m src.syn_wallet.serve            # http://127.0.0.1:8000
.venv/bin/python -m src.syn_wallet.serve --port 9000
.venv/bin/python -m src.syn_wallet.serve --demo     # never call the AI, even with a key
```

Everything loads into memory at startup, so pages render instantly. Requires stages 1–4 to have
been built; `--sensitivity` on stage 3 is what fills the range marks and the model-trust page.

| Page | What it answers |
|---|---|
| Portfolio | Three core Share of Wallet cards, two supporting signals, the focus list, and where the opportunity concentrates |
| Heatmap | Every client × pillar, fill = opportunity score, **fill style = confidence**. Filter by sector, pillar, confidence, status |
| Clients | Relationship snapshot, three share gauges, the opportunity table, the financial signals behind each estimate, why it is the focus, and the banker questions |
| Model trust | Stable versus sensitive per pillar, the widest ranges in the book, the 36-run verdict, and how the benchmarks are built |
| Products | One pillar at a time, with the observed detail that suits it — currency pairs and corridors for FX, instrument mix for trade, financing components for lending, signal categories for IB |

The copilot is on every page: click **Ask the copilot** or press `/`.

Two visual devices carry the argument. **The range mark**: every figure that moves is drawn as
a band from low to high with a dot at the base; a figure that does not move gets a lone dot and
the label *does not move* — cash management is a dot, FX and trade are wide bands. **The
pillar grammar**: a solid rule for CORE, a half rule for SUPPORTING, a dotted rule for
SIGNAL_ONLY, repeated on every card, column header and table row. In the heatmap, colour
carries **magnitude** and fill style carries **evidence** — a solid dark cell is a
well-evidenced opportunity, a pale dashed outline is the same score on LOW confidence.

**No gradients, anywhere.** One indigo accent, one indigo ramp for magnitude, and the reserved
status four; every fill is a flat colour or a border. Two type voices, both from the system
stack because the dashboard must run with no network — the sans for headings and figures, a
monospace for every label that *names* rather than states.

```text
opportunity_engine + commercial intelligence  →  service layer  →  JSON API  →  browser
                                                 (projection only)
```

`src/syn_wallet/api/service.py` reads the published tables and **projects** them. It performs no
financial arithmetic, and neither does the browser: every rand figure arrives pre-formatted,
because the moment the front end derives a currency value it can disagree with the model. A
build-time assertion runs over every payload and fails if any field equals a total across the
five pillars.

No build step, no CDN, no webfont — it runs with no network. FastAPI + vanilla JS + inline SVG.

---

Read [docs/MODEL_FINAL_REPORT.md](docs/MODEL_FINAL_REPORT.md) §12 before changing what the
dashboard displays: it lists what may and may not go on a screen.
