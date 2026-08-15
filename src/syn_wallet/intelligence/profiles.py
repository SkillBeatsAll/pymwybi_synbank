"""One banker-facing row per client: every pillar side by side, never summed.

This is the table a client page renders from. It is deliberately wide and
deliberately flat -- five pillars' worth of figures with no arithmetic between
them -- because the one thing a relationship manager must not be able to do from
this screen is add up the five columns and quote the result.

The five rand figures are not commensurable. Two of them (cash and FX) overlap
by an unresolvable amount on the SWIFT channel; one is an accounting identity
and two are peer-benchmark expectations; lending has no observed side at all.
:func:`assert_no_cross_pillar_total` runs on every build, not just in the tests.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..wallet import assumptions
from ..wallet.common import pct, zar
from . import config, selection

#: Pillar rand columns. Named here so the no-summation guard knows what it is
#: forbidding the total of.
PILLAR_VALUE_COLUMNS = (
    "addressable_cash_flow_zar",
    "fx_addressable_zar",
    "trade_addressable_zar",
    "lending_opportunity_zar",
)

#: Pillar opportunity columns, which are equally forbidden from being totalled.
PILLAR_OPPORTUNITY_COLUMNS = (
    "cash_opportunity_zar",
    "fx_opportunity_zar",
    "trade_opportunity_zar",
    "lending_opportunity_zar",
)


def _pick(group: pd.DataFrame, product: str, column: str) -> Any:
    rows = group[group["product"] == product]
    if rows.empty:
        return np.nan
    value = rows.iloc[0][column]
    return value


def _numeric(group: pd.DataFrame, product: str, column: str) -> float:
    value = _pick(group, product, column)
    return float(value) if pd.notna(value) else np.nan


def _summary(record: dict[str, Any]) -> str:
    """A single sentence a banker can read without opening anything else."""
    if not record["has_primary_opportunity"]:
        return (
            f"{record['entity_name']} shows no pillar with demonstrated headroom: Syn Bank "
            "already handles essentially all of the activity the model can size, or no pillar "
            "could be sized. Treat as a retention relationship."
        )
    parts = [
        f"{record['entity_name']} ({record['sector']}, {record['fy_label']}): primary "
        f"opportunity is {record['primary_label']} at {record['primary_status']}"
    ]
    if pd.notna(record["primary_opportunity_zar"]):
        parts.append(f"{zar(record['primary_opportunity_zar'])} of unserved activity")
    parts.append(f"{record['primary_confidence_band']} confidence")
    if record["primary_sensitivity_flag"] in (config.SENSITIVE, config.MODERATE):
        parts.append(config.SENSITIVITY_PHRASE[record["primary_sensitivity_flag"]])
    sentence = ", ".join(parts) + "."
    if record["secondary_label"]:
        sentence += f" Secondary: {record['secondary_label']} ({record['secondary_status']})."
    if record["high_severity_flag"]:
        # Naming the affected pillars matters: a client-level count reads as
        # though the primary opportunity is flagged, and it usually is not --
        # a HIGH-severity diagnostic on a pillar blocks that pillar from
        # PRIORITY, so a PRIORITY primary is by construction unflagged.
        sentence += (
            f" {int(record['diagnostic_count_total'])} model diagnostics recorded across all "
            f"pillars, with HIGH severity on: {record['high_severity_pillars']} — review those "
            "before quoting their rand figures."
        )
    sentence += (
        " Syn Bank currently handles "
        f"{pct(record['cash_share'], 2)} of this client's Addressable Cash Flow."
        if pd.notna(record["cash_share"])
        else ""
    )
    return sentence


#: Columns published in ``client_opportunity_intelligence.parquet``.
PROFILE_COLUMNS = (
    # identity
    "entity_id",
    "entity_name",
    "sector",
    "fy_label",
    "fiscal_year_end",
    # --- CORE pillar 1: cash management ---------------------------------
    "addressable_cash_flow_zar",
    "cash_observed_zar",
    "cash_share",
    "cash_opportunity_zar",
    "cash_confidence",
    "cash_confidence_band",
    "cash_intensity_rank_in_product",
    "cash_status",
    # --- CORE pillar 2: FX ----------------------------------------------
    "fx_addressable_zar",
    "fx_observed_zar",
    "fx_share",
    "fx_opportunity_zar",
    "fx_confidence",
    "fx_confidence_band",
    "fx_intensity_rank_in_product",
    "fx_estimate_low",
    "fx_estimate_base",
    "fx_estimate_high",
    "fx_sensitivity_flag",
    "fx_rank_stability",
    "fx_status",
    # --- CORE pillar 3: trade -------------------------------------------
    "trade_addressable_zar",
    "trade_observed_zar",
    "trade_share",
    "trade_opportunity_zar",
    "trade_confidence",
    "trade_confidence_band",
    "trade_intensity_rank_in_product",
    "trade_estimate_low",
    "trade_estimate_base",
    "trade_estimate_high",
    "trade_sensitivity_flag",
    "trade_rank_stability",
    "trade_status",
    # --- SUPPORTING: lending --------------------------------------------
    "lending_opportunity_zar",
    "lending_confidence",
    "lending_confidence_band",
    "lending_intensity_rank_in_product",
    "lending_status",
    # --- SIGNAL_ONLY: investment banking --------------------------------
    "ib_signal_score",
    "ib_opportunity_type",
    "ib_confidence",
    "ib_confidence_band",
    "ib_status",
    # --- selection ------------------------------------------------------
    "has_primary_opportunity",
    "no_opportunity_reason",
    "primary_product",
    "primary_label",
    "primary_class",
    "primary_status",
    "primary_action",
    "primary_selection_score",
    "primary_commercial_score",
    "primary_confidence",
    "primary_confidence_band",
    "primary_opportunity_zar",
    "primary_headroom",
    "primary_sensitivity_flag",
    "secondary_product",
    "secondary_label",
    "secondary_status",
    "secondary_action",
    "secondary_selection_score",
    "secondary_opportunity_zar",
    "supporting_signal_product",
    "supporting_signal_label",
    "supporting_signal_status",
    # --- portfolio position ---------------------------------------------
    "commercial_opportunity_score",
    "commercial_rank",
    "opportunity_intensity",
    "intensity_rank",
    "mean_core_confidence",
    "min_core_confidence",
    "diagnostic_count_total",
    "high_severity_flag",
    "high_severity_pillars",
    "opportunity_summary",
    "intelligence_version",
    "methodology_version",
)

#: Per-pillar column prefixes, so the pillar blocks are built by loop rather
#: than by twenty-five near-identical lines.
PILLAR_PREFIX = {
    assumptions.CASH: "cash",
    assumptions.FX: "fx",
    assumptions.TRADE: "trade",
    assumptions.LENDING: "lending",
    assumptions.IB: "ib",
}


def build(
    scored: pd.DataFrame,
    sensitivity: pd.DataFrame,
    selections: pd.DataFrame,
) -> pd.DataFrame:
    """Assemble one intelligence row per client."""
    sensitivity_index = sensitivity.set_index(["entity_id", "product"])
    selection_index = selections.set_index("entity_id")

    rows: list[dict[str, Any]] = []
    for entity_id, group in scored.groupby("entity_id", sort=True):
        first = group.iloc[0]
        chosen = selection_index.loc[entity_id]
        core = group[group["product"].isin(assumptions.WALLET_PILLARS)]
        high_severity = group[group["high_severity_diagnostic"].fillna(False)]

        record: dict[str, Any] = {
            "entity_id": entity_id,
            "entity_name": first["entity_name"],
            "sector": first["sector"],
            "fy_label": first["fy_label"],
            "fiscal_year_end": first["fiscal_year_end"],
        }

        for product, prefix in PILLAR_PREFIX.items():
            confidence_column = f"{prefix}_confidence"
            record[confidence_column] = _numeric(group, product, "confidence")
            record[f"{prefix}_confidence_band"] = _pick(group, product, "confidence_band")
            record[f"{prefix}_status"] = _pick(group, product, "opportunity_status")
            if product != assumptions.IB:
                record[f"{prefix}_intensity_rank_in_product"] = _numeric(
                    group, product, "intensity_rank_in_product"
                )

        # Cash: the addressable figure keeps its own name everywhere.
        record["addressable_cash_flow_zar"] = _numeric(
            group, assumptions.CASH, "addressable_zar"
        )
        record["cash_observed_zar"] = _numeric(group, assumptions.CASH, "observed_zar")
        record["cash_share"] = _numeric(group, assumptions.CASH, "share")
        record["cash_opportunity_zar"] = _numeric(
            group, assumptions.CASH, "opportunity_zar"
        )

        for product, prefix in (
            (assumptions.FX, "fx"),
            (assumptions.TRADE, "trade"),
        ):
            record[f"{prefix}_addressable_zar"] = _numeric(group, product, "addressable_zar")
            record[f"{prefix}_observed_zar"] = _numeric(group, product, "observed_zar")
            record[f"{prefix}_share"] = _numeric(group, product, "share")
            record[f"{prefix}_opportunity_zar"] = _numeric(group, product, "opportunity_zar")
            key = (entity_id, product)
            sensitivity_row = (
                sensitivity_index.loc[key] if key in sensitivity_index.index else None
            )
            for suffix in ("estimate_low", "estimate_base", "estimate_high"):
                record[f"{prefix}_{suffix}"] = (
                    float(sensitivity_row[suffix])
                    if sensitivity_row is not None and pd.notna(sensitivity_row[suffix])
                    else np.nan
                )
            record[f"{prefix}_sensitivity_flag"] = (
                sensitivity_row["sensitivity_flag"]
                if sensitivity_row is not None
                else config.NOT_APPLICABLE
            )
            record[f"{prefix}_rank_stability"] = (
                sensitivity_row["rank_stability"]
                if sensitivity_row is not None
                else config.NOT_APPLICABLE
            )

        # Lending publishes an opportunity, never a share.
        record["lending_opportunity_zar"] = _numeric(
            group, assumptions.LENDING, "opportunity_zar"
        )
        # Investment banking publishes a signal, never a rand.
        record["ib_signal_score"] = _numeric(group, assumptions.IB, "signal_score")
        ib_rows = group[group["product"] == assumptions.IB]
        record["ib_opportunity_type"] = (
            ib_rows.iloc[0]["ib_opportunity_type"]
            if not ib_rows.empty and "ib_opportunity_type" in ib_rows.columns
            else None
        )

        record["has_primary_opportunity"] = bool(chosen["has_primary_opportunity"])
        record["no_opportunity_reason"] = chosen["no_opportunity_reason"]
        for slot in selection.SLOTS:
            for field in ("product", "label", "status"):
                record[f"{slot}_{field}"] = chosen[f"{slot}_{field}"]
        for field in (
            "class",
            "action",
            "selection_score",
            "commercial_score",
            "confidence",
            "confidence_band",
            "opportunity_zar",
            "sensitivity_flag",
        ):
            record[f"primary_{field}"] = chosen[f"primary_{field}"]
        for field in ("action", "selection_score", "opportunity_zar"):
            record[f"secondary_{field}"] = chosen[f"secondary_{field}"]

        primary_product = chosen["primary_product"]
        record["primary_headroom"] = (
            _numeric(group, primary_product, "headroom_fraction")
            if primary_product is not None
            else np.nan
        )

        # Portfolio position: the client's best row on each ranking.
        best = group.sort_values("commercial_rank").iloc[0]
        record["commercial_opportunity_score"] = float(best["commercial_opportunity_score"])
        record["commercial_rank"] = int(best["commercial_rank"])
        intensity_rows = group[group["opportunity_intensity"].notna()]
        top_intensity = (
            intensity_rows.sort_values("intensity_rank").iloc[0]
            if not intensity_rows.empty
            else None
        )
        record["opportunity_intensity"] = (
            float(top_intensity["opportunity_intensity"]) if top_intensity is not None else np.nan
        )
        record["intensity_rank"] = (
            int(top_intensity["intensity_rank"]) if top_intensity is not None else pd.NA
        )

        record["mean_core_confidence"] = float(
            pd.to_numeric(core["confidence"], errors="coerce").mean()
        )
        record["min_core_confidence"] = float(
            pd.to_numeric(core["confidence"], errors="coerce").min()
        )
        record["diagnostic_count_total"] = int(
            pd.to_numeric(group["diagnostic_count"], errors="coerce").fillna(0).sum()
        )
        record["high_severity_flag"] = bool(len(high_severity) > 0)
        record["high_severity_pillars"] = ", ".join(sorted(high_severity["product"]))
        record["opportunity_summary"] = _summary(record)
        record["intelligence_version"] = config.INTELLIGENCE_VERSION
        record["methodology_version"] = first["methodology_version"]
        rows.append(record)

    profiles = pd.DataFrame(rows)[list(PROFILE_COLUMNS)]
    assert_no_cross_pillar_total(profiles)
    return profiles


def assert_no_cross_pillar_total(profiles: pd.DataFrame) -> None:
    """Fail the build if any column equals a total across the pillars.

    Run on every build rather than only in the tests, because the failure mode
    it guards against -- a column quietly added later that happens to be the sum
    -- would otherwise ship looking like a headline figure.
    """
    for columns, description in (
        (PILLAR_VALUE_COLUMNS, "addressable"),
        (PILLAR_OPPORTUNITY_COLUMNS, "opportunity"),
    ):
        present = [column for column in columns if column in profiles.columns]
        if len(present) < 2:
            continue
        total = profiles[present].sum(axis=1, min_count=1)
        if total.isna().all():
            continue
        for column in profiles.columns:
            values = pd.to_numeric(profiles[column], errors="coerce")
            comparable = values.notna() & total.notna()
            if comparable.sum() == 0:
                continue
            if np.allclose(values[comparable], total[comparable], rtol=1e-9, atol=1.0):
                raise AssertionError(
                    f"column {column!r} equals the cross-pillar {description} total; the five "
                    "pillars are not commensurable and must never be added"
                )
