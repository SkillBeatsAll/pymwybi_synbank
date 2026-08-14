"""Ranking client x product opportunities without letting size decide everything.

The naive ranking is "sort by rand gap", and it produces a league table of the
biggest companies in the portfolio, in order of revenue. Glencore's revenue is
R4.4 trillion; on a raw-rand ranking it would occupy the top of every product
whether or not the estimate behind it rests on an imputed denominator.

So the score combines three things, each on 0-1:

``gap_scale`` (weight 0.45)
    The **percentile rank of the rand gap inside its own product**, not the rand
    amount. Percentile because the five products have different estimate bases
    that must never be compared as one number, and because rank is what stops
    scale alone from dominating. Investment banking, which produces no rand
    amount, contributes its signal score here instead.

``confidence`` (weight 0.30)
    Straight from the confidence engine. A large opportunity resting on an
    imputed cost of sales ranks below a smaller one built from disclosed figures.

``headroom`` (weight 0.25)
    ``1 - share``. Where Syn Bank already handles most of the addressable
    activity the conversation is retention, not growth. Products with no
    defensible share (lending, investment banking) sit at a neutral 0.5 rather
    than being rewarded or penalised for a number that does not exist.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import assumptions

#: Share used for headroom when no defensible share exists. Neutral by design.
NEUTRAL_HEADROOM = 0.5


def _gap_scale(estimates: pd.DataFrame) -> pd.Series:
    """Percentile rank of the gap inside each product, or the signal score."""
    scale = pd.Series(np.nan, index=estimates.index, dtype="float64")
    for product, group in estimates.groupby("product", sort=False):
        if product == assumptions.IB:
            scale.loc[group.index] = pd.to_numeric(
                group["signal_score"], errors="coerce"
            ).fillna(0.0)
            continue
        gaps = pd.to_numeric(group["gap_zar"], errors="coerce")
        ranked = gaps.rank(pct=True, na_option="keep")
        # A client with no computable gap cannot be ranked on one; it scores
        # zero on this factor rather than borrowing a neighbour's rank.
        scale.loc[group.index] = ranked.fillna(0.0)
    return scale.clip(0.0, 1.0)


def score(estimates: pd.DataFrame) -> pd.DataFrame:
    """Add ``opportunity_score`` and both rank columns to the estimate table."""
    result = estimates.copy()
    weights = assumptions.OPPORTUNITY_WEIGHTS

    gap_scale = _gap_scale(result)
    confidence = pd.to_numeric(result["confidence"], errors="coerce").fillna(0.0).clip(0.0, 1.0)
    share = pd.to_numeric(result["share"], errors="coerce")
    headroom = (1.0 - share).fillna(NEUTRAL_HEADROOM).clip(0.0, 1.0)

    result["opportunity_gap_scale"] = gap_scale
    result["opportunity_headroom"] = headroom
    result["opportunity_score"] = (
        weights["gap"] * gap_scale
        + weights["confidence"] * confidence
        + weights["headroom"] * headroom
    ).clip(0.0, 1.0)

    # Deterministic ordering: score, then gap, then entity_id, so a rerun on
    # identical inputs produces identical ranks even where scores tie exactly.
    ordering = ["opportunity_score", "gap_zar", "entity_id"]
    ascending = [False, False, True]
    result["rank_in_product"] = (
        result.sort_values(ordering, ascending=ascending)
        .groupby("product", sort=False)
        .cumcount()
        .add(1)
        .reindex(result.index)
    )
    result["rank_overall"] = (
        result.sort_values(ordering, ascending=ascending)
        .assign(_rank=lambda frame: np.arange(1, len(frame) + 1))["_rank"]
        .reindex(result.index)
    )
    return result


#: Columns published in ``opportunities.parquet`` -- the ranked, banker-facing view.
OPPORTUNITY_COLUMNS = (
    "rank_overall",
    "rank_in_product",
    "entity_id",
    "entity_name",
    "sector",
    "product",
    "product_label",
    "estimate_basis",
    "estimate_kind",
    "observed_zar",
    "estimate_zar",
    "share",
    "gap_zar",
    "confidence",
    "confidence_band",
    "opportunity_score",
    "opportunity_gap_scale",
    "opportunity_headroom",
    "diagnostic_flags",
    "explanation",
    "methodology_version",
)


def ranked_view(estimates: pd.DataFrame) -> pd.DataFrame:
    """The ranked opportunity table, ordered best-first."""
    return (
        estimates[list(OPPORTUNITY_COLUMNS)]
        .sort_values("rank_overall")
        .reset_index(drop=True)
    )


def portfolio_summary(estimates: pd.DataFrame) -> pd.DataFrame:
    """Product-level totals, with portfolio share stated on a matched basis.

    ``portfolio_share`` divides summed observed by summed estimate, which is a
    value-weighted share. ``median_client_share`` is the unweighted middle
    client. They differ sharply when one client dominates the totals, and both
    are published so that difference stays visible.
    """
    rows = []
    for product, group in estimates.groupby("product", sort=False):
        observed = pd.to_numeric(group["observed_zar"], errors="coerce")
        estimate = pd.to_numeric(group["estimate_zar"], errors="coerce")
        gap = pd.to_numeric(group["gap_zar"], errors="coerce")
        share = pd.to_numeric(group["share"], errors="coerce")
        confidence = pd.to_numeric(group["confidence"], errors="coerce")
        total_observed = observed.sum(min_count=1)
        total_estimate = estimate.sum(min_count=1)
        rows.append(
            {
                "product": product,
                "product_label": assumptions.PRODUCT_LABELS[product],
                "estimate_basis": assumptions.ESTIMATE_BASIS[product],
                "clients": int(len(group)),
                "clients_with_estimate": int(estimate.notna().sum()),
                "clients_with_share": int(share.notna().sum()),
                "total_observed_zar": total_observed,
                "total_estimate_zar": total_estimate,
                "total_gap_zar": gap.sum(min_count=1),
                "portfolio_share": (
                    total_observed / total_estimate
                    if pd.notna(total_estimate) and total_estimate > 0
                    else np.nan
                ),
                "median_client_share": share.median(),
                "min_client_share": share.min(),
                "max_client_share": share.max(),
                "mean_confidence": confidence.mean(),
                "clients_high_confidence": int((group["confidence_band"] == "HIGH").sum()),
                "clients_medium_confidence": int((group["confidence_band"] == "MEDIUM").sum()),
                "clients_low_confidence": int((group["confidence_band"] == "LOW").sum()),
                "clients_flagged": int((group["diagnostic_flags"].fillna("") != "").sum()),
                "methodology_version": assumptions.METHODOLOGY_VERSION,
            }
        )
    order = {product: position for position, product in enumerate(assumptions.PRODUCTS)}
    return (
        pd.DataFrame(rows)
        .sort_values("product", key=lambda column: column.map(order))
        .reset_index(drop=True)
    )
