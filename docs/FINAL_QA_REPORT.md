# Final QA Report — Syn Bank Share of Wallet Intelligence Engine

Generated 2026-08-15. Every figure below is an observed result from a command
run against this working tree, not an estimate. Commands are given so each row
can be reproduced.

**This report covers the areas that were actually exercised.** Sections of the QA
brief that were not run are listed under [Not covered](#not-covered) rather than
being marked PASS by omission.

---

## Summary

| # | Area | Verdict |
|---|---|---|
| 2 | Environment / secret audit | **PASS** |
| 3 | Adversarial Copilot QA | **PASS** — 16/16 live, 16/16 offline |
| 4 | Numeric integrity (no front-end calculation) | **PASS** — one defect found and fixed |
| 5 | Analytical contract (no stale sources) | **PASS** |
| 6 | Dashboard endpoints | **PASS** — every page, all 20 clients, all 5 products |
| 7 | Copilot integration | **PASS** |
| 8 | Performance | **PASS** |
| — | Unit + integration tests | **PASS** — 523 passed, 0 failed |
| 1 | Clean-environment install from scratch | **PARTIAL** — see below |
| 9–13 | Submission assets, narrative, presentation metrics | **NOT DONE** |

Four defects were found and fixed during this pass. They are described in
[Defects found and fixed](#defects-found-and-fixed).

---

## Defects found and fixed

### 1. The Copilot reported a reachable service as unreachable — FIXED

**Symptom.** The dashboard frequently showed *"Demo / AI unavailable — the
language-model service could not be reached"* while the service was up and
answering.

**Cause.** Not connectivity. `deepseek-v4-flash` is a reasoning model: it spends
output budget thinking before it writes. The ceiling was 2,048 tokens, then
8,192. Measured across the ten demo questions with a 16,000-token allowance,
reasoning alone ran to **1,360–12,983 tokens** and the worst total was
**14,865** (the Vodacom briefing). Past the ceiling the API returns HTTP 200,
`finish_reason="length"` and **empty content**. The client treated empty content
as unavailability and the UI reported a network failure that had not happened.

**Fix.**
- `MAX_OUTPUT_TOKENS` 8,192 → **16,384**, sized from the measurement above;
  `RETRY_OUTPUT_TOKENS` = 32,768 for one retry when a first attempt truncates.
- `REQUEST_TIMEOUT_SECONDS` 60 → **240**. A reasoning model generating 15k
  tokens takes over a minute, so the old ceiling turned a slow success into a
  timeout, and the timeout into the same misleading notice.
- New `LLMTruncated` exception and `FALLBACK_TRUNCATED` mode, so a truncation
  can never again be reported as a connectivity failure. Its notice reads
  *"the language-model service responded, but the answer ran past its output
  limit"*, which is true.
- `reasoning_tokens` now recorded in the audit record, so the next occurrence is
  diagnosable from the log rather than from a debugging session.

**Verification.** Eight questions through `deepseek-v4-flash`, the configuration
that was failing:

| Before | After |
|---|---|
| 3 of 5 fell back to "could not be reached" | **8 of 8 return generated answers**, 0 fallbacks, 0 validation warnings |

### 2. A rand figure attributed to Investment Banking was not rejected — FIXED

Found by the adversarial suite. `IB_RAND_ATTRIBUTION` now rejects a rand amount
attached to investment banking, precisely enough not to fire on the correct
sentence *"FX is R8.75bn; investment banking is a ranked signal only"*.

### 3. Competitor-ownership and external-source claims were unchecked — FIXED

Also found by the adversarial suite. `"held by a competitor bank"` and
`"according to their annual report"` both passed validation. Two new
negation-aware rejection rules, `competitor_ownership` and `external_source`,
close them.

### 4. The browser formatted currency, disagreeing with the server — FIXED

`dashboard/assets/app.js` carried `fmtShort()`, a second rand formatter rounding
to whole billions where the server writes two decimals — so a figure the model
published as **R278.56bn** could be drawn as **R279bn**. Its own comment claimed
it was "for axis ticks only", but it was reached from the heatmap tooltip and
the client table.

`fmtShort` is deleted. The API now ships a `_display` string beside every rand
column the front end reads (`with_money()` in `api/service.py`), and the browser
renders no currency at all. Pinned by
`test_the_front_end_contains_no_currency_formatter`.

---

## 2. Environment and secret audit — PASS

| Check | Command | Result |
|---|---|---|
| `.env` gitignored | `git check-ignore -v .env` | PASS — `.gitignore:13` |
| `.env` not tracked | `git ls-files .env` | PASS — not known to git |
| No key in tracked source | `git grep -nIE "sk-[A-Za-z0-9]{16,}\|nvapi-…"` | PASS — no hits |
| No key in generated reports | grep over `*.md`, `data/processed/`, `dashboard/` | PASS — no hits |
| No key in audit log | `_assert_no_secret` walks every record before write | PASS — asserted by test |
| `.env.example` has no real credential | test asserts every non-comment value is empty | PASS |
| Model config is environment-driven | `SYN_COPILOT_PROVIDER` / `_MODEL` / `_BASE_URL` | PASS |
| Demo mode works with no key | stored demos, else deterministic template | PASS |

**`--demo` cannot reactivate a real key.** This was checked specifically, because
it is the promise the flag makes to a judge. `serve --demo` sets
`SYN_COPILOT_DEMO=1`; `llm_available()` short-circuits on `demo_mode()` before
the key is read. Enforcement was previously in that one place, so a refactor
that called `complete()` without the pre-check would have reached the network.
`ChatClient._client()` now refuses independently, and two tests pin both paths —
including one that sets a real-looking key *and* demo mode and asserts the
client still refuses.

## 3. Adversarial Copilot QA — PASS

New: `analysis/adversarial_suite.py` (16 traps), `tests/test_copilot_adversarial.py`
(70 offline tests), `analysis/adversarial_qa_report.py` → `ADVERSARIAL_QA_REPORT.md`.

Every brief-mandated category is covered and asserted covered by test:
arithmetic, range/competitor inference, revenue/pricing, lending, investment
banking, competitor, confidence, model source, client confusion, prompt
injection.

Each trap is checked three ways, and all three must hold:

1. **Deterministic answer clean** — with no model available, the template must
   not take the bait either.
2. **Poison rejected** — the trap's own bad answer, validated against the context
   that question really retrieves, with the poison's figures added to the
   allow-list so the *claim* is judged rather than the arithmetic.
3. **Routing correct** — for distractor questions, only the intended client.

| Run | Result |
|---|---|
| Offline (`--offline`) | **16/16 PASS** |
| Live (`deepseek-chat`) | **16/16 PASS** |
| Offline test suite | **70 passed** |

**A note on the first live run, because it is the more useful result.** It
reported 11/16. All five "failures" were the model behaving correctly — *"this is
client flow, **not** bank revenue"*, *"there is **no** lending Share of Wallet"*,
*"the FX opportunity is **not** certain"*. The trap checker was matching
substrings, not claims, and was reporting honesty as failure. Acting on that
report would have pushed the model toward being unable to deny a banned claim,
which is worse than the problem. `forbidden_hits` is now negation-aware, sharing
its cue list with the validator so the two cannot drift, and two tests pin the
carve-out from both sides: every poison must still trip its own patterns, and
six correct denials must trip none.

## 4. Numeric integrity — PASS

- `api/service.py` performs no arithmetic on financial columns: it filters,
  sorts and shapes. Verified by reading the module and by
  `test_portfolio_figures_match_the_portfolio_summary` and
  `test_client_pillar_figures_match_the_selection_detail`, which trace payload
  figures back to the published columns.
- `_assert_no_cross_pillar_total` runs on every portfolio payload at build time,
  not only in tests.
- The browser now performs no currency formatting and no financial arithmetic.
  The only client-side numbers left are pixel geometry for the range mark and
  percentage rescaling of published ratios.
- One duplicate financial implementation found (`fmtShort`) and removed — see
  defect 4.

## 5. Analytical contract — PASS

`MODEL_TABLES` in `api/service.py` reads only stage 3 and stage 4 Parquet:
`opportunity_engine`, `client_opportunity_profile` and the eleven
commercial-intelligence tables built from them, plus two optional sensitivity
tables and two stage-2 descriptive tables (`client_features`,
`client_corridor_breakdown`, used for observed breakdowns only — no estimate is
derived from them).

Searched for accidental fallbacks across `src/syn_wallet/api/`,
`src/syn_wallet/copilot/` and `dashboard/assets/app.js`:

| Looked for | Found |
|---|---|
| `.csv` reads | none |
| `read_csv` | none |
| `MODEL_REPORT` values | none |
| Markdown file reads | none |
| Hardcoded rand constants | none |

## 6. Dashboard — PASS

Every endpoint exercised through `TestClient`, including all 20 clients and all
5 products individually.

| Surface | Result |
|---|---|
| `/`, `/assets/app.js`, `/assets/app.css` | 200 |
| `/api/health`, `/portfolio`, `/heatmap`, `/clients`, `/sensitivity`, `/products` | 200 |
| `/api/clients/{id}` × 20 | 200, all |
| `/api/products/{p}` × 5 | 200, all |
| `POST /api/copilot/ask` | 200 |
| **Failures** | **none** |

`node --check dashboard/assets/app.js` passes after every JS edit.

Terminology invariants are asserted by test rather than by inspection:
`test_lending_and_ib_never_carry_a_share`,
`test_investment_banking_never_carries_a_rand_figure`,
`test_cash_is_labelled_addressable_cash_flow_and_never_a_wallet`,
`test_no_page_payload_totals_the_pillars`, and
`test_the_no_total_guard_actually_fires` (which proves the guard is not vacuous).

## 7. Copilot integration — PASS

Question → deterministic retrieval → context → DeepSeek → validation → answer,
verified live for eight questions across four clients (Vodacom, MTN, Shoprite,
NEPI Rockcastle) plus portfolio, product, sensitivity and methodology intents.

All eight returned `mode=llm` with zero validation violations and zero warnings.
Correct client, figures from the allow-list, confidence and sensitivity
preserved, no competitor claim, no fee or revenue claim, no cross-pillar total —
the last four now enforced by the rules in defects 2 and 3 rather than by the
prompt alone.

## 8. Performance — PASS

Measured on this machine, warm.

| Step | Time |
|---|---|
| Startup: load every published table | **27 ms** |
| Portfolio page | 10.1 ms |
| Heatmap page | 4.1 ms |
| Client page (worst of 20) | 3.7 ms |
| Product page (worst of 5) | 3.0 ms |
| Sensitivity page | 4.2 ms |
| Copilot, deterministic | 6.5 ms |
| Copilot, live `deepseek-chat` | median **3.4 s**, max 11.0 s |
| Copilot, live `deepseek-v4-flash` (reasoning) | 15.8–105.2 s |

No optimisation was needed or done. One configuration note for the demo:
`deepseek-chat` is the current default and answers a briefing in seconds;
`deepseek-v4-flash` reasons first and took **105 s** for the Vodacom briefing.
Both now work correctly — the reasoning model no longer truncates — but the
default is the one that fits a timed demo.

## 1. Clean-environment install — PARTIAL

**Verified.** `requirements.txt` pins every import the pipeline, dashboard and
tests use, with `openai` correctly marked optional (the suite passes without
it). The README quickstart lists every stage in order with working module
paths; the raw CSVs are gitignored but restorable from the tracked
`data/data.tgz`, and `data/processed/` is regenerated by the documented
commands. No undocumented local state was found in the code paths exercised
above. `.env.example` is a complete template and a test asserts it documents
every variable the code reads.

**Not verified.** The pipeline was not rebuilt from scratch in a fresh
virtualenv on a clean clone during this pass — the existing
`data/processed/*.parquet` were used throughout. The stale test count in the
README (431) was corrected to 523, which is itself evidence that this quickstart
had drifted and should be run end to end before submission.

---

## Test suite

```
.venv/bin/python -m pytest -q
523 passed in 27.88s
```

Up from 446 at the start of this pass: +55 adversarial and validation tests,
+2 demo-mode isolation tests, +2 front-end formatting tests, plus the
rounding-tolerance and IB-attribution parametrised cases.

---

## Known limitations

Carried forward from `AUDIT_REPORT.md` §7 and `CLAUDE.md` §6.6 — unchanged by
this pass, and none of them a code defect:

- Three entities fail the revenue-split identity (E08 Sanlam, E18 Bidvest,
  E06 Valterra). Geographic splits built on those legs will not reconcile.
- Five zeros in debt / FX-notional fields cannot be cross-checked (E05, E06,
  E07). Treat E06 and E07 zero-debt claims as provisional in a lending
  narrative.
- `fx_rates_fy_window.csv` is stale for E11, E12, E13; use the derived rates in
  `CLAUDE.md` §3.4.
- `total_assets`, `equity` and `total_liabilities` do not exist in
  `data/finances/`, so the balance-sheet identity cannot be checked.
- Fiscal year ends span nine months across five distinct dates; the alignment of
  the 36-month flow window to 20 reporting periods is a decision no code
  currently encodes.
- FX and trade rand totals move 7.4× and 4.0× across the 36-scenario grid. Rank
  them, quote the range, never a point estimate.

New limitation from this pass:

- Validation now warns rather than rejects on three heuristic checks
  (share-of-wallet proximity to lending or IB, rand-figure co-occurrence with
  IB, first-person analyst voice). Warnings are recorded in the audit log and
  counted in `ValidationResult.warnings`. The load-bearing guarantees —
  unsupported figures, asserted forbidden claims, cross-pillar totalling,
  competitor ownership, external sourcing, IB rand attribution — all remain
  rejections.

## Not covered

These sections of the QA brief were not run, and are not claimed:

- **§6 browser-level dashboard QA.** Endpoints, payload invariants and JS syntax
  were verified; the pages were not opened in a browser during this pass, so
  console cleanliness and chart rendering are unverified here.
- **§9** `DEMO.md`, architecture diagram, and the README's ability to answer the
  nine listed questions.
- **§10** `submission/` — notebook, methodology document, GenAI evidence,
  presentation data, demo script, one-page summary.
- **§11** machine-readable final presentation metrics.
- **§12** `JUDGING_NARRATIVE.md`.
