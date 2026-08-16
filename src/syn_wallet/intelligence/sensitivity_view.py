"""How far each rand figure moves when the arguable coefficients move.

Reads ``model_sensitivity.parquet`` -- 36 full model runs -- and collapses it to
one row per client x product: the base estimate, the range across every tested
assumption, and whether the client's position within its pillar survives that
range.

The point is not to hedge. It is that "R193bn of FX headroom" and "somewhere
between R45bn and R330bn of FX headroom, most likely R193bn" are different
statements, and only the second is true. A banker who quotes the first in a
client meeting and is challenged has nothing to fall back on.

Cash management comes out of this with a zero-width range, because both its
coefficients are accounting identities and no scenario can touch it. That is
worth saying explicitly rather than leaving as an absence.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..wallet import assumptions
from ..wallet import sensitivity as sweep
from . import config

#: Columns published per client x product.
SENSITIVITY_COLUMNS = (
    "entity_id",
    "product",
    "estimate_base",
    "estimate_low",
    "estimate_high",
    "estimate_range_pct",
    "opportunity_base",
    "opportunity_low",
    "opportunity_high",
    "opportunity_range_pct",
    "share_low",
    "share_high",
    "rank_base",
    "rank_low",
    "rank_high",
    "rank_swing",
    "rank_stability",
    "sensitivity_flag",
    "sensitivity_phrase",
    "rank_stability_phrase",
    "scenarios_tested",
)


def _classify_range(range_pct: float | None) -> str:
    if range_pct is None or pd.isna(range_pct):
        return config.NOT_APPLICABLE
    if range_pct <= config.SENSITIVITY_STABLE_RANGE:
        return config.STABLE
    if range_pct <= config.SENSITIVITY_MODERATE_RANGE:
        return config.MODERATE
    return config.SENSITIVE


def _classify_rank(swing: float | None) -> str:
    if swing is None or pd.isna(swing):
        return config.NOT_APPLICABLE
    if swing <= config.RANK_STABLE_SWING:
        return config.STABLE
    if swing <= config.RANK_MODERATE_SWING:
        return config.MODERATE
    return config.SENSITIVE


def build(sensitivity: pd.DataFrame) -> pd.DataFrame:
    """Collapse the scenario sweep to one row per client x product."""
    base_label = sweep.base_config().label
    frame = sensitivity.copy()
    scenarios = int(frame["scenario"].nunique())

    base = (
        frame[frame["scenario"] == base_label]
        .set_index(["entity_id", "product"])[
            ["estimate_zar", "gap_zar", "commercial_rank_in_product"]
        ]
        .rename(
            columns={
                "estimate_zar": "estimate_base",
                "gap_zar": "opportunity_base",
                "commercial_rank_in_product": "rank_base",
            }
        )
    )
    if base.empty:
        raise ValueError(
            f"model_sensitivity.parquet contains no base scenario {base_label!r}; "
            "rebuild with `build_wallet --overwrite --sensitivity`"
        )

    grouped = frame.groupby(["entity_id", "product"])
    spread = pd.DataFrame(
        {
            "estimate_low": grouped["estimate_zar"].min(),
            "estimate_high": grouped["estimate_zar"].max(),
            "opportunity_low": grouped["gap_zar"].min(),
            "opportunity_high": grouped["gap_zar"].max(),
            "share_low": grouped["share"].min(),
            "share_high": grouped["share"].max(),
            "rank_low": grouped["commercial_rank_in_product"].min(),
            "rank_high": grouped["commercial_rank_in_product"].max(),
        }
    )

    result = base.join(spread).reset_index()

    # Range as a fraction of the base. Expressed against the base rather than
    # against the low end, so "40%" means "plus or minus a fifth of what we
    # published" rather than an unbounded ratio when the low end approaches zero.
    for prefix in ("estimate", "opportunity"):
        low = pd.to_numeric(result[f"{prefix}_low"], errors="coerce")
        high = pd.to_numeric(result[f"{prefix}_high"], errors="coerce")
        base_values = pd.to_numeric(result[f"{prefix}_base"], errors="coerce")
        span = (high - low) / base_values.where(base_values > 0)
        result[f"{prefix}_range_pct"] = span.replace([np.inf, -np.inf], np.nan)

    result["rank_swing"] = pd.to_numeric(
        result["rank_high"], errors="coerce"
    ) - pd.to_numeric(result["rank_low"], errors="coerce")

    # A pillar with no rand estimate has nothing to be sensitive about, and
    # saying "STABLE" would imply a well-supported figure that does not exist.
    no_rand = result["product"].isin(
        [product for product in assumptions.PRODUCTS if product == assumptions.IB]
    )
    result["sensitivity_flag"] = [
        config.NOT_APPLICABLE if flagged else _classify_range(value)
        for flagged, value in zip(no_rand, result["estimate_range_pct"])
    ]
    result["rank_stability"] = [
        config.NOT_APPLICABLE if flagged else _classify_rank(value)
        for flagged, value in zip(no_rand, result["rank_swing"])
    ]
    result["sensitivity_phrase"] = result["sensitivity_flag"].map(config.SENSITIVITY_PHRASE)
    result["rank_stability_phrase"] = result["rank_stability"].map(
        config.RANK_STABILITY_PHRASE
    )
    result["scenarios_tested"] = scenarios

    return result[list(SENSITIVITY_COLUMNS)].sort_values(["entity_id", "product"]).reset_index(
        drop=True
    )


def empty(estimates: pd.DataFrame) -> pd.DataFrame:
    """A sensitivity view for a run with no sweep available.

    Every field is NULL and every flag is ``NOT_APPLICABLE``, so a caller that
    forgot ``--sensitivity`` gets an intelligence layer that says "not tested"
    rather than one that silently implies stability.
    """
    keys = estimates[["entity_id", "product"]].drop_duplicates().reset_index(drop=True)
    for column in SENSITIVITY_COLUMNS:
        if column in ("entity_id", "product"):
            continue
        keys[column] = np.nan
    keys["sensitivity_flag"] = config.NOT_APPLICABLE
    keys["rank_stability"] = config.NOT_APPLICABLE
    keys["sensitivity_phrase"] = "not tested in this run"
    keys["rank_stability_phrase"] = "not tested in this run"
    keys["scenarios_tested"] = 0
    return keys[list(SENSITIVITY_COLUMNS)]
