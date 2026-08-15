"""Which opportunity a banker should look at first, and why that one.

**The naive answer is "the biggest rand number", and it is wrong.** The five
pillars produce rand figures on incomparable bases, and their evidence quality
differs by a factor of three: cash management averages 0.86 confidence, FX 0.35.
Sorting on rand puts a weakly evidenced, benchmark-sensitive FX figure above a
well-evidenced financing need every time, and a relationship manager who acts on
it walks into a meeting with a number they cannot defend.

So selection multiplies the commercial score by what is known about it::

    selection_score = commercial_opportunity_score
                    x role_weight[product_class]        CORE 1.00 / SUPPORTING 0.85 / SIGNAL 0.55
                    x confidence_weight[band]           HIGH 1.00 / MEDIUM 0.80 / LOW 0.55
                    x (1 - 0.20 if high_severity_diagnostic)
                    x (1 - 0.10 if benchmark-sensitive)

Worked through on the brief's own example: a LOW-confidence FX row scoring 0.75
lands at ``0.75 x 1.00 x 0.55 = 0.41``, and lower still if it carries a
diagnostic. A HIGH-confidence lending row scoring 0.60 lands at
``0.60 x 0.85 x 1.00 = 0.51`` and wins, regardless of which has more rand behind
it. That is the intended behaviour and a test asserts it.

Rows with no demonstrated headroom are excluded from selection entirely. A
pillar where Syn Bank already handles everything the model can size is a
retention conversation; offering it as a growth opportunity would be a false
positive of the most embarrassing kind.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..wallet import assumptions
from . import config

PRIMARY = "primary"
SECONDARY = "secondary"
SUPPORTING = "supporting_signal"

#: The three selected slots, in order.
SLOTS = (PRIMARY, SECONDARY, SUPPORTING)

#: Deterministic tie-break. Selection score first, then the model's own
#: commercial score, then confidence, then a stable alphabetical key, so two
#: runs on identical inputs always choose the same primary.
TIE_BREAK = ("commercial_opportunity_score", "confidence", "entity_id", "product")


def _headroom_fraction(row: pd.Series) -> float | None:
    """Opportunity as a fraction of the addressable figure, where both exist."""
    addressable = row["addressable_zar"]
    opportunity = row["opportunity_zar"]
    if pd.isna(addressable) or pd.isna(opportunity) or addressable <= 0:
        return None
    return float(opportunity / addressable)


def score(estimates: pd.DataFrame, sensitivity: pd.DataFrame) -> pd.DataFrame:
    """Add the selection score, status and headroom columns to every row."""
    frame = estimates.merge(
        sensitivity[["entity_id", "product", "sensitivity_flag", "rank_stability"]],
        on=["entity_id", "product"],
        how="left",
        validate="one_to_one",
    )
    frame["sensitivity_flag"] = frame["sensitivity_flag"].fillna(config.NOT_APPLICABLE)
    frame["rank_stability"] = frame["rank_stability"].fillna(config.NOT_APPLICABLE)

    frame["headroom_fraction"] = frame.apply(_headroom_fraction, axis=1)
    frame["has_rand_basis"] = frame["product"] != assumptions.IB

    role = frame["product_class"].map(config.ROLE_WEIGHT).astype("float64")
    confidence = frame["confidence_band"].map(config.CONFIDENCE_WEIGHT).astype("float64")
    diagnostic = np.where(
        frame["high_severity_diagnostic"].fillna(False), 1.0 - config.HIGH_SEVERITY_PENALTY, 1.0
    )
    sensitive = np.where(
        frame["sensitivity_flag"] == config.SENSITIVE, 1.0 - config.SENSITIVITY_PENALTY, 1.0
    )
    commercial = pd.to_numeric(
        frame["commercial_opportunity_score"], errors="coerce"
    ).fillna(0.0)

    frame["selection_role_weight"] = role
    frame["selection_confidence_weight"] = confidence
    frame["selection_diagnostic_factor"] = diagnostic
    frame["selection_sensitivity_factor"] = sensitive
    frame["selection_score"] = (commercial * role * confidence * diagnostic * sensitive).clip(
        0.0, 1.0
    )

    statuses = [
        config.classify_status(
            product_class=row.product_class,
            confidence_band=row.confidence_band,
            commercial_score=float(row.commercial_opportunity_score),
            high_severity_diagnostic=bool(row.high_severity_diagnostic),
            headroom_fraction=row.headroom_fraction,
            has_rand_basis=bool(row.has_rand_basis),
            entity_id=row.entity_id,
            product=row.product,
        )
        for row in frame.itertuples()
    ]
    frame["opportunity_status"] = [status for status, _ in statuses]
    frame["status_reason"] = [reason for _, reason in statuses]
    frame["status_action"] = frame["opportunity_status"].map(config.STATUS_ACTION)
    frame["status_note"] = frame["opportunity_status"].map(config.STATUS_NOTE)
    return frame


def _order(group: pd.DataFrame) -> pd.DataFrame:
    return group.sort_values(
        ["selection_score", *TIE_BREAK], ascending=[False, False, False, True, True]
    )


def assign_slots(scored: pd.DataFrame) -> pd.DataFrame:
    """Pick each client's primary, secondary and supporting opportunity.

    * **Primary** — highest selection score among pillars that demonstrated
      headroom.
    * **Secondary** — the next one down, same population.
    * **Supporting signal** — the highest-scoring remaining pillar that is
      ``SUPPORTING`` or ``SIGNAL_ONLY`` class, or is not HIGH confidence. The
      slot exists to carry the softer evidence into the conversation, so it
      deliberately prefers a signal-grade row over a third CORE pillar; if no
      such row is left it falls back to the next-best remaining pillar.

    A client with no pillar showing headroom gets no primary, and its
    ``no_opportunity_reason`` says so. That is a real state, not a gap to fill.
    """
    result = scored.copy()
    result["selection_slot"] = pd.Series(pd.NA, index=result.index, dtype="object")
    result["selection_rank_for_client"] = pd.Series(pd.NA, index=result.index, dtype="Int64")

    for _, group in result.groupby("entity_id", sort=False):
        eligible = _order(group[group["opportunity_status"] != config.NO_HEADROOM])
        result.loc[eligible.index, "selection_rank_for_client"] = np.arange(
            1, len(eligible) + 1
        )
        if eligible.empty:
            continue

        result.at[eligible.index[0], "selection_slot"] = PRIMARY
        if len(eligible) > 1:
            result.at[eligible.index[1], "selection_slot"] = SECONDARY

        remaining = eligible.iloc[2:]
        if remaining.empty:
            continue
        soft = remaining[
            remaining["product_class"].isin(
                [assumptions.SUPPORTING, assumptions.SIGNAL_ONLY]
            )
            | (remaining["confidence_band"] != "HIGH")
        ]
        chosen = soft.index[0] if not soft.empty else remaining.index[0]
        result.at[chosen, "selection_slot"] = SUPPORTING

    return result


def client_selection_summary(scored: pd.DataFrame) -> pd.DataFrame:
    """One row per client naming its three slots, or the no-opportunity state."""
    rows = []
    for entity_id, group in scored.groupby("entity_id", sort=True):
        first = group.iloc[0]
        slots = {
            str(row["selection_slot"]): row
            for _, row in group.iterrows()
            if pd.notna(row["selection_slot"])
        }
        eligible = group[group["opportunity_status"] != config.NO_HEADROOM]
        record: dict[str, object] = {
            "entity_id": entity_id,
            "entity_name": first["entity_name"],
            "sector": first["sector"],
            "fy_label": first["fy_label"],
            "has_primary_opportunity": PRIMARY in slots,
            "eligible_pillar_count": int(len(eligible)),
            "no_opportunity_reason": (
                ""
                if PRIMARY in slots
                else (
                    "No pillar demonstrated headroom: Syn Bank already handles essentially all "
                    "of the activity the model can size for this client, or no pillar could be "
                    "sized at all."
                )
            ),
        }
        for slot in SLOTS:
            row = slots.get(slot)
            prefix = slot
            record[f"{prefix}_product"] = row["product"] if row is not None else None
            record[f"{prefix}_label"] = row["product_label"] if row is not None else None
            record[f"{prefix}_class"] = row["product_class"] if row is not None else None
            record[f"{prefix}_status"] = (
                row["opportunity_status"] if row is not None else None
            )
            record[f"{prefix}_action"] = row["status_action"] if row is not None else None
            record[f"{prefix}_selection_score"] = (
                float(row["selection_score"]) if row is not None else np.nan
            )
            record[f"{prefix}_commercial_score"] = (
                float(row["commercial_opportunity_score"]) if row is not None else np.nan
            )
            record[f"{prefix}_confidence"] = (
                float(row["confidence"]) if row is not None else np.nan
            )
            record[f"{prefix}_confidence_band"] = (
                row["confidence_band"] if row is not None else None
            )
            record[f"{prefix}_opportunity_zar"] = (
                float(row["opportunity_zar"])
                if row is not None and pd.notna(row["opportunity_zar"])
                else np.nan
            )
            record[f"{prefix}_sensitivity_flag"] = (
                row["sensitivity_flag"] if row is not None else None
            )
        rows.append(record)
    return pd.DataFrame(rows)
