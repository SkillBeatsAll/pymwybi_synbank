"""Portfolio-level intelligence, in a shape a dashboard can render directly.

Everything here is a **long** table -- ``section, metric, product, sector,
entity_id, value_numeric, value_text, rank, note`` -- rather than a wide one.
Nine different summaries with nine different grains do not share a schema, and
forcing them to would mean either nine tables or a wide table full of NULLs. A
long table lets a dashboard filter to one ``section`` and render it without
knowing anything else about the layer.

**No section totals rand across pillars.** Product-level figures are reported
per product and stop there. The portfolio block reports each pillar's observed
and addressable figures on separate rows, deliberately, so that no consumer can
pick up a single "total opportunity" number that does not exist.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..wallet import assumptions
from ..wallet.common import count, pct, zar
from . import config

#: Columns of the long portfolio table.
PORTFOLIO_COLUMNS = (
    "section",
    "metric",
    "product",
    "product_label",
    "sector",
    "entity_id",
    "entity_name",
    "rank",
    "value_numeric",
    "value_text",
    "note",
    "intelligence_version",
)

PRODUCT_ORDER = {product: position for position, product in enumerate(assumptions.PRODUCTS)}


def _row(
    section: str,
    metric: str,
    value_numeric: float | None = None,
    value_text: str = "",
    product: str | None = None,
    sector: str | None = None,
    entity_id: str | None = None,
    entity_name: str | None = None,
    rank: int | None = None,
    note: str = "",
) -> dict[str, Any]:
    return {
        "section": section,
        "metric": metric,
        "product": product,
        "product_label": assumptions.PRODUCT_LABELS.get(product) if product else None,
        "sector": sector,
        "entity_id": entity_id,
        "entity_name": entity_name,
        "rank": rank,
        "value_numeric": (
            float(value_numeric)
            if value_numeric is not None and pd.notna(value_numeric)
            else np.nan
        ),
        "value_text": value_text,
        "note": note,
        "intelligence_version": config.INTELLIGENCE_VERSION,
    }


# ---------------------------------------------------------------------------
# Portfolio-level dashboard-safe metrics
# ---------------------------------------------------------------------------


def _portfolio_metrics(
    scored: pd.DataFrame, sensitivity: pd.DataFrame
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    section = "portfolio_position"

    for product in assumptions.PRODUCTS:
        group = scored[scored["product"] == product]
        sensitivity_group = sensitivity[sensitivity["product"] == product]
        observed = pd.to_numeric(group["observed_zar"], errors="coerce").sum(min_count=1)
        addressable = pd.to_numeric(group["addressable_zar"], errors="coerce").sum(min_count=1)
        opportunity = pd.to_numeric(group["opportunity_zar"], errors="coerce").sum(min_count=1)

        if product == assumptions.IB:
            distribution = group["ib_opportunity_type"].value_counts()
            for category, clients in distribution.items():
                rows.append(
                    _row(
                        section,
                        "ib_signal_category_clients",
                        clients,
                        f"{clients} clients",
                        product=product,
                        note=f"Category `{category}`. Signal only; no rand amount exists.",
                    )
                )
            rows.append(
                _row(
                    section,
                    "ib_median_signal_score",
                    group["signal_score"].median(),
                    f"{group['signal_score'].median():.2f}",
                    product=product,
                    note="Median investment-banking opportunity signal across the portfolio.",
                )
            )
            continue

        rows.append(
            _row(
                section,
                "observed_zar",
                observed,
                zar(observed),
                product=product,
                note="Activity Syn Bank actually handled in the fiscal year.",
            )
        )
        rows.append(
            _row(
                section,
                "addressable_zar",
                addressable,
                zar(addressable),
                product=product,
                note=(
                    "Addressable Cash Flow: the clients' own operating turnover, not bank income."
                    if product == assumptions.CASH
                    else config.DENOMINATOR_LABEL[product].capitalize() + "."
                ),
            )
        )
        rows.append(
            _row(
                section,
                "opportunity_zar",
                opportunity,
                zar(opportunity),
                product=product,
                note="Addressable activity not observed in Syn Bank's data.",
            )
        )
        if product in assumptions.WALLET_PILLARS and pd.notna(addressable) and addressable > 0:
            rows.append(
                _row(
                    section,
                    "value_weighted_share",
                    observed / addressable,
                    pct(observed / addressable),
                    product=product,
                    note="Summed observed over summed addressable across all twenty clients.",
                )
            )

        # Published for every rand pillar, not only the volatile ones. Cash
        # management's zero-width range is a finding in its own right: it says
        # the figure is an identity rather than a coefficient, and leaving the
        # cell blank would read as "not tested".
        if not sensitivity_group.empty:
            low = pd.to_numeric(sensitivity_group["opportunity_low"], errors="coerce").sum(
                min_count=1
            )
            high = pd.to_numeric(sensitivity_group["opportunity_high"], errors="coerce").sum(
                min_count=1
            )
            rows.append(
                _row(
                    section,
                    "opportunity_range_low_zar",
                    low,
                    zar(low),
                    product=product,
                    note="Lowest total across every tested benchmark assumption.",
                )
            )
            rows.append(
                _row(
                    section,
                    "opportunity_range_high_zar",
                    high,
                    zar(high),
                    product=product,
                    note="Highest total across every tested benchmark assumption.",
                )
            )
            spans = pd.notna(low) and pd.notna(high) and high > low
            rows.append(
                _row(
                    section,
                    "opportunity_range_text",
                    value_text=(
                        f"{zar(low)} to {zar(high)}, base case {zar(opportunity)}"
                        if spans
                        else f"{zar(opportunity)}, identical in every tested scenario"
                    ),
                    product=product,
                    note=(
                        "Present this pillar as a range. The denominator is a peer benchmark, "
                        "not a disclosed total, so the coefficient choice moves the answer."
                        if spans
                        else "No tested assumption moves this figure."
                    ),
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Product-level dashboard-safe metrics
# ---------------------------------------------------------------------------


def _product_metrics(
    scored: pd.DataFrame, sensitivity: pd.DataFrame
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    section = "product_metrics"

    for product in assumptions.PRODUCTS:
        group = scored[scored["product"] == product]
        sensitivity_group = sensitivity[sensitivity["product"] == product]
        share = pd.to_numeric(group["share"], errors="coerce")
        confidence = pd.to_numeric(group["confidence"], errors="coerce")
        observed = pd.to_numeric(group["observed_zar"], errors="coerce").sum(min_count=1)
        addressable = pd.to_numeric(group["addressable_zar"], errors="coerce").sum(min_count=1)

        common = {"product": product}
        rows.append(
            _row(
                section,
                "product_class",
                value_text=str(group["product_class"].iloc[0]),
                note=str(group["pillar_role"].iloc[0]),
                **common,
            )
        )
        rows.append(
            _row(
                section,
                "median_client_share",
                share.median(),
                pct(share.median()) if share.notna().any() else "no share computed",
                note=(
                    "The unweighted middle client."
                    if share.notna().any()
                    else "This pillar publishes no share of wallet."
                ),
                **common,
            )
        )
        rows.append(
            _row(
                section,
                "value_weighted_share",
                (observed / addressable)
                if pd.notna(addressable) and addressable > 0 and pd.notna(observed)
                else None,
                pct(observed / addressable)
                if pd.notna(addressable) and addressable > 0 and pd.notna(observed)
                else "no share computed",
                note="Differs from the median wherever one client dominates the totals.",
                **common,
            )
        )
        rows.append(
            _row(
                section,
                "mean_confidence",
                confidence.mean(),
                f"{confidence.mean():.2f}",
                **common,
            )
        )
        rows.append(
            _row(
                section,
                "opportunity_count",
                int((group["opportunity_status"] != config.NO_HEADROOM).sum()),
                f"{int((group['opportunity_status'] != config.NO_HEADROOM).sum())} clients",
                note="Clients where the model demonstrated headroom in this pillar.",
                **common,
            )
        )
        rows.append(
            _row(
                section,
                "high_confidence_opportunity_count",
                int(
                    (
                        (group["opportunity_status"] != config.NO_HEADROOM)
                        & (group["confidence_band"] == "HIGH")
                    ).sum()
                ),
                f"{int(((group['opportunity_status'] != config.NO_HEADROOM) & (group['confidence_band'] == 'HIGH')).sum())} clients",
                note="Headroom demonstrated on HIGH confidence.",
                **common,
            )
        )
        for status in config.STATUS_ORDER:
            clients = int((group["opportunity_status"] == status).sum())
            rows.append(
                _row(
                    section,
                    f"status_{status.lower()}_count",
                    clients,
                    f"{clients} clients",
                    note=config.STATUS_ACTION[status],
                    **common,
                )
            )

        if not sensitivity_group.empty:
            flags = sensitivity_group["sensitivity_flag"]
            dominant = flags.mode().iloc[0] if not flags.mode().empty else config.NOT_APPLICABLE
            rows.append(
                _row(
                    section,
                    "sensitivity_level",
                    value_text=str(dominant),
                    note=config.SENSITIVITY_PHRASE.get(str(dominant), ""),
                    **common,
                )
            )
    return rows


# ---------------------------------------------------------------------------
# The nine listings
# ---------------------------------------------------------------------------


def _listings(scored: pd.DataFrame, profiles: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    # 1. Top opportunities by product.
    for product in assumptions.PRODUCTS:
        group = scored[
            (scored["product"] == product)
            & (scored["opportunity_status"] != config.NO_HEADROOM)
        ].sort_values("selection_score", ascending=False)
        for position, (_, row) in enumerate(group.head(5).iterrows(), start=1):
            rows.append(
                _row(
                    "top_by_product",
                    "selection_score",
                    row["selection_score"],
                    (
                        zar(row["opportunity_zar"])
                        if pd.notna(row["opportunity_zar"])
                        else f"signal {row['signal_score']:.2f}"
                    ),
                    product=product,
                    sector=row["sector"],
                    entity_id=row["entity_id"],
                    entity_name=row["entity_name"],
                    rank=position,
                    note=f"{row['opportunity_status']} — {row['confidence_band']} confidence.",
                )
            )

    # 2. Top opportunities by client, on the primary slot.
    primary = profiles[profiles["has_primary_opportunity"]].sort_values(
        "primary_selection_score", ascending=False
    )
    for position, (_, row) in enumerate(primary.head(10).iterrows(), start=1):
        rows.append(
            _row(
                "top_by_client",
                "primary_selection_score",
                row["primary_selection_score"],
                (
                    zar(row["primary_opportunity_zar"])
                    if pd.notna(row["primary_opportunity_zar"])
                    else "signal only"
                ),
                product=row["primary_product"],
                sector=row["sector"],
                entity_id=row["entity_id"],
                entity_name=row["entity_name"],
                rank=position,
                note=f"{row['primary_status']} — {row['primary_action']}.",
            )
        )

    # 3. Product penetration distribution.
    for product in assumptions.WALLET_PILLARS:
        share = pd.to_numeric(
            scored.loc[scored["product"] == product, "share"], errors="coerce"
        ).dropna()
        for label, value in (
            ("min", share.min()),
            ("p25", share.quantile(0.25)),
            ("median", share.median()),
            ("p75", share.quantile(0.75)),
            ("max", share.max()),
        ):
            rows.append(
                _row(
                    "penetration_distribution",
                    f"share_{label}",
                    value,
                    pct(value),
                    product=product,
                    note="Share of wallet distribution across the twenty clients.",
                )
            )

    # 4. Confidence distribution.
    for product in assumptions.PRODUCTS:
        group = scored[scored["product"] == product]
        for band in ("HIGH", "MEDIUM", "LOW"):
            clients = int((group["confidence_band"] == band).sum())
            rows.append(
                _row(
                    "confidence_distribution",
                    f"clients_{band.lower()}",
                    clients,
                    f"{clients} of {len(group)}",
                    product=product,
                    note=f"{clients / len(group):.0%} of the portfolio at {band} confidence.",
                )
            )

    # 5. Largest observable gaps, within each pillar so the bases stay separate.
    for product in assumptions.PRODUCTS:
        if product == assumptions.IB:
            continue
        group = scored[scored["product"] == product].sort_values(
            "opportunity_zar", ascending=False
        )
        for position, (_, row) in enumerate(group.head(3).iterrows(), start=1):
            rows.append(
                _row(
                    "largest_gaps",
                    "opportunity_zar",
                    row["opportunity_zar"],
                    zar(row["opportunity_zar"]),
                    product=product,
                    sector=row["sector"],
                    entity_id=row["entity_id"],
                    entity_name=row["entity_name"],
                    rank=position,
                    note=(
                        f"{row['confidence_band']} confidence. Ranked within this pillar only: "
                        "rand figures are not comparable across pillars."
                    ),
                )
            )

    # 6. Highest cash-flow penetration -- where Syn Bank is already strongest.
    cash = scored[scored["product"] == assumptions.CASH].sort_values("share", ascending=False)
    for position, (_, row) in enumerate(cash.head(5).iterrows(), start=1):
        rows.append(
            _row(
                "highest_cash_penetration",
                "cash_share",
                row["share"],
                pct(row["share"]),
                product=assumptions.CASH,
                sector=row["sector"],
                entity_id=row["entity_id"],
                entity_name=row["entity_name"],
                rank=position,
                note=(
                    f"Syn Bank handles {pct(row['share'])} of this client's Addressable Cash "
                    "Flow — the strongest positions in the portfolio."
                ),
            )
        )

    # 7. Low-confidence, high-value: the rows most likely to be misquoted.
    risky = scored[
        (scored["confidence_band"] == "LOW") & scored["opportunity_zar"].notna()
    ].sort_values("opportunity_zar", ascending=False)
    for position, (_, row) in enumerate(risky.head(10).iterrows(), start=1):
        rows.append(
            _row(
                "low_confidence_high_value",
                "opportunity_zar",
                row["opportunity_zar"],
                zar(row["opportunity_zar"]),
                product=row["product"],
                sector=row["sector"],
                entity_id=row["entity_id"],
                entity_name=row["entity_name"],
                rank=position,
                note=(
                    f"Confidence {float(row['confidence']):.2f} (LOW), status "
                    f"{row['opportunity_status']}. Validate before pursuing; do not quote the "
                    "rand figure unqualified."
                ),
            )
        )

    # 8. Clients with several simultaneous opportunities.
    for entity_id, group in scored.groupby("entity_id", sort=False):
        actionable = group[
            group["opportunity_status"].isin([config.PRIORITY, config.INVESTIGATE])
        ]
        if len(actionable) < 2:
            continue
        rows.append(
            _row(
                "multiple_opportunities",
                "actionable_pillar_count",
                len(actionable),
                ", ".join(sorted(actionable["product"])),
                sector=group.iloc[0]["sector"],
                entity_id=entity_id,
                entity_name=group.iloc[0]["entity_name"],
                note=(
                    f"{len(actionable)} pillars at PRIORITY or INVESTIGATE. Breadth of "
                    "opportunity, not a combined rand figure — the pillars are not additive."
                ),
            )
        )

    # 8b. How concentrated the primary slot is. If one pillar wins for almost
    # every client, the primary opportunity carries little client-specific
    # information and the differentiation lives in the secondary slot. That is a
    # property of the evidence -- cash management is the only pillar with HIGH
    # confidence across the portfolio -- and it should be stated, not discovered.
    primary_counts = (
        profiles.loc[profiles["has_primary_opportunity"], "primary_product"]
        .value_counts()
        .sort_values(ascending=False)
    )
    clients_with_primary = int(profiles["has_primary_opportunity"].sum())
    for position, (product, clients) in enumerate(primary_counts.items(), start=1):
        rows.append(
            _row(
                "primary_concentration",
                "clients_with_this_primary",
                clients,
                f"{clients} of {clients_with_primary} clients",
                product=product,
                rank=position,
                note=(
                    f"{clients / clients_with_primary:.0%} of clients with a primary "
                    "opportunity land on this pillar."
                ),
            )
        )
    if not primary_counts.empty:
        dominant_product = str(primary_counts.index[0])
        dominant_share = int(primary_counts.iloc[0]) / clients_with_primary
        rows.append(
            _row(
                "primary_concentration",
                "concentration_warning",
                dominant_share,
                f"{dominant_share:.0%} on {dominant_product}",
                product=dominant_product,
                note=(
                    "The primary slot is dominated by one pillar because it is the only one with "
                    "HIGH confidence across most of the portfolio. Read the secondary and "
                    "supporting slots for client-specific differentiation; the primary slot "
                    "mostly restates where the evidence is strongest, not where the client "
                    "differs from its peers."
                    if dominant_share >= 0.7
                    else "Primary opportunities are spread across several pillars."
                ),
            )
        )

    # 9. Product opportunity concentration by sector.
    for product in assumptions.PRODUCTS:
        if product == assumptions.IB:
            continue
        group = scored[scored["product"] == product]
        totals = (
            pd.to_numeric(group["opportunity_zar"], errors="coerce")
            .groupby(group["sector"])
            .sum(min_count=1)
            .sort_values(ascending=False)
        )
        product_total = totals.sum()
        for position, (sector, value) in enumerate(totals.items(), start=1):
            rows.append(
                _row(
                    "sector_concentration",
                    "opportunity_zar",
                    value,
                    zar(value),
                    product=product,
                    sector=sector,
                    rank=position,
                    note=(
                        f"{value / product_total:.0%} of this pillar's opportunity."
                        if pd.notna(product_total) and product_total > 0
                        else ""
                    ),
                )
            )

    return rows


def build(
    scored: pd.DataFrame, profiles: pd.DataFrame, sensitivity: pd.DataFrame
) -> pd.DataFrame:
    """The whole portfolio intelligence table, long format."""
    rows = (
        _portfolio_metrics(scored, sensitivity)
        + _product_metrics(scored, sensitivity)
        + _listings(scored, profiles)
    )
    frame = pd.DataFrame(rows, columns=list(PORTFOLIO_COLUMNS))
    frame["_product_order"] = frame["product"].map(PRODUCT_ORDER).fillna(99)
    frame = (
        frame.sort_values(
            ["section", "_product_order", "metric", "rank", "entity_id"],
            na_position="last",
            kind="stable",
        )
        .drop(columns="_product_order")
        .reset_index(drop=True)
    )
    return frame


def client_level_metrics(profiles: pd.DataFrame) -> pd.DataFrame:
    """The compact client-level card a dashboard shows in a list view."""
    return pd.DataFrame(
        {
            "entity_id": profiles["entity_id"],
            "entity_name": profiles["entity_name"],
            "sector": profiles["sector"],
            "primary_opportunity": profiles["primary_label"],
            "primary_opportunity_product": profiles["primary_product"],
            "primary_opportunity_score": profiles["primary_selection_score"],
            "primary_opportunity_zar": profiles["primary_opportunity_zar"],
            "confidence": profiles["primary_confidence"],
            "confidence_band": profiles["primary_confidence_band"],
            "headroom": profiles["primary_headroom"],
            "sensitivity": profiles["primary_sensitivity_flag"],
            "status": profiles["primary_status"],
            "next_action": profiles["primary_action"],
            "high_severity_flag": profiles["high_severity_flag"],
            "intelligence_version": config.INTELLIGENCE_VERSION,
        }
    ).sort_values("primary_opportunity_score", ascending=False, na_position="last").reset_index(
        drop=True
    )
