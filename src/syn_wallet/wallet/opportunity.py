"""Two rankings, because bankers ask two different questions.

**A. Commercial Opportunity Score** -- *where is the largest commercially
meaningful opportunity?* A blended, bounded score built to be sorted top-down
into a call list. This is the v1.0.0 ``opportunity_score`` under its final name;
the old column is retained as an alias so nothing downstream breaks.

**B. Opportunity Intensity** -- *where is Syn Bank particularly under-penetrated
relative to the scale of the client's own activity?* A single transparent ratio::

    opportunity_intensity = gap_zar / addressable_cash_flow_zar

with **no weights and no fitted coefficients at all**. The denominator is the
client's own addressable cash flow (revenue + cost of sales), which is
identity-anchored, available for all twenty clients, and identical across the
five products -- so a client's five intensities are directly comparable to each
other, and a small company with a proportionally large gap outranks a giant with
a proportionally small one. That is precisely what the commercial score, which
uses a within-product percentile, cannot tell you.

The two disagree, and they are meant to. The commercial score answers "who do we
call first"; intensity answers "where are we most obviously absent". A dashboard
should show both and never average them.

---

The commercial score combines three things, each on 0-1:

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

#: Deterministic tie-break for every ranking in this module. Score first, then
#: rand gap, then entity_id and product, so two runs on identical inputs produce
#: identical ranks even where the scores tie exactly.
TIE_BREAK = ("gap_zar", "entity_id", "product")


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


def _dense_rank(
    frame: pd.DataFrame, column: str, within_product: bool = False
) -> pd.Series:
    """Rank a score descending, 1 = best, with a fully deterministic tie-break.

    Rows whose score is NULL are not ranked -- they receive ``pd.NA`` rather than
    being pushed to the bottom, because "this could not be scored" and "this
    scored worst" are different statements and the dashboard must be able to
    tell them apart.
    """
    ordering = [column, *TIE_BREAK]
    ascending = [False, False, True, True]
    scored = frame[frame[column].notna()]
    ordered = scored.sort_values(ordering, ascending=ascending)
    if within_product:
        positions = ordered.groupby("product", sort=False).cumcount().add(1)
    else:
        positions = pd.Series(np.arange(1, len(ordered) + 1), index=ordered.index)
    return positions.reindex(frame.index).astype("Int64")


def commercial_opportunity_score(estimates: pd.DataFrame) -> pd.DataFrame:
    """The three declared factors and the score they produce.

    Returned as a frame rather than a Series so a test can reproduce the score
    from its published inputs without re-deriving them.
    """
    weights = assumptions.OPPORTUNITY_WEIGHTS
    gap_scale = _gap_scale(estimates)
    confidence = (
        pd.to_numeric(estimates["confidence"], errors="coerce").fillna(0.0).clip(0.0, 1.0)
    )
    share = pd.to_numeric(estimates["share"], errors="coerce")
    headroom = (1.0 - share).fillna(NEUTRAL_HEADROOM).clip(0.0, 1.0)
    return pd.DataFrame(
        {
            "opportunity_gap_scale": gap_scale,
            "opportunity_headroom": headroom,
            "commercial_opportunity_score": (
                weights["gap"] * gap_scale
                + weights["confidence"] * confidence
                + weights["headroom"] * headroom
            ).clip(0.0, 1.0),
        }
    )


def opportunity_intensity(estimates: pd.DataFrame) -> pd.Series:
    """``gap_zar / addressable_cash_flow_zar`` -- one ratio, no coefficients.

    NULL wherever either side is missing or the denominator is non-positive: a
    client with no addressable cash flow has no scale to be under-penetrated
    relative to, and inventing one would defeat the point of the metric.
    Investment banking is NULL for every client by construction, because it
    produces no rand gap.
    """
    gap = pd.to_numeric(estimates["gap_zar"], errors="coerce")
    scale = pd.to_numeric(estimates["addressable_cash_flow_zar"], errors="coerce")
    usable = scale.notna() & (scale > 0) & gap.notna()
    intensity = pd.Series(np.nan, index=estimates.index, dtype="float64")
    intensity[usable] = gap[usable] / scale[usable]
    return intensity.replace([np.inf, -np.inf], np.nan)


def score(estimates: pd.DataFrame) -> pd.DataFrame:
    """Add both opportunity scores and every rank column to the estimate table.

    Requires ``addressable_cash_flow_zar`` to already be present on every row --
    the engine attaches it from the cash pillar before calling this, so that the
    intensity denominator is literally the published cash figure rather than a
    second, independently derived one that could drift from it.
    """
    result = estimates.copy()
    if "addressable_cash_flow_zar" not in result.columns:
        raise KeyError(
            "addressable_cash_flow_zar must be attached before scoring; it is the "
            "denominator of opportunity_intensity"
        )

    result = pd.concat([result, commercial_opportunity_score(result)], axis=1)
    # Retained as an alias so anything built against v1.0.0 keeps working. The
    # two columns are equal by construction and a test asserts it.
    result["opportunity_score"] = result["commercial_opportunity_score"]

    result["opportunity_intensity"] = opportunity_intensity(result)
    # A bounded companion for display. The raw ratio is the metric; this is only
    # a rendering convenience, and no ranking is computed from it.
    result["opportunity_intensity_percentile"] = (
        result["opportunity_intensity"].rank(pct=True, na_option="keep").astype("float64")
    )

    result["commercial_rank"] = _dense_rank(result, "commercial_opportunity_score")
    result["commercial_rank_in_product"] = _dense_rank(
        result, "commercial_opportunity_score", within_product=True
    )
    result["intensity_rank"] = _dense_rank(result, "opportunity_intensity")
    result["intensity_rank_in_product"] = _dense_rank(
        result, "opportunity_intensity", within_product=True
    )

    # v1.0.0 rank names, kept as aliases of the commercial ranking.
    result["rank_overall"] = result["commercial_rank"]
    result["rank_in_product"] = result["commercial_rank_in_product"]
    return result


#: Columns published in ``opportunities.parquet`` -- the ranked, banker-facing view.
OPPORTUNITY_COLUMNS = (
    "commercial_rank",
    "commercial_rank_in_product",
    "intensity_rank",
    "intensity_rank_in_product",
    "rank_overall",
    "rank_in_product",
    "entity_id",
    "entity_name",
    "sector",
    "product",
    "product_label",
    "pillar_role",
    "estimate_basis",
    "estimate_kind",
    "observed_zar",
    "estimate_zar",
    "addressable_cash_flow_zar",
    "share",
    "gap_zar",
    "confidence",
    "confidence_band",
    "benchmark_level",
    "benchmark_n",
    "commercial_opportunity_score",
    "opportunity_score",
    "opportunity_gap_scale",
    "opportunity_headroom",
    "opportunity_intensity",
    "opportunity_intensity_percentile",
    "diagnostic_flags",
    "explanation",
    "methodology_version",
)


def ranked_view(estimates: pd.DataFrame) -> pd.DataFrame:
    """The ranked opportunity table, ordered best-first on the commercial score."""
    return (
        estimates[list(OPPORTUNITY_COLUMNS)]
        .sort_values("commercial_rank")
        .reset_index(drop=True)
    )


#: A product needs a computable share for at least this fraction of the
#: portfolio before its share of wallet can be shown as a headline number.
CORE_SHARE_COVERAGE = 0.50


def classify_products(estimates: pd.DataFrame) -> pd.DataFrame:
    """Decide, by measurement, how far each product's output can be trusted.

    Nothing here is hardcoded per product. The rule reads what the engine
    actually produced:

    * no rand estimate for anybody          -> ``SIGNAL_ONLY``
    * a rand estimate but no computable share -> ``SUPPORTING``
    * a share for most of the portfolio     -> ``CORE``

    On this portfolio that yields cash, FX and trade CORE, lending SUPPORTING and
    investment banking SIGNAL_ONLY -- the expected answer, but reached by
    measurement, so a future run in which the lending data gained an observed
    numerator would reclassify itself instead of silently contradicting a
    hardcoded dashboard.
    """
    rows = []
    for product, group in estimates.groupby("product", sort=False):
        clients = int(len(group))
        with_estimate = int(pd.to_numeric(group["estimate_zar"], errors="coerce").notna().sum())
        with_observed = int(pd.to_numeric(group["observed_zar"], errors="coerce").notna().sum())
        with_share = int(pd.to_numeric(group["share"], errors="coerce").notna().sum())
        share_coverage = with_share / clients if clients else 0.0
        estimate_coverage = with_estimate / clients if clients else 0.0

        if with_estimate == 0:
            product_class = assumptions.SIGNAL_ONLY
            reason = (
                "No rand estimate is produced for any client, so there is nothing to show as a "
                "currency figure. Publish the ranked signal and the category only."
            )
        elif share_coverage >= CORE_SHARE_COVERAGE:
            product_class = assumptions.CORE
            reason = (
                f"A share of wallet is computable for {with_share} of {clients} clients "
                f"({share_coverage:.0%}), from an observed numerator present for {with_observed}. "
                "Share can be shown as a headline number."
            )
        else:
            product_class = assumptions.SUPPORTING
            reason = (
                f"A rand estimate exists for {with_estimate} of {clients} clients "
                f"({estimate_coverage:.0%}) but a share is computable for only {with_share}, "
                "because the supplied data carries no observed numerator for this product. "
                "Show the rand amount as an opportunity indicator, never as a share."
            )

        rows.append(
            {
                "product": product,
                "product_label": assumptions.PRODUCT_LABELS[product],
                "pillar_role": assumptions.PILLAR_ROLE[product],
                "product_class": product_class,
                "product_class_note": assumptions.PRODUCT_CLASS_NOTES[product_class],
                "classification_reason": reason,
                "clients": clients,
                "clients_with_estimate": with_estimate,
                "clients_with_observed": with_observed,
                "clients_with_share": with_share,
                "share_coverage": share_coverage,
                "estimate_coverage": estimate_coverage,
                "estimate_basis": assumptions.ESTIMATE_BASIS[product],
                "methodology_version": assumptions.METHODOLOGY_VERSION,
            }
        )
    order = {product: position for position, product in enumerate(assumptions.PRODUCTS)}
    return (
        pd.DataFrame(rows)
        .sort_values("product", key=lambda column: column.map(order))
        .reset_index(drop=True)
    )


def product_confidence(estimates: pd.DataFrame, diagnostics: pd.DataFrame) -> pd.DataFrame:
    """How reliable is each product, in the terms a dashboard needs to say it.

    ``pct_major_diagnostic`` counts clients carrying at least one HIGH-severity
    diagnostic finding for the product -- the "do not quote this before review"
    class, not the merely-noteworthy one.
    """
    high_severity = (
        diagnostics[
            (diagnostics["severity"] == "HIGH") & diagnostics["entity_id"].notna()
        ][["entity_id", "product"]]
        if len(diagnostics)
        else pd.DataFrame(columns=["entity_id", "product"])
    )
    flagged = {
        (str(row.entity_id), str(row.product)) for row in high_severity.itertuples()
    }

    classes = classify_products(estimates).set_index("product")
    rows = []
    for product, group in estimates.groupby("product", sort=False):
        confidence = pd.to_numeric(group["confidence"], errors="coerce")
        bands = group["confidence_band"]
        clients = int(len(group))
        major = sum(
            1
            for entity_id in group["entity_id"]
            if (str(entity_id), str(product)) in flagged
        )
        any_flag = int((group["diagnostic_flags"].fillna("") != "").sum())
        rows.append(
            {
                "product": product,
                "product_label": assumptions.PRODUCT_LABELS[product],
                "pillar_role": assumptions.PILLAR_ROLE[product],
                "product_class": classes.at[product, "product_class"],
                "clients": clients,
                "mean_confidence": float(confidence.mean()),
                "median_confidence": float(confidence.median()),
                "min_confidence": float(confidence.min()),
                "max_confidence": float(confidence.max()),
                "clients_high": int((bands == "HIGH").sum()),
                "clients_medium": int((bands == "MEDIUM").sum()),
                "clients_low": int((bands == "LOW").sum()),
                "pct_high": float((bands == "HIGH").mean()),
                "pct_medium": float((bands == "MEDIUM").mean()),
                "pct_low": float((bands == "LOW").mean()),
                "clients_major_diagnostic": int(major),
                "pct_major_diagnostic": float(major / clients) if clients else 0.0,
                "clients_any_flag": any_flag,
                "pct_any_flag": float(any_flag / clients) if clients else 0.0,
                "methodology_version": assumptions.METHODOLOGY_VERSION,
            }
        )
    order = {product: position for position, product in enumerate(assumptions.PRODUCTS)}
    return (
        pd.DataFrame(rows)
        .sort_values("product", key=lambda column: column.map(order))
        .reset_index(drop=True)
    )


def portfolio_summary(estimates: pd.DataFrame) -> pd.DataFrame:
    """Product-level totals, with portfolio share stated on a matched basis.

    ``portfolio_share`` divides summed observed by summed estimate, which is a
    value-weighted share. ``median_client_share`` is the unweighted middle
    client. They differ sharply when one client dominates the totals, and both
    are published so that difference stays visible.
    """
    classes = classify_products(estimates).set_index("product")
    rows = []
    for product, group in estimates.groupby("product", sort=False):
        observed = pd.to_numeric(group["observed_zar"], errors="coerce")
        estimate = pd.to_numeric(group["estimate_zar"], errors="coerce")
        gap = pd.to_numeric(group["gap_zar"], errors="coerce")
        share = pd.to_numeric(group["share"], errors="coerce")
        confidence = pd.to_numeric(group["confidence"], errors="coerce")
        intensity = pd.to_numeric(group["opportunity_intensity"], errors="coerce")
        total_observed = observed.sum(min_count=1)
        total_estimate = estimate.sum(min_count=1)
        rows.append(
            {
                "product": product,
                "product_label": assumptions.PRODUCT_LABELS[product],
                "pillar_role": assumptions.PILLAR_ROLE[product],
                "product_class": classes.at[product, "product_class"],
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
                "median_opportunity_intensity": intensity.median(),
                "max_opportunity_intensity": intensity.max(),
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
