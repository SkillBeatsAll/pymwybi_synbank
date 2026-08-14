"""A computed confidence score, not a label somebody chose.

**Directness is a ceiling, not a vote.** Four factors describe how good the
*inputs* are and combine additively. A fifth -- ``evidence_directness``, how
sound the *method* is -- then multiplies that total. Having every input a model
needs cannot make a weak model confident, and an earlier additive version proved
the point: it scored all twenty investment-banking estimates HIGH, a pillar whose
every threshold is an undrawn judgement, purely because the balance-sheet fields
behind it were fully disclosed.

============================  ======  =========================================
Factor                        Weight  What it measures
============================  ======  =========================================
``input_completeness``          0.35  Fraction of the pillar's economic drivers
                                      the client actually disclosed, rather than
                                      being imputed from peers.
``sector_applicability``        0.25  Whether the model's economic logic applies
                                      to this sector at all -- an insurer scored
                                      for import letters of credit should not
                                      read as confident.
``observation_support``         0.20  How much internal activity backs the
                                      estimate, log-scaled against the busiest
                                      client in the pillar. Thin observation is
                                      a thin basis for a share.
``internal_consistency``        0.20  Whether related disclosures agree: the
                                      gross-debt identity, the revenue split,
                                      and whether the revenue denominator is an
                                      as-reported figure or a constructed one.
----------------------------  ------  -----------------------------------------
``evidence_directness``      MULTIPLY How far the method sits from an accounting
                                      identity. Identity-anchored scores 1.0; a
                                      peer-benchmark coefficient 0.60; a
                                      judgement threshold 0.35. Scaled down
                                      further when a component could not be
                                      built at all, or when the wallet had to be
                                      floored at observed activity.
============================  ======  =========================================

Bands: HIGH at 0.70 and above, MEDIUM at 0.45, LOW below that.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import assumptions

#: Additive factors describing input quality. These sum to 1.0.
WEIGHTS = {
    "input_completeness": 0.35,
    "sector_applicability": 0.25,
    "observation_support": 0.20,
    "internal_consistency": 0.20,
}

#: ``evidence_directness`` is applied multiplicatively on top of the weighted
#: sum above, so it caps confidence rather than being outvoted by it.
DIRECTNESS_IS_MULTIPLICATIVE = True

#: Directness of each coefficient basis, used to score ``evidence_directness``.
BASIS_DIRECTNESS = {
    assumptions.ACCOUNTING_IDENTITY: 1.00,
    assumptions.STRUCTURAL: 0.90,
    assumptions.PORTFOLIO_BENCHMARK: 0.60,
    assumptions.JUDGEMENT: 0.35,
}


#: How far confidence is cut when the modelled wallet had to be floored at
#: observed activity. A floored estimate means the pillar's economic driver did
#: not work for that client at all, which is a far bigger problem than an
#: imputed input, and confidence has to say so.
FLOOR_PENALTY = 0.50


def effective_directness(
    value_weighted: pd.Series,
    components_realised: pd.Series,
    components_expected: pd.Series,
    floored: pd.Series | None = None,
) -> pd.Series:
    """Scale value-weighted directness by how much of the model actually ran.

    Weighting directness purely by component *value* hides a missing component,
    because a component that could not be built carries zero weight and so
    cannot pull the average down. OUTsurance, with no cost of sales at all,
    scored the same directness as Shoprite with everything disclosed. Scaling by
    the fraction of expected components that were realised fixes that: half a
    model is half as direct.
    """
    realised = pd.to_numeric(components_realised, errors="coerce").fillna(0.0)
    expected = pd.to_numeric(components_expected, errors="coerce").replace(0.0, np.nan)
    scaled = value_weighted.fillna(0.0) * (realised / expected).clip(0.0, 1.0)
    if floored is not None:
        scaled = scaled * (1.0 - FLOOR_PENALTY * floored.fillna(False).astype("float64"))
    return scaled.clip(0.0, 1.0)


@dataclass
class ConfidenceResult:
    score: pd.Series
    band: pd.Series
    detail: pd.DataFrame


def observation_support(counts: pd.Series) -> pd.Series:
    """Log-scaled activity support, 0 at no activity and 1 at the busiest client.

    Log rather than linear because the difference between 800 and 8,000
    transactions is real evidence, while the difference between 600,000 and
    900,000 is not. The reference point is measured from the portfolio, so no
    threshold is invented.
    """
    values = pd.to_numeric(counts, errors="coerce").fillna(0.0).clip(lower=0.0)
    reference = float(values.max())
    if reference <= 0:
        return pd.Series(0.0, index=counts.index, dtype="float64")
    return (np.log10(1.0 + values) / np.log10(1.0 + reference)).clip(0.0, 1.0)


def internal_consistency(
    frame: pd.DataFrame,
    uses_revenue: bool = True,
    uses_revenue_split: bool = False,
    uses_debt_structure: bool = False,
) -> pd.Series:
    """Penalise an estimate whose supporting disclosures disagree with each other.

    Starts at 1.0 and deducts for each specific, named inconsistency. A NULL
    identity flag means the identity could not be checked (a leg was not
    disclosed), which is treated as neutral rather than as a failure.
    """
    score = pd.Series(1.0, index=frame.index, dtype="float64")
    if uses_revenue:
        soft = frame["revenue_total_is_soft_basis"].fillna(False).astype(bool)
        score -= 0.25 * soft
    if uses_revenue_split:
        failed = frame["revenue_split_identity_ok"].eq(False).fillna(False)
        score -= 0.35 * failed
    if uses_debt_structure:
        failed = frame["gross_debt_identity_ok"].eq(False).fillna(False)
        score -= 0.35 * failed
    return score.clip(0.0, 1.0)


def score(
    input_completeness: pd.Series,
    evidence_directness: pd.Series,
    sector_applicability: pd.Series,
    observation: pd.Series,
    consistency: pd.Series,
) -> ConfidenceResult:
    """Combine the input factors, then cap the result by method directness."""
    factors = pd.DataFrame(
        {
            "input_completeness": input_completeness.clip(0.0, 1.0),
            "sector_applicability": sector_applicability.clip(0.0, 1.0),
            "observation_support": observation.clip(0.0, 1.0),
            "internal_consistency": consistency.clip(0.0, 1.0),
        }
    ).fillna(0.0)

    input_quality = sum(factors[name] * weight for name, weight in WEIGHTS.items())
    directness = evidence_directness.clip(0.0, 1.0).fillna(0.0)
    factors["evidence_directness"] = directness
    factors["input_quality"] = input_quality
    total = (input_quality * directness).clip(0.0, 1.0)

    band = pd.Series("LOW", index=total.index, dtype="object")
    band[total >= assumptions.CONFIDENCE_BAND_MEDIUM] = "MEDIUM"
    band[total >= assumptions.CONFIDENCE_BAND_HIGH] = "HIGH"

    detail = factors.copy()
    detail["confidence"] = total
    detail["confidence_band"] = band
    return ConfidenceResult(total, band, detail)


def weights_registry() -> list[dict[str, object]]:
    """The confidence weights and basis directness table, for the run report."""
    rows = [
        {"kind": "additive_factor_weight", "name": name, "value": value}
        for name, value in WEIGHTS.items()
    ]
    rows.append(
        {"kind": "multiplicative_factor", "name": "evidence_directness", "value": 1.0}
    )
    rows += [
        {"kind": "basis_directness", "name": name, "value": value}
        for name, value in BASIS_DIRECTNESS.items()
    ]
    rows += [
        {"kind": "band_floor", "name": "HIGH", "value": assumptions.CONFIDENCE_BAND_HIGH},
        {"kind": "band_floor", "name": "MEDIUM", "value": assumptions.CONFIDENCE_BAND_MEDIUM},
    ]
    return rows
