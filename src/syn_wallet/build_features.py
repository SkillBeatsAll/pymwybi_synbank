"""Role A — Layer 2: build ``internal_features.csv`` from the cleaned Layer 0 tables.

Input: the three DataFrames produced by ``clean_data.py`` (transactional_banking,
cross_border_payments, trade_finance), already deduplicated, currency-canonicalised,
with conflicting identifiers flagged (not dropped).

Output schema (the agreed A/C handoff contract — do not change without a team sync):

    entity_id, entity_name, sector, pillar, observed_flow_zar, exposure_days,
    product_breadth, recency_days, trend_pct

One row per (entity, pillar), pillar in {cash_mgmt, trade_finance, fx, lending_dcm}.

This module intentionally encodes every internal-data watch-out from the execution
brief so Person C's Layer 2 receives numbers that are already safe to compare against
Layer 1's disclosed-statement-derived addressable flow:

* ``intercompany_sweeps`` are excluded from the cash-management observed flow — they
  are internal treasury movements with no matching income-statement line and would
  badly distort the ratio (~R201bn of the ~R405bn raw transactional total).
* Trade finance is measured as exposure (value x tenor_days / 365), not raw
  instrument value, and issued/active instruments are weighted separately from
  settled/expired ones rather than aggregated as if equivalent.
* FX uses cross-border payment data only. Transactional SWIFT rows are never summed
  in — the data audit found no reconcilable match on entity/date/direction/amount/
  beneficiary/reference between the two files.
* Cross-border ``intercompany`` corridor volume is excluded from the FX pillar for
  the same reason sweeps are excluded from cash management: it is not counterparty-
  facing flow that a client's disclosed ``revenue_foreign`` would imply.
* Lending/DCM has no signal anywhere in the supplied internal data. It is returned
  as NaN (UNOBSERVABLE), never zero-filled — silently scoring it 0% share would
  overstate the opportunity, which the brief explicitly warns against.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

CASH_LEG_TYPES = ("collections", "supplier_payments", "payroll", "tax")
EXCLUDED_LEG_TYPE = "intercompany_sweeps"
LIVE_TRADE_STATUSES = ("issued", "active")
HISTORICAL_TRADE_STATUSES = ("settled", "expired")
EXCLUDED_FX_CORRIDOR = "intercompany"
PILLARS = ("cash_mgmt", "trade_finance", "fx", "lending_dcm")


@dataclass(frozen=True)
class AnnualisationWindow:
    """The most recent 12 months of the supplied 2023-07-01..2026-06-30 range.

    Annualising on the latest 12 months (rather than averaging three years) is what
    lines the internal data up with a single fiscal year of financial statements, per
    the brief's Layer 0 instruction.
    """

    start: pd.Timestamp
    end: pd.Timestamp

    @property
    def days(self) -> int:
        return int((self.end - self.start).days) + 1

    @classmethod
    def latest_12_months(cls, as_of: pd.Timestamp) -> "AnnualisationWindow":
        end = pd.Timestamp(as_of)
        start = end - pd.DateOffset(years=1) + pd.Timedelta(days=1)
        return cls(start=start, end=end)


def _in_window(dates: pd.Series, window: AnnualisationWindow) -> pd.Series:
    return (dates >= window.start) & (dates <= window.end)


def _annualisation_factor(window: AnnualisationWindow) -> float:
    """Scale a window's total to a 365-day year. A no-op when the window is already ~1yr."""
    return 365.0 / window.days


# --------------------------------------------------------------------------- #
# Pillar 1 — Cash Management & Payments
# --------------------------------------------------------------------------- #

def build_cash_mgmt(transactional: pd.DataFrame, window: AnnualisationWindow) -> pd.DataFrame:
    """Observed cash-management flow: collections + supplier_payments + payroll + tax.

    Excludes intercompany_sweeps (see module docstring). Sweeps are summarised
    separately by ``sweeps_diagnostic`` — they are evidence of liquidity-management
    depth, not part of the addressable-flow ratio.
    """
    df = transactional[_in_window(transactional["date"], window)].copy()
    cash_df = df[df["leg_type"].isin(CASH_LEG_TYPES)]
    factor = _annualisation_factor(window)

    observed = (
        cash_df.groupby("entity_id")["amount_zar"].sum().mul(factor).rename("observed_flow_zar")
    )
    recency = (
        cash_df.groupby("entity_id")["date"].max()
        .apply(lambda d: (window.end - d).days)
        .rename("recency_days")
    )
    out = pd.concat([observed, recency], axis=1).reset_index()
    out["pillar"] = "cash_mgmt"
    out["exposure_days"] = np.nan  # not a tenor-based product
    return out


def sweeps_diagnostic(transactional: pd.DataFrame, window: AnnualisationWindow) -> pd.DataFrame:
    """Intercompany sweep volume, reported separately — never part of Share of Flow."""
    df = transactional[_in_window(transactional["date"], window)]
    sweeps = df[df["leg_type"] == EXCLUDED_LEG_TYPE]
    factor = _annualisation_factor(window)
    return (
        sweeps.groupby("entity_id")["amount_zar"].sum().mul(factor)
        .rename("intercompany_sweep_zar_annualised").reset_index()
    )


# --------------------------------------------------------------------------- #
# Pillar 2 — Trade Finance
# --------------------------------------------------------------------------- #

def build_trade_finance(trade_finance: pd.DataFrame, window: AnnualisationWindow) -> pd.DataFrame:
    """Observed trade-finance activity: annualised issuance value (coverage ratio)
    plus exposure-days (value x tenor_days / 365), split live vs historical by status.

    ``observed_flow_zar`` uses all instruments issued in the window regardless of
    current status — issuance in the period is real coverage regardless of whether
    the instrument has since settled. ``exposure_days`` is reported for *live*
    instruments only (issued/active), since that reflects the bank's current book,
    not its historical one — settled/expired instruments no longer represent
    standing exposure. This is the exposure-day weighting the brief calls for.
    """
    df = trade_finance[_in_window(trade_finance["date"], window)].copy()
    factor = _annualisation_factor(window)

    issuance = df.groupby("entity_id")["value_zar"].sum().mul(factor).rename("observed_flow_zar")

    live = df[df["status"].isin(LIVE_TRADE_STATUSES)].copy()
    live["exposure_days_value"] = live["value_zar"] * live["tenor_days"] / 365.0
    exposure = live.groupby("entity_id")["exposure_days_value"].sum().rename("exposure_days")

    recency = (
        df.groupby("entity_id")["date"].max()
        .apply(lambda d: (window.end - d).days)
        .rename("recency_days")
    )

    out = pd.concat([issuance, exposure, recency], axis=1).reset_index()
    out["pillar"] = "trade_finance"
    return out


def trade_finance_status_diagnostic(trade_finance: pd.DataFrame, window: AnnualisationWindow) -> pd.DataFrame:
    """Instrument value by entity x status, for the methodology write-up.

    Surfaces exactly the "do not aggregate all four as if equivalent" check the
    brief calls for — live vs historical exposure should visibly differ.
    """
    df = trade_finance[_in_window(trade_finance["date"], window)]
    return (
        df.groupby(["entity_id", "status"])["value_zar"].sum()
        .unstack(fill_value=0.0).reset_index()
    )


# --------------------------------------------------------------------------- #
# Pillar 3 — FX / Global Markets
# --------------------------------------------------------------------------- #

def build_fx(cross_border: pd.DataFrame, window: AnnualisationWindow) -> pd.DataFrame:
    """Observed FX flow: annualised cross-border value, trade + other corridors only.

    Excludes the ``intercompany`` corridor for the same reason sweeps are excluded
    from cash management (see module docstring), and excludes transactional SWIFT
    rows entirely — the audit found no reconcilable overlap between the two files,
    so summing them would double count an unknown fraction of flow.
    """
    df = cross_border[_in_window(cross_border["date"], window)].copy()
    fx_df = df[df["corridor_type"] != EXCLUDED_FX_CORRIDOR]
    factor = _annualisation_factor(window)

    observed = fx_df.groupby("entity_id")["value_zar"].sum().mul(factor).rename("observed_flow_zar")
    recency = (
        fx_df.groupby("entity_id")["date"].max()
        .apply(lambda d: (window.end - d).days)
        .rename("recency_days")
    )
    out = pd.concat([observed, recency], axis=1).reset_index()
    out["pillar"] = "fx"
    out["exposure_days"] = np.nan  # not a tenor-based product
    return out


def fx_intercompany_diagnostic(cross_border: pd.DataFrame, window: AnnualisationWindow) -> pd.DataFrame:
    """Intercompany cross-border volume, reported separately — same rationale as sweeps."""
    df = cross_border[_in_window(cross_border["date"], window)]
    intercompany = df[df["corridor_type"] == EXCLUDED_FX_CORRIDOR]
    factor = _annualisation_factor(window)
    return (
        intercompany.groupby("entity_id")["value_zar"].sum().mul(factor)
        .rename("intercompany_fx_zar_annualised").reset_index()
    )


# --------------------------------------------------------------------------- #
# Pillar 4 — Lending / DCM (structurally unobservable)
# --------------------------------------------------------------------------- #

def build_lending(entity_ids: pd.Series) -> pd.DataFrame:
    """No lending, facility, drawdown or balance field exists in any supplied dataset.

    Returns NaN observed_flow_zar for every entity — UNOBSERVABLE, not zero. Layer 2
    downstream must not treat this NaN as "no relationship"; it means "no signal".
    """
    out = pd.DataFrame({"entity_id": entity_ids.unique()})
    out["observed_flow_zar"] = np.nan
    out["exposure_days"] = np.nan
    out["recency_days"] = np.nan
    out["pillar"] = "lending_dcm"
    return out


# --------------------------------------------------------------------------- #
# Cross-pillar features: product_breadth, trend_pct
# --------------------------------------------------------------------------- #

def compute_product_breadth(
    transactional: pd.DataFrame, cross_border: pd.DataFrame, trade_finance: pd.DataFrame,
    window: AnnualisationWindow,
) -> pd.Series:
    """Count of the three internal datasets (transactional ex-sweeps, cross-border
    ex-intercompany, trade finance) in which a client shows any activity in the
    window. An entity-level relationship-depth signal (0-3), reused across every
    pillar row for that entity — this is what Layer 4's Propensity term consumes.
    """
    t = transactional[_in_window(transactional["date"], window)]
    t_active = set(t.loc[t["leg_type"] != EXCLUDED_LEG_TYPE, "entity_id"].unique())

    c = cross_border[_in_window(cross_border["date"], window)]
    c_active = set(c.loc[c["corridor_type"] != EXCLUDED_FX_CORRIDOR, "entity_id"].unique())

    f = trade_finance[_in_window(trade_finance["date"], window)]
    f_active = set(f["entity_id"].unique())

    all_entities = t_active | c_active | f_active
    breadth = {
        eid: int(eid in t_active) + int(eid in c_active) + int(eid in f_active)
        for eid in all_entities
    }
    return pd.Series(breadth, name="product_breadth")


def compute_trend_pct(values_by_month: pd.DataFrame, window: AnnualisationWindow) -> pd.Series:
    """% change: last-6-months total vs prior-6-months total of the window, per entity.

    ``values_by_month`` must have columns entity_id, date, amount (already filtered
    to the relevant pillar's included leg types / corridors). Returned as a fraction
    (0.12 = +12%). NaN where the prior-6-month base is zero or missing (avoid
    manufacturing an infinite or undefined trend).
    """
    midpoint = window.start + (window.end - window.start) / 2
    recent = values_by_month[values_by_month["date"] > midpoint].groupby("entity_id")["amount"].sum()
    prior = values_by_month[values_by_month["date"] <= midpoint].groupby("entity_id")["amount"].sum()
    trend = (recent - prior) / prior.replace(0, np.nan)
    return trend.rename("trend_pct")


# --------------------------------------------------------------------------- #
# Top-level assembly
# --------------------------------------------------------------------------- #

def build_internal_features(
    transactional: pd.DataFrame,
    cross_border: pd.DataFrame,
    trade_finance: pd.DataFrame,
    entities: pd.DataFrame,
    as_of: pd.Timestamp | str = "2026-06-30",
) -> pd.DataFrame:
    """Assemble the full internal_features.csv per the agreed A/C schema.

    Parameters
    ----------
    transactional, cross_border, trade_finance
        Cleaned (Layer 0) DataFrames matching the column names in ``clean_data.py``.
    entities
        DataFrame with entity_id, entity_name, sector (source of truth on real data).
    as_of
        End of the annualisation window. Defaults to the end of the supplied data
        range so this lines up with a single fiscal year of financials, per the brief.
    """
    window = AnnualisationWindow.latest_12_months(pd.Timestamp(as_of))

    cash = build_cash_mgmt(transactional, window)
    trade = build_trade_finance(trade_finance, window)
    fx = build_fx(cross_border, window)
    lending = build_lending(entities["entity_id"])

    stacked = pd.concat([cash, trade, fx, lending], ignore_index=True, sort=False)

    breadth = compute_product_breadth(transactional, cross_border, trade_finance, window)

    # Trend: cash-management trend as the representative relationship-trend signal
    # (matches the pillar with the richest leg-type detail; trade/fx trend can be
    # added the same way once Sync 1 confirms whether C wants it per-pillar).
    cash_window_df = transactional[_in_window(transactional["date"], window)]
    cash_window_df = cash_window_df[cash_window_df["leg_type"].isin(CASH_LEG_TYPES)]
    trend_input = cash_window_df.rename(columns={"amount_zar": "amount"})[["entity_id", "date", "amount"]]
    trend = compute_trend_pct(trend_input, window)

    features = stacked.merge(entities, on="entity_id", how="left")
    features["product_breadth"] = features["entity_id"].map(breadth).fillna(0).astype(int)
    features["trend_pct"] = features["entity_id"].map(trend)
    # trend_pct is only meaningful for observable, flow-based pillars
    features.loc[features["pillar"] == "lending_dcm", "trend_pct"] = np.nan

    ordered = [
        "entity_id", "entity_name", "sector", "pillar", "observed_flow_zar",
        "exposure_days", "product_breadth", "recency_days", "trend_pct",
    ]
    return features[ordered].sort_values(["entity_id", "pillar"]).reset_index(drop=True)
