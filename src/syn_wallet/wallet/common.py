"""Shared machinery every pillar model uses: share guards, bands, formatting.

Keeping the share calculation in one place matters more than it looks. Every
pillar has to answer the same awkward questions -- what if the denominator is
NULL, zero or negative; what if observed activity exceeds the estimated wallet --
and answering them differently per pillar is how a portfolio ends up with one
product quoting 400% share and another quoting nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import assumptions

# ---------------------------------------------------------------------------
# Share and gap, with every degenerate case named
# ---------------------------------------------------------------------------

#: Reasons a share could not be computed, or had to be treated.
SHARE_OK = "observed_over_estimated_wallet"
SHARE_NO_DENOMINATOR = "no_defensible_denominator"
SHARE_NON_POSITIVE_DENOMINATOR = "non_positive_denominator"
SHARE_NO_OBSERVED = "no_observed_activity_in_dataset"
SHARE_CAPPED = "capped_observed_exceeds_estimate"

#: ``benchmark_level`` for a pillar that uses no peer coefficient at all.
NO_BENCHMARK = "not_applicable"


def apply_observed_floor(
    estimate: pd.Series, observed: pd.Series
) -> tuple[pd.Series, pd.Series]:
    """Raise an estimated wallet to at least the activity already flowing.

    Addressable wallet cannot be smaller than observed activity: business Syn
    Bank is already doing is, by definition, addressable by Syn Bank. When the
    modelled driver lands below observed, the driver is wrong for that client --
    an insurer's cross-border flow is reinsurance and investment movement, not
    the trade settlement the exposure model is built on.

    Publishing the raw ratio would give Sanlam a 1,353% FX share. Flooring gives
    a defensible 100% and a flag saying the model could not demonstrate headroom,
    which is the honest conclusion. The unfloored value is retained as
    ``estimate_modelled_zar`` so the size of the failure stays auditable.

    The published estimate is also floored at zero, so a driver that goes
    negative -- a restated or mis-signed disclosure -- can never surface as a
    negative wallet. A zero estimate then produces a NULL share rather than a
    division, which is the correct answer for "we cannot size this".

    Returns ``(published_estimate, floored_mask)``.
    """
    estimate = pd.to_numeric(estimate, errors="coerce").astype("float64")
    observed = pd.to_numeric(observed, errors="coerce").astype("float64")
    comparable = estimate.notna() & observed.notna()
    floored = comparable & (observed > estimate)
    published = estimate.copy()
    published[floored] = observed[floored]
    below_zero = published.notna() & (published < 0)
    published[below_zero] = 0.0
    return published, (floored | below_zero)


@dataclass
class ShareResult:
    share: pd.Series
    share_uncapped: pd.Series
    basis: pd.Series
    gap: pd.Series
    gap_basis: pd.Series
    flags: pd.Series


def share_and_gap(
    observed: pd.Series,
    estimate: pd.Series,
    observed_available: bool = True,
) -> ShareResult:
    """Compute share and gap, naming every case rather than silently coercing.

    * NULL or non-positive estimate -> share NULL, gap NULL. No division.
    * observed above estimate -> share capped at 1.0, uncapped value retained,
      gap floored at zero, and a diagnostic flag raised. A wallet smaller than
      the activity already flowing through the bank is a model finding that
      needs review, not a number to publish at 340%.
    * no observed data for the pillar at all (lending, investment banking) ->
      share NULL with an explicit basis, and the whole estimate reported as the
      gap.
    """
    index = estimate.index
    estimate = pd.to_numeric(estimate, errors="coerce").astype("float64")
    observed = pd.to_numeric(observed, errors="coerce").astype("float64")

    usable_denominator = estimate.notna() & (estimate > 0)
    flags = pd.Series("", index=index, dtype="object")

    uncapped = pd.Series(np.nan, index=index, dtype="float64")
    basis = pd.Series(SHARE_NO_DENOMINATOR, index=index, dtype="object")
    basis[estimate.notna() & (estimate <= 0)] = SHARE_NON_POSITIVE_DENOMINATOR

    if observed_available:
        computable = usable_denominator & observed.notna()
        uncapped[computable] = observed[computable] / estimate[computable]
        basis[computable] = SHARE_OK
        basis[usable_denominator & observed.isna()] = SHARE_NO_OBSERVED
    else:
        basis[:] = SHARE_NO_OBSERVED

    share = uncapped.clip(upper=assumptions.SHARE_CAP)
    over = uncapped.notna() & (uncapped > assumptions.SHARE_CAP)
    basis[over] = SHARE_CAPPED
    flags[over] = "observed_exceeds_estimate"

    if observed_available:
        gap = (estimate - observed.fillna(0.0)).clip(lower=0.0)
        gap = gap.where(usable_denominator, np.nan)
        gap_basis = pd.Series("estimated_wallet_less_observed", index=index, dtype="object")
        gap_basis[~usable_denominator] = "not_computable_no_denominator"
        gap_basis[over] = "floored_at_zero_observed_exceeds_estimate"
    else:
        gap = estimate.where(usable_denominator, np.nan)
        gap_basis = pd.Series("full_estimate_no_observed_activity_in_dataset", index=index, dtype="object")
        gap_basis[~usable_denominator] = "not_computable_no_denominator"

    return ShareResult(share, uncapped, basis, gap, gap_basis, flags)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def zar(value: float | None) -> str:
    """Format a rand amount at a scale a reader can hold in their head.

    The sign sits outside the currency symbol, so a negative working-capital
    cycle reads as ``-R9.25bn`` rather than ``R-9.25bn``.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "not available"
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1e12:
        return f"{sign}R{magnitude / 1e12:,.2f}tn"
    if magnitude >= 1e9:
        return f"{sign}R{magnitude / 1e9:,.2f}bn"
    if magnitude >= 1e6:
        return f"{sign}R{magnitude / 1e6:,.1f}m"
    if magnitude >= 1e3:
        return f"{sign}R{magnitude / 1e3:,.1f}k"
    return f"{sign}R{magnitude:,.0f}"


def count(value: float | None) -> str:
    """Format a non-monetary count. Never prefixed with a currency symbol."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "not available"
    return f"{value:,.0f}"


def pct(value: float | None, places: int = 2) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "not available"
    return f"{value * 100:.{places}f}%"


# ---------------------------------------------------------------------------
# Flag accumulation
# ---------------------------------------------------------------------------


class FlagSet:
    """Accumulates per-client diagnostic flags into one comma-separated column."""

    def __init__(self, index: pd.Index) -> None:
        self._flags: dict[str, pd.Series] = {}
        self._index = index

    def add(self, name: str, condition: pd.Series) -> None:
        self._flags[name] = condition.fillna(False).astype(bool)

    def series(self) -> pd.Series:
        result = pd.Series("", index=self._index, dtype="object")
        for name, condition in self._flags.items():
            result = result.where(~condition, result.str.cat(pd.Series(name, index=self._index), sep=","))
        return result.str.lstrip(",")

    def as_frame(self) -> pd.DataFrame:
        if not self._flags:
            return pd.DataFrame(index=self._index)
        return pd.DataFrame(self._flags, index=self._index)


# ---------------------------------------------------------------------------
# Standard pillar output
# ---------------------------------------------------------------------------

#: Columns every pillar model returns, in order. ``opportunity_score`` and the
#: rank columns are filled by :mod:`.opportunity` once all pillars exist.
ESTIMATE_COLUMNS = (
    "entity_id",
    "entity_name",
    "sector",
    "fy_label",
    "fiscal_year_end",
    "product",
    "product_label",
    "pillar_role",
    "estimate_basis",
    "estimate_kind",
    "observed_zar",
    "estimate_zar",
    "estimate_modelled_zar",
    "share",
    "share_uncapped",
    "share_basis",
    "gap_zar",
    "gap_basis",
    "confidence",
    "confidence_band",
    "benchmark_level",
    "benchmark_n",
    "benchmark_fallback_reason",
    "out_of_scope_observed_zar",
    "overlap_excluded_zar",
    "signal_score",
    "explanation",
    "diagnostic_flags",
    "methodology_version",
)


@dataclass
class PillarOutput:
    """One pillar's estimates plus its component and driver detail."""

    estimates: pd.DataFrame
    components: pd.DataFrame
    confidence_detail: pd.DataFrame
    flags: pd.DataFrame
    #: One record per client x benchmark metric: the coefficient actually used.
    benchmarks: list[dict[str, Any]] = field(default_factory=list)
    #: One record per benchmark metric describing the whole peer population.
    benchmark_metrics: list[dict[str, Any]] = field(default_factory=list)


def component_rows(
    frame: pd.DataFrame,
    product: str,
    components: dict[str, pd.Series],
    drivers: dict[str, tuple[pd.Series, pd.Series]],
) -> pd.DataFrame:
    """Long-format component breakdown: one row per client x component.

    ``drivers`` maps a component name to its ``(driver_value, driver_source)``
    pair so the breakdown records not just how much each component contributed
    but what it was built from and whether that input was disclosed or imputed.
    """
    rows = []
    for name, values in components.items():
        driver_value, driver_source = drivers.get(name, (None, None))
        rows.append(
            pd.DataFrame(
                {
                    "entity_id": frame["entity_id"].to_numpy(),
                    "entity_name": frame["entity_name"].to_numpy(),
                    "sector": frame["sector"].to_numpy(),
                    "product": product,
                    "component": name,
                    "component_zar": pd.to_numeric(values, errors="coerce").to_numpy(),
                    "driver_value_zar": (
                        pd.to_numeric(driver_value, errors="coerce").to_numpy()
                        if driver_value is not None
                        else np.nan
                    ),
                    "driver_source": (
                        driver_source.to_numpy() if driver_source is not None else "n/a"
                    ),
                }
            )
        )
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def assemble(
    frame: pd.DataFrame,
    product: str,
    observed: pd.Series | None,
    estimate: pd.Series,
    share_result: ShareResult,
    confidence: pd.Series,
    confidence_band: pd.Series,
    explanation: pd.Series,
    flags: pd.Series,
    estimate_kind: str,
    estimate_modelled: pd.Series | None = None,
    out_of_scope_observed: pd.Series | None = None,
    overlap_excluded: pd.Series | None = None,
    signal_score: pd.Series | None = None,
    benchmark_level: pd.Series | None = None,
    benchmark_n: pd.Series | None = None,
    benchmark_fallback_reason: pd.Series | None = None,
) -> pd.DataFrame:
    """Build one pillar's slice of ``wallet_estimates`` in the standard schema."""
    nan = pd.Series(np.nan, index=frame.index, dtype="float64")
    result = pd.DataFrame(
        {
            "entity_id": frame["entity_id"],
            "entity_name": frame["entity_name"],
            "sector": frame["sector"],
            "fy_label": frame["fy_label"],
            "fiscal_year_end": frame["fiscal_year_end"],
            "product": product,
            "product_label": assumptions.PRODUCT_LABELS[product],
            "pillar_role": assumptions.PILLAR_ROLE[product],
            "estimate_basis": assumptions.ESTIMATE_BASIS[product],
            "estimate_kind": estimate_kind,
            "observed_zar": observed if observed is not None else nan,
            "estimate_zar": estimate,
            "estimate_modelled_zar": (
                estimate_modelled if estimate_modelled is not None else estimate
            ),
            "share": share_result.share,
            "share_uncapped": share_result.share_uncapped,
            "share_basis": share_result.basis,
            "gap_zar": share_result.gap,
            "gap_basis": share_result.gap_basis,
            "confidence": confidence,
            "confidence_band": confidence_band,
            # Which peer population set this pillar's coefficients for this
            # client. NULL where the pillar uses no peer benchmark at all --
            # cash is an accounting identity, investment banking is a signal.
            "benchmark_level": (
                benchmark_level
                if benchmark_level is not None
                else pd.Series(NO_BENCHMARK, index=frame.index, dtype="object")
            ),
            "benchmark_n": (
                benchmark_n
                if benchmark_n is not None
                else pd.Series(pd.NA, index=frame.index, dtype="Int64")
            ),
            "benchmark_fallback_reason": (
                benchmark_fallback_reason
                if benchmark_fallback_reason is not None
                else pd.Series(
                    "pillar_uses_no_peer_benchmark", index=frame.index, dtype="object"
                )
            ),
            "out_of_scope_observed_zar": (
                out_of_scope_observed if out_of_scope_observed is not None else nan
            ),
            "overlap_excluded_zar": overlap_excluded if overlap_excluded is not None else nan,
            "signal_score": signal_score if signal_score is not None else nan,
            "explanation": explanation,
            "diagnostic_flags": flags,
            "methodology_version": assumptions.METHODOLOGY_VERSION,
        }
    )
    return result[list(ESTIMATE_COLUMNS)]
