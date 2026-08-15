"""Pillar 5 -- Investment Banking / Capital Markets.

The lowest-confidence pillar, and the only one that produces **no rand amount at
all**. Nothing in the supplied data says a client is planning an issue, a
disposal or an acquisition. Inventing a deal size would be the least defensible
number in the engine, so none is produced.

What the data *can* support is a ranked signal of mandate likelihood, built from
five normalised balance-sheet facts, and a category assigned only when a specific
threshold is met:

``debt_capital_markets``
    At least 30% of gross debt classified current, and a debt maturity profile
    actually disclosed. A near-term maturity wall with a published profile is
    what a DCM desk works from.
``refinancing_restructuring``
    Leverage above half of revenue *and* an implied cost of debt above 9%.
    Expensive debt on a leveraged balance sheet.
``corporate_finance``
    Capex intensity above 10%. An investment programme large enough to need
    external structuring.
``advisory``
    A wide named-lender syndicate with no other trigger met -- relationship
    breadth without a specific event.
``none_supported``
    The default. Most clients land here, and that is the correct answer.

Scores are percentile ranks inside this portfolio, so they say "high relative to
these twenty clients", never "high in absolute terms".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import assumptions, common, confidence

PRODUCT = assumptions.IB

#: Weights on the five normalised signals. Equal thirds would over-weight scale;
#: leverage and refinancing pressure are what actually generate mandates.
SIGNAL_WEIGHTS = {
    "scale": 0.20,
    "leverage": 0.25,
    "near_term_maturity": 0.25,
    "capex_intensity": 0.20,
    "syndicate_breadth": 0.10,
}


def _percentile(series: pd.Series) -> pd.Series:
    """Rank inside the portfolio on 0-1, with missing values held at neutral."""
    values = pd.to_numeric(series, errors="coerce")
    ranked = values.rank(pct=True, na_option="keep")
    return ranked.fillna(0.5)


def _categorise(row: pd.Series) -> str:
    thresholds = assumptions.IB_THRESHOLDS
    if (
        pd.notna(row["near_term_maturity_ratio"])
        and row["near_term_maturity_ratio"] >= thresholds["near_term_maturity"]
        and bool(row["has_debt_maturity_disclosure"])
    ):
        return "debt_capital_markets"
    if (
        pd.notna(row["net_debt_to_revenue"])
        and row["net_debt_to_revenue"] >= thresholds["leverage"]
        and pd.notna(row["finance_costs_to_debt"])
        and row["finance_costs_to_debt"] >= thresholds["cost_of_debt"]
    ):
        return "refinancing_restructuring"
    if (
        pd.notna(row["capex_to_revenue"])
        and row["capex_to_revenue"] >= thresholds["capex_intensity"]
    ):
        return "corporate_finance"
    if pd.notna(row["named_lender_count"]) and row["named_lender_count"] >= 5:
        return "advisory"
    return "none_supported"


def _explain(row: pd.Series) -> str:
    supporting = row["supporting_signals"]
    sentence = (
        f"Investment-banking opportunity signal {row['signal_score']:.2f} "
        f"(percentile-ranked within the portfolio). No rand amount is estimated: nothing in the "
        f"supplied data indicates a planned issue, disposal or acquisition."
    )
    if row["opportunity_type"] == "none_supported":
        sentence += (
            " No category is assigned; the disclosed balance sheet does not meet any mandate "
            "threshold."
        )
    else:
        sentence += f" Category: {row['opportunity_type'].replace('_', ' ')}, triggered because "
        if row["opportunity_type"] == "debt_capital_markets":
            sentence += (
                f"{common.pct(row['near_term_maturity_ratio'], 1)} of gross debt is classified "
                "current and a debt maturity profile is disclosed."
            )
        elif row["opportunity_type"] == "refinancing_restructuring":
            sentence += (
                f"net debt is {row['net_debt_to_revenue']:.2f}x revenue at an implied cost of "
                f"debt of {common.pct(row['finance_costs_to_debt'], 1)}."
            )
        elif row["opportunity_type"] == "corporate_finance":
            sentence += f"capex intensity is {common.pct(row['capex_to_revenue'], 1)} of revenue."
        else:
            sentence += (
                f"{row['named_lender_count']:,.0f} lenders are named in the facilities note "
                "without any other mandate trigger being met."
            )
    if supporting:
        sentence += f" Strongest supporting signals: {supporting}."
    return sentence


def build(
    frame: pd.DataFrame, config: assumptions.ModelConfig | None = None
) -> common.PillarOutput:
    """Score investment-banking mandate likelihood for every client.

    Takes ``config`` for signature symmetry with the other pillars and uses
    nothing from it: every threshold here is a declared judgement and none of
    them is a peer coefficient, so no sensitivity scenario moves this pillar.
    """
    index = frame.index
    work = frame.copy()

    gross_debt = pd.to_numeric(work["gross_debt_zar"], errors="coerce")
    debt_current = pd.to_numeric(work["debt_current_zar"], errors="coerce")
    near_term = (debt_current / gross_debt.replace(0.0, np.nan)).replace(
        [np.inf, -np.inf], np.nan
    )

    signals = pd.DataFrame(
        {
            "scale": _percentile(work["revenue_total_zar"]),
            "leverage": _percentile(work["net_debt_to_revenue"]),
            "near_term_maturity": _percentile(near_term),
            "capex_intensity": _percentile(work["capex_to_revenue"]),
            "syndicate_breadth": _percentile(work["named_lender_count"]),
        }
    )
    signal_score = sum(signals[name] * weight for name, weight in SIGNAL_WEIGHTS.items())
    signal_score = signal_score.clip(0.0, 1.0)

    category_frame = pd.DataFrame(
        {
            "near_term_maturity_ratio": near_term,
            "has_debt_maturity_disclosure": work["has_debt_maturity_disclosure"].fillna(False),
            "net_debt_to_revenue": pd.to_numeric(work["net_debt_to_revenue"], errors="coerce"),
            "finance_costs_to_debt": pd.to_numeric(work["finance_costs_to_debt"], errors="coerce"),
            "capex_to_revenue": pd.to_numeric(work["capex_to_revenue"], errors="coerce"),
            "named_lender_count": pd.to_numeric(work["named_lender_count"], errors="coerce"),
        }
    )
    opportunity_type = category_frame.apply(_categorise, axis=1)

    top_signals = signals.apply(
        lambda row: ", ".join(
            f"{name.replace('_', ' ')} {value:.2f}"
            for name, value in row.sort_values(ascending=False).head(2).items()
            if value >= 0.6
        ),
        axis=1,
    )

    # --- Confidence -------------------------------------------------------
    rules = work["sector"].map(lambda sector: assumptions.sector_rule(PRODUCT, sector))
    applicability = rules.map(lambda rule: rule.applicability).astype("float64")

    inputs = [
        work["gross_debt_zar"].notna(),
        work["debt_current_zar"].notna(),
        work["capex_zar"].notna(),
        work["cash_and_equivalents_zar"].notna(),
        work["finance_costs_zar"].notna(),
    ]
    completeness = sum(series.astype("float64") for series in inputs) / len(inputs)

    # Every threshold in this pillar is a judgement, so directness is capped there
    # and rises only where a maturity profile is actually disclosed.
    judgement = confidence.BASIS_DIRECTNESS[assumptions.JUDGEMENT]
    directness = pd.Series(judgement, index=index, dtype="float64")
    directness += 0.15 * work["has_debt_maturity_disclosure"].fillna(False).astype("float64")

    observation = confidence.observation_support(
        pd.to_numeric(work["named_lender_count"], errors="coerce").fillna(0.0) * 10.0
        + pd.to_numeric(work["txn_memo_count_fy"], errors="coerce").fillna(0.0)
    )
    consistency = confidence.internal_consistency(
        work, uses_revenue=True, uses_debt_structure=True
    )
    scored = confidence.score(completeness, directness, applicability, observation, consistency)

    # --- Diagnostics ------------------------------------------------------
    flags = common.FlagSet(index)
    flags.add("no_mandate_category_supported", opportunity_type == "none_supported")
    flags.add("signal_only_no_rand_estimate", pd.Series(True, index=index))
    flags.add("zero_gross_debt_disclosed", gross_debt.eq(0.0))
    flags.add(
        "driven_by_scale_alone",
        (signals["scale"] >= 0.8)
        & (signals[["leverage", "near_term_maturity", "capex_intensity"]].max(axis=1) < 0.5),
    )

    nan_series = pd.Series(np.nan, index=index, dtype="float64")
    share_result = common.share_and_gap(nan_series, nan_series, observed_available=False)

    explain_frame = category_frame.copy()
    explain_frame["signal_score"] = signal_score
    explain_frame["opportunity_type"] = opportunity_type
    explain_frame["supporting_signals"] = top_signals
    explanation = explain_frame.apply(_explain, axis=1)

    estimates = common.assemble(
        work,
        PRODUCT,
        None,
        nan_series,
        share_result,
        scored.score,
        scored.band,
        explanation,
        flags.series(),
        estimate_kind="signal_only",
        signal_score=signal_score,
    )
    estimates["opportunity_type"] = opportunity_type.to_numpy()

    component_values = {name: signals[name] for name in SIGNAL_WEIGHTS}
    components = common.component_rows(work, PRODUCT, component_values, {})
    components["component_zar"] = np.nan
    components["signal_value"] = pd.concat(
        [signals[name] for name in SIGNAL_WEIGHTS], ignore_index=True
    ).to_numpy()

    detail = scored.detail.copy()
    detail["entity_id"] = work["entity_id"]
    detail["product"] = PRODUCT

    flag_frame = flags.as_frame()
    flag_frame["entity_id"] = work["entity_id"]
    flag_frame["product"] = PRODUCT

    return common.PillarOutput(estimates, components, detail, flag_frame, [])
