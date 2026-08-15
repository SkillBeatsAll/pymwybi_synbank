"""Pillar 4 -- Lending.

This pillar deliberately does **not** claim a wallet or a share.

Syn Bank's supplied datasets contain no loan book: there is no facility, no
drawdown and no balance anywhere in the three internal files. So there is no
observed lending activity to divide by an estimate, and any "share of lending
wallet" would be fabricated. ``share`` is NULL with an explicit basis, and the
output is named an **opportunity estimate**, not a wallet.

Nor does the model assume existing debt is replaceable. Gross debt is never used
as the estimate. Four components, each a specific financing event inside a
twelve-month horizon:

``refinancing``
    Debt classified current. Contractually repayable within twelve months, so it
    must be repaid from cash or refinanced. Structural, coefficient 1.0.
``undrawn_facilities``
    Committed headroom another bank is providing that the client is not using.
    Contestable at renewal. Structural, coefficient 1.0.
``working_capital``
    Receivables plus inventory less payables, times the portfolio's own median
    ratio of current debt to working capital -- the share of the cycle this
    portfolio actually funds with short-term debt rather than equity.
``capex_funding``
    Annual capex times 0.30. **This is the one underived coefficient in the
    whole engine.** No cash-flow statement field exists to split capex between
    operating cash flow and new debt. A diagnostic fires whenever this component
    exceeds half of a client's estimate, and the component breakdown lets a
    reviewer set it to zero.

**Competitor evidence, kept separate.** Populated ``memo`` rows in the
transactional ledger describe facility drawdowns, bridging finance and syndicate
participations settling through the account -- lending Syn Bank is not the lender
on. Together with the banks a client names in its facilities note, this is
evidence that competitor lending exists. It raises confidence in the
*opportunity*; it is never converted into a rand amount, and its absence is not
treated as evidence of no competitor lending.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import assumptions, benchmarks, common, confidence

PRODUCT = assumptions.LENDING

WORKING_CAPITAL_BENCHMARK = "lending_current_debt_per_working_capital"


def _explain(row: pd.Series) -> str:
    pieces = []
    if pd.notna(row["refinancing_component"]) and row["refinancing_component"] > 0:
        pieces.append(
            f"refinancing {common.zar(row['refinancing_component'])} (debt classified current)"
        )
    if pd.notna(row["undrawn_component"]) and row["undrawn_component"] > 0:
        pieces.append(
            f"undrawn committed facilities {common.zar(row['undrawn_component'])} "
            "(headroom another lender is providing)"
        )
    if pd.notna(row["working_capital_component"]) and row["working_capital_component"] > 0:
        pieces.append(
            f"working-capital funding {common.zar(row['working_capital_component'])} "
            f"(cycle {common.zar(row['working_capital_zar'])} at the median debt-funded share of "
            f"{row['wc_share']:.2f} across {row['wc_benchmark_n']:,.0f} "
            f"{row['wc_benchmark_level']} peers, this client excluded)"
        )
    if pd.notna(row["capex_component"]) and row["capex_component"] > 0:
        pieces.append(
            f"capex funding {common.zar(row['capex_component'])} "
            f"(capex {common.zar(row['capex_zar'])} at the judgement coefficient "
            f"{row['capex_share']:.2f})"
        )
    body = " + ".join(pieces) if pieces else "no component estimable"
    sentence = (
        f"Financing opportunity {common.zar(row['estimate_zar'])} = {body}. "
        "This is a financing-need indicator, not a lending wallet: Syn Bank's datasets contain "
        "no loan book, so no share can be computed and none is claimed."
    )
    if row["gross_debt_zar"] == 0:
        sentence += (
            " The client discloses zero gross debt, which the audit flags as unverifiable; "
            "treat the absence of a refinancing component as provisional."
        )
    evidence = []
    if row["txn_memo_count_fy"] > 0:
        evidence.append(
            f"{row['txn_memo_count_fy']:,.0f} transactional memos describing facility drawdowns, "
            "bridging finance or syndicate settlements passing through the account"
        )
    if pd.notna(row["named_lender_count"]) and row["named_lender_count"] > 0:
        evidence.append(f"{row['named_lender_count']:,.0f} lenders named in the facilities note")
    if evidence:
        sentence += " Competitor-lending evidence: " + "; ".join(evidence) + "."
    else:
        sentence += (
            " No competitor-lending evidence is visible in the ledger, which is not evidence that "
            "none exists."
        )
    return sentence


def build(
    frame: pd.DataFrame, config: assumptions.ModelConfig | None = None
) -> common.PillarOutput:
    """Estimate the financing opportunity for every client."""
    index = frame.index
    work = frame.copy()
    config = config or assumptions.BASE_CONFIG

    debt_current = pd.to_numeric(work["debt_current_zar"], errors="coerce")
    undrawn = pd.to_numeric(work["undrawn_facilities_zar"], errors="coerce")
    working_capital = pd.to_numeric(work["working_capital_zar"], errors="coerce")
    capex = pd.to_numeric(work["capex_zar"], errors="coerce")
    gross_debt = pd.to_numeric(work["gross_debt_zar"], errors="coerce")

    # --- Working-capital funded share, measured from this client's peers ---
    # Held at the median whatever the run's benchmark percentile is: this is a
    # structural funding norm, not a penetration frontier, so the typical case
    # is the right anchor and an upper quartile would overstate it. The
    # leave-one-out and sector rules still apply.
    peers = benchmarks.PeerBenchmarks(work, config)
    wc_share = peers.register(
        WORKING_CAPITAL_BENCHMARK,
        PRODUCT,
        "debt_current_zar",
        "working_capital_zar",
        "The share of the working-capital cycle this client's peers fund with short-term debt "
        "rather than equity, at the median rather than the upper quartile: this is a structural "
        "funding norm, not a penetration frontier, so the typical case is the right anchor. The "
        "client is excluded from the population that sets its own coefficient.",
        eligible=working_capital > 0,
        percentile=0.5,
    )

    # Both coefficients are 1.0 on a structural basis: current debt is
    # contractually repayable inside the horizon, and undrawn committed
    # facilities are by definition unused capacity.
    refinancing_component = debt_current
    undrawn_component = undrawn
    working_capital_component = working_capital.clip(lower=0.0) * wc_share
    capex_component = capex * config.capex_debt_funded_share

    estimate = pd.concat(
        [
            refinancing_component,
            undrawn_component,
            working_capital_component,
            capex_component,
        ],
        axis=1,
    ).sum(axis=1, min_count=1)

    # No observed lending activity exists anywhere in the supplied datasets.
    share_result = common.share_and_gap(
        pd.Series(np.nan, index=index, dtype="float64"), estimate, observed_available=False
    )

    # --- Confidence -------------------------------------------------------
    rules = work["sector"].map(lambda sector: assumptions.sector_rule(PRODUCT, sector))
    applicability = rules.map(lambda rule: rule.applicability).astype("float64")

    inputs = [
        work["debt_current_zar"].notna(),
        work["undrawn_facilities_zar"].notna(),
        work["working_capital_zar"].notna(),
        work["capex_zar"].notna(),
    ]
    completeness = sum(series.astype("float64") for series in inputs) / len(inputs)

    structural = confidence.BASIS_DIRECTNESS[assumptions.STRUCTURAL]
    benchmark_directness = confidence.BASIS_DIRECTNESS[assumptions.PORTFOLIO_BENCHMARK]
    judgement = confidence.BASIS_DIRECTNESS[assumptions.JUDGEMENT]
    weights = {
        structural: refinancing_component.fillna(0.0) + undrawn_component.fillna(0.0),
        benchmark_directness: working_capital_component.fillna(0.0),
        judgement: capex_component.fillna(0.0),
    }
    total_weight = sum(weights.values()).replace(0.0, np.nan)
    realised = (
        refinancing_component.notna().astype("float64")
        + undrawn_component.notna().astype("float64")
        + working_capital_component.notna().astype("float64")
        + capex_component.notna().astype("float64")
    )
    directness = confidence.effective_directness(
        sum(weight * value for value, weight in weights.items()) / total_weight,
        realised,
        pd.Series(4.0, index=index),
    )

    # Competitor-lending evidence in the ledger supports the opportunity being real.
    memos = pd.to_numeric(work["txn_memo_count_fy"], errors="coerce").fillna(0.0)
    named = pd.to_numeric(work["named_lender_count"], errors="coerce").fillna(0.0)
    evidence = confidence.observation_support(memos + named * 10.0)
    consistency = confidence.internal_consistency(
        work, uses_revenue=False, uses_debt_structure=True
    )
    scored = confidence.score(completeness, directness, applicability, evidence, consistency)

    # --- Diagnostics ------------------------------------------------------
    capex_share_of_estimate = (capex_component / estimate).replace([np.inf, -np.inf], np.nan)
    flags = common.FlagSet(index)
    flags.add("capex_judgement_dominates", capex_share_of_estimate > 0.50)
    flags.add("zero_gross_debt_disclosed", gross_debt.eq(0.0))
    flags.add("undrawn_facilities_undisclosed", work["undrawn_facilities_zar"].isna())
    flags.add("no_competitor_lending_evidence", (memos + named) == 0)
    flags.add("gross_debt_identity_failed", work["gross_debt_identity_ok"].eq(False))
    flags.add("negative_working_capital", working_capital < 0)

    explain_frame = pd.DataFrame(
        {
            "estimate_zar": estimate,
            "refinancing_component": refinancing_component,
            "undrawn_component": undrawn_component,
            "working_capital_component": working_capital_component,
            "capex_component": capex_component,
            "working_capital_zar": working_capital,
            "capex_zar": capex,
            "gross_debt_zar": gross_debt,
            "wc_share": wc_share,
            "wc_benchmark_level": peers.levels(WORKING_CAPITAL_BENCHMARK),
            "wc_benchmark_n": peers.sample_sizes(WORKING_CAPITAL_BENCHMARK),
            "capex_share": config.capex_debt_funded_share,
            "txn_memo_count_fy": memos,
            "named_lender_count": work["named_lender_count"],
        }
    )
    explanation = explain_frame.apply(_explain, axis=1)

    estimates = common.assemble(
        work,
        PRODUCT,
        None,
        estimate,
        share_result,
        scored.score,
        scored.band,
        explanation,
        flags.series(),
        estimate_kind="opportunity_estimate",
        benchmark_level=peers.levels(WORKING_CAPITAL_BENCHMARK),
        benchmark_n=peers.sample_sizes(WORKING_CAPITAL_BENCHMARK).astype("Int64"),
        benchmark_fallback_reason=peers.fallback_reasons(WORKING_CAPITAL_BENCHMARK),
    )

    components = common.component_rows(
        work,
        PRODUCT,
        {
            "refinancing": refinancing_component,
            "undrawn_facilities": undrawn_component,
            "working_capital": working_capital_component,
            "capex_funding": capex_component,
        },
        {
            "refinancing": (debt_current, pd.Series("disclosed", index=index).where(debt_current.notna(), "unavailable")),
            "undrawn_facilities": (undrawn, pd.Series("disclosed", index=index).where(undrawn.notna(), "unavailable")),
            "working_capital": (working_capital, pd.Series("disclosed", index=index).where(working_capital.notna(), "unavailable")),
            "capex_funding": (capex, pd.Series("disclosed", index=index).where(capex.notna(), "unavailable")),
        },
    )

    detail = scored.detail.copy()
    detail["entity_id"] = work["entity_id"]
    detail["product"] = PRODUCT

    flag_frame = flags.as_frame()
    flag_frame["entity_id"] = work["entity_id"]
    flag_frame["product"] = PRODUCT

    return common.PillarOutput(
        estimates,
        components,
        detail,
        flag_frame,
        peers.coefficient_records(),
        peers.metric_summary(),
    )
