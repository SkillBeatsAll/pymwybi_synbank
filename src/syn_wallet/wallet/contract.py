"""The analytical contract: what the application layer is allowed to read.

Everything upstream of this module is free to change shape. Everything
downstream -- dashboard, GenAI narrative, any export -- reads only what is
declared here, so a model change that would break an application breaks a test
first.

Two tables:

``opportunity_engine``
    One row per client x product. The canonical grain. Column names are chosen
    to be product-neutral (``addressable_zar``, ``opportunity_zar``) precisely
    because the five products' rand figures mean different things and the
    ``estimate_basis`` column, not the column name, is what says which.

``client_opportunity_profile``
    One row per client. A denormalised convenience view for a client page,
    carrying each pillar's headline side by side.

**The one arithmetic prohibition, enforced by construction.** No column here sums
across pillars, and none can be built by summing across pillars, because the five
rand figures are not commensurable: two of them overlap by an unresolvable
amount (SWIFT), one is a fee-free flow identity, two are peer-benchmarked
exposures and one is a financing need with no observed side at all. The profile
table therefore has five separate pillar columns and no total. A test asserts
that no column equals the row-wise sum of the pillar columns.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import assumptions

# ---------------------------------------------------------------------------
# opportunity_engine -- one row per client x product
# ---------------------------------------------------------------------------

#: The contract. Downstream code may read these columns and should tolerate
#: additional ones being appended; it must never rely on ordinal position.
OPPORTUNITY_ENGINE_COLUMNS = (
    # identity
    "entity_id",
    "entity_name",
    "sector",
    "fy_label",
    "fiscal_year_end",
    "product",
    "product_label",
    "pillar_role",
    "product_class",
    # what the rand figure means
    "estimate_basis",
    "estimate_kind",
    # the numbers
    "observed_zar",
    "addressable_zar",
    "opportunity_zar",
    "addressable_cash_flow_zar",
    "cash_management_wallet_zar",
    "share",
    "share_basis",
    "opportunity_basis",
    # quality
    "confidence",
    "confidence_band",
    "benchmark_level",
    "benchmark_n",
    "benchmark_fallback_reason",
    "diagnostic_count",
    "high_severity_diagnostic",
    "diagnostic_flags",
    # ranking
    "commercial_opportunity_score",
    "commercial_rank",
    "commercial_rank_in_product",
    "opportunity_intensity",
    "opportunity_intensity_percentile",
    "intensity_rank",
    "intensity_rank_in_product",
    "signal_score",
    # narrative
    "explanation",
    "methodology_version",
)

#: Products whose rand columns must be NULL rather than zero, because no
#: defensible denominator exists. Enforced, not merely documented.
NO_RAND_DENOMINATOR = (assumptions.IB,)


def _diagnostic_counts(diagnostics: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Per client x product finding counts, and whether any is HIGH severity."""
    if diagnostics.empty:
        empty = pd.Series(dtype="int64")
        return empty, pd.Series(dtype="bool")
    client_scope = diagnostics[
        diagnostics["entity_id"].notna() & diagnostics["product"].notna()
    ]
    if client_scope.empty:
        return pd.Series(dtype="int64"), pd.Series(dtype="bool")
    key = [client_scope["entity_id"].astype(str), client_scope["product"].astype(str)]
    counts = client_scope.groupby(key).size()
    high = client_scope.assign(_high=client_scope["severity"] == "HIGH").groupby(key)["_high"].any()
    return counts, high


def build_opportunity_engine(
    estimates: pd.DataFrame, diagnostics: pd.DataFrame
) -> pd.DataFrame:
    """Project the estimate table onto the published contract."""
    from . import opportunity

    classes = opportunity.classify_products(estimates).set_index("product")[
        "product_class"
    ]
    counts, high = _diagnostic_counts(diagnostics)

    result = estimates.copy()
    result["product_class"] = result["product"].map(classes)
    result["addressable_zar"] = pd.to_numeric(result["estimate_zar"], errors="coerce")
    result["opportunity_zar"] = pd.to_numeric(result["gap_zar"], errors="coerce")
    result["opportunity_basis"] = result["gap_basis"]

    index = pd.MultiIndex.from_arrays(
        [result["entity_id"].astype(str), result["product"].astype(str)]
    )
    result["diagnostic_count"] = (
        counts.reindex(index).fillna(0).astype("int64").to_numpy()
        if len(counts)
        else 0
    )
    result["high_severity_diagnostic"] = (
        high.reindex(index).fillna(False).astype(bool).to_numpy()
        if len(high)
        else False
    )

    # A product with no defensible rand denominator keeps NULL. Never a zero:
    # "we cannot size this" and "this is worth nothing" are opposite statements
    # and a fillna(0) anywhere downstream would merge them.
    for product in NO_RAND_DENOMINATOR:
        mask = result["product"] == product
        for column in ("addressable_zar", "opportunity_zar", "observed_zar"):
            result.loc[mask, column] = np.nan

    return result[list(OPPORTUNITY_ENGINE_COLUMNS)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# client_opportunity_profile -- one row per client
# ---------------------------------------------------------------------------

#: Pillar columns in the profile. Named here so the no-summation test can assert
#: that nothing in the table equals their total.
PILLAR_VALUE_COLUMNS = (
    "addressable_cash_flow_zar",
    "fx_addressable_zar",
    "trade_addressable_zar",
    "lending_opportunity_zar",
)

#: Products a "next product to investigate" recommendation may name. Investment
#: banking is excluded: it produces no rand figure, so it cannot be the subject
#: of a sizing conversation, and recommending it would send a banker to a page
#: with no number on it.
RECOMMENDABLE = tuple(
    product for product in assumptions.PRODUCTS if product != assumptions.IB
)


def _pick(group: pd.DataFrame, product: str, column: str) -> float:
    rows = group[group["product"] == product]
    if rows.empty:
        return np.nan
    value = rows.iloc[0][column]
    return float(value) if pd.notna(value) else np.nan


def build_client_profiles(
    estimates: pd.DataFrame, diagnostics: pd.DataFrame
) -> pd.DataFrame:
    """One row per client: every pillar's headline, side by side and never summed."""
    high_findings = (
        diagnostics[(diagnostics["severity"] == "HIGH") & diagnostics["entity_id"].notna()]
        if len(diagnostics)
        else diagnostics
    )

    rows = []
    for entity_id, group in estimates.groupby("entity_id", sort=True):
        first = group.iloc[0]
        core = group[group["product"].isin(assumptions.WALLET_PILLARS)]

        # The best opportunity on each ranking. Ties are already broken
        # deterministically upstream, so idxmin over the rank is stable.
        commercial_order = group.sort_values(["commercial_rank"])
        top = commercial_order.iloc[0]
        intensity_scored = group[group["opportunity_intensity"].notna()]
        top_intensity = (
            intensity_scored.sort_values("intensity_rank").iloc[0]
            if not intensity_scored.empty
            else None
        )

        recommendable = commercial_order[
            commercial_order["product"].isin(RECOMMENDABLE)
            & (commercial_order["product"] != top["product"])
        ]
        if recommendable.empty:
            next_product = None
            next_reason = (
                "No second product carries a rand figure for this client, so there is nothing "
                "further to investigate from the disclosed data."
            )
        else:
            candidate = recommendable.iloc[0]
            next_product = candidate["product"]
            next_reason = (
                f"Second-highest commercial opportunity score for this client "
                f"({candidate['commercial_opportunity_score']:.3f}, rank "
                f"{int(candidate['commercial_rank'])} of {len(estimates)} portfolio-wide) among "
                "products that produce a rand figure."
            )

        client_findings = (
            high_findings[high_findings["entity_id"] == entity_id]
            if len(high_findings)
            else high_findings
        )
        diagnostic_names = (
            ", ".join(sorted(set(client_findings["diagnostic"])))
            if len(client_findings)
            else ""
        )

        ib_row = group[group["product"] == assumptions.IB]
        rows.append(
            {
                "entity_id": entity_id,
                "entity_name": first["entity_name"],
                "sector": first["sector"],
                "fy_label": first["fy_label"],
                "fiscal_year_end": first["fiscal_year_end"],
                # --- Share of Wallet pillars, side by side, never summed -----
                "addressable_cash_flow_zar": _pick(
                    group, assumptions.CASH, "estimate_zar"
                ),
                "cash_management_wallet_zar": np.nan,
                "cash_observed_zar": _pick(group, assumptions.CASH, "observed_zar"),
                "cash_share_of_wallet": _pick(group, assumptions.CASH, "share"),
                "cash_opportunity_zar": _pick(group, assumptions.CASH, "gap_zar"),
                "fx_addressable_zar": _pick(group, assumptions.FX, "estimate_zar"),
                "fx_observed_zar": _pick(group, assumptions.FX, "observed_zar"),
                "fx_share_of_wallet": _pick(group, assumptions.FX, "share"),
                "fx_opportunity_zar": _pick(group, assumptions.FX, "gap_zar"),
                "trade_addressable_zar": _pick(group, assumptions.TRADE, "estimate_zar"),
                "trade_observed_zar": _pick(group, assumptions.TRADE, "observed_zar"),
                "trade_share_of_wallet": _pick(group, assumptions.TRADE, "share"),
                "trade_opportunity_zar": _pick(group, assumptions.TRADE, "gap_zar"),
                # --- opportunity signals ------------------------------------
                "lending_opportunity_zar": _pick(
                    group, assumptions.LENDING, "estimate_zar"
                ),
                "ib_signal_score": _pick(group, assumptions.IB, "signal_score"),
                "ib_opportunity_type": (
                    str(ib_row.iloc[0]["opportunity_type"])
                    if not ib_row.empty and "opportunity_type" in ib_row.columns
                    else None
                ),
                # --- rankings -----------------------------------------------
                "commercial_opportunity_score": float(
                    top["commercial_opportunity_score"]
                ),
                "commercial_rank": int(top["commercial_rank"]),
                "opportunity_intensity": (
                    float(top_intensity["opportunity_intensity"])
                    if top_intensity is not None
                    else np.nan
                ),
                "intensity_rank": (
                    int(top_intensity["intensity_rank"])
                    if top_intensity is not None
                    else pd.NA
                ),
                # --- quality ------------------------------------------------
                "top_opportunity_confidence": float(top["confidence"]),
                "top_opportunity_confidence_band": str(top["confidence_band"]),
                "mean_core_confidence": float(
                    pd.to_numeric(core["confidence"], errors="coerce").mean()
                ),
                "min_core_confidence": float(
                    pd.to_numeric(core["confidence"], errors="coerce").min()
                ),
                "major_diagnostic_count": int(len(client_findings)),
                "major_diagnostics": diagnostic_names,
                # --- what to do next ----------------------------------------
                "top_opportunity_product": top["product"],
                "top_opportunity_label": top["product_label"],
                "top_opportunity_zar": (
                    float(top["gap_zar"]) if pd.notna(top["gap_zar"]) else np.nan
                ),
                "top_intensity_product": (
                    top_intensity["product"] if top_intensity is not None else None
                ),
                "recommended_next_product": next_product,
                "recommended_next_reason": next_reason,
                "methodology_version": assumptions.METHODOLOGY_VERSION,
            }
        )

    profiles = pd.DataFrame(rows)
    # Cheap structural guard, run every build rather than only in the tests: if
    # a future edit adds a column that happens to equal the pillar total, this
    # fails loudly at build time instead of shipping a cross-pillar sum.
    assert_no_pillar_summation(profiles)
    return profiles


def assert_no_pillar_summation(profiles: pd.DataFrame) -> None:
    """Fail if any column equals the row-wise total of the pillar columns.

    The prohibition that matters most in this repository is the one against
    adding the transactional and cross-border pillars, because 279,389
    transactional rows sit on the SWIFT channel and overlap cross-border
    payments by an amount the supplied fields cannot resolve. This check is
    deliberately broader than that: no column may equal *any* pillar total.
    """
    present = [column for column in PILLAR_VALUE_COLUMNS if column in profiles.columns]
    if len(present) < 2:
        return
    total = profiles[present].sum(axis=1, min_count=1)
    if total.isna().all():
        return
    for column in profiles.columns:
        values = pd.to_numeric(profiles[column], errors="coerce")
        if values.isna().all():
            continue
        comparable = values.notna() & total.notna()
        if comparable.sum() == 0:
            continue
        if np.allclose(values[comparable], total[comparable], rtol=1e-9, atol=1.0):
            raise AssertionError(
                f"column {column!r} equals the sum of the pillar columns; the five pillars "
                "are not commensurable and must never be totalled"
            )
