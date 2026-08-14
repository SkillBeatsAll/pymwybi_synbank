"""Role C — Layer 1 input: build the ZAR-wide external financials table from the
upgraded ``finances/`` data package (replaces the earlier ``build_external_features.py``,
which parsed a single messy long-format CSV by hand).

This version is built directly on top of pre-cleaned, already-validated inputs instead
of re-deriving them:

* ``external_financials_wide.csv`` — one row per entity, already pivoted, already
  numeric, already in the entity's *native reporting currency* (no comma-decimal or
  scientific-notation parsing needed — that cleanup has already been done upstream).
* ``entities.csv`` — authoritative ``reporting_currency`` and fiscal-year-end per
  entity. This is what tells us whether a ZAR conversion is even needed, rather than
  inferring it from a `unit` column on individual field rows.
* ``fx_rates_normalized.csv`` — AFS-disclosed average/closing ZAR rates, split cleanly
  by currency pair (fixes the old multi-currency free-text problem for Valterra,
  OUTsurance, Shoprite) and explicitly statused (OK / NOT_DISCLOSED / NOT_APPLICABLE).
* ``fx_rates_fy_window.csv`` — SARB daily-rate-derived average/closing rates for each
  entity's *actual disclosed fiscal year window*, for USD/GBP/EUR. Used as the fallback
  when the AFS itself didn't disclose a usable rate.
* ``fx_rate_crosscheck.csv`` — independent validation: every AFS-disclosed rate that
  could be checked matches the SARB rate within 0.5%. This is why we prefer the
  AFS-disclosed rate when available (it's not just plausible, it's confirmed) and treat
  the SARB fallback as equally trustworthy for entities where the AFS rate is missing.

Conversion logic, in priority order, per entity:
  1. If ``reporting_currency == "ZAR"``: no conversion needed, use the wide values as-is.
  2. Else, look up that currency's AFS-disclosed average rate in ``fx_rates_normalized``
     (status == "OK"). If found, convert with it.
  3. Else, look up the SARB FY-window average rate in ``fx_rates_fy_window`` (status ==
     "OK"). If found, convert with it — this is what recovers BHP, Naspers, MTN,
     Vodacom, Bidvest, and Shaftesbury, none of which disclosed a usable rate themselves.
  4. Else (NEPI Rockcastle only, currently): leave NaN and flag — fiscal year end is
     unverified, so even the SARB fallback can't be computed. A real gap, not a bug.

No value is ever silently inverted, guessed, or defaulted to 1.0. Debt-maturity-note
references and lender names pass through unconverted (they're text, not currency).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MONETARY_FIELDS = [
    "revenue_total", "revenue_south_africa", "revenue_foreign", "cost_of_sales",
    "capex", "finance_costs", "inventory", "trade_receivables", "trade_payables",
    "gross_debt", "debt_current", "debt_noncurrent", "undrawn_facilities",
    "committed_facilities_total", "fx_forward_notional", "cash_and_equivalents",
]


def _best_rate_lookup(entities: pd.DataFrame, fx_normalized: pd.DataFrame,
                       fx_fy_window: pd.DataFrame) -> pd.DataFrame:
    """One row per entity: the average ZAR rate to use, and where it came from.

    Returns columns: entity_id, avg_zar_rate, rate_source
    (rate_source in {"native_zar", "afs_disclosed", "sarb_fy_window", "unavailable"}).
    """
    out_rows = []
    afs_ok = fx_normalized[(fx_normalized["rate_type"] == "average")
                            & (fx_normalized["status"] == "OK")]
    sarb_ok = fx_fy_window[fx_fy_window["status"] == "OK"]

    for _, e in entities.iterrows():
        entity_id, currency = e["entity_id"], e["reporting_currency"]
        if currency == "ZAR":
            out_rows.append({"entity_id": entity_id, "avg_zar_rate": 1.0, "rate_source": "native_zar"})
            continue

        afs_match = afs_ok[(afs_ok["entity_id"] == entity_id) & (afs_ok["foreign_currency"] == currency)]
        if len(afs_match):
            out_rows.append({"entity_id": entity_id, "avg_zar_rate": afs_match.iloc[0]["zar_per_unit"],
                              "rate_source": "afs_disclosed"})
            continue

        sarb_match = sarb_ok[(sarb_ok["entity_id"] == entity_id) & (sarb_ok["foreign_currency"] == currency)]
        if len(sarb_match):
            out_rows.append({"entity_id": entity_id, "avg_zar_rate": sarb_match.iloc[0]["avg_rate"],
                              "rate_source": "sarb_fy_window"})
            continue

        out_rows.append({"entity_id": entity_id, "avg_zar_rate": np.nan, "rate_source": "unavailable"})

    return pd.DataFrame(out_rows)


def build_external_wide(
    entities: pd.DataFrame,
    financials_wide: pd.DataFrame,
    fx_normalized: pd.DataFrame,
    fx_fy_window: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (ZAR-converted wide table for Layer 1, coverage/rate-source report).

    Parameters mirror the four files loaded from ``finances/``:
      entities          <- entities.csv
      financials_wide   <- external_financials_wide.csv
      fx_normalized     <- fx_rates_normalized.csv
      fx_fy_window       <- fx_rates_fy_window.csv
    """
    rates = _best_rate_lookup(entities, fx_normalized, fx_fy_window)

    wide = financials_wide.merge(entities[["entity_id", "reporting_currency"]],
                                  on="entity_id", suffixes=("", "_entities"), how="left")
    wide = wide.merge(rates, on="entity_id", how="left")

    for field in MONETARY_FIELDS:
        if field not in wide.columns:
            wide[field] = np.nan
        wide[field] = wide[field] * wide["avg_zar_rate"]

    coverage = wide[["entity_id", "entity_name", "reporting_currency", "avg_zar_rate", "rate_source"]].copy()
    coverage["fields_populated"] = wide[MONETARY_FIELDS].notna().sum(axis=1)
    coverage["coverage_pct"] = coverage["fields_populated"] / len(MONETARY_FIELDS)

    keep_cols = ["entity_id", "entity_name"] + MONETARY_FIELDS
    wide_out = wide[keep_cols].copy()

    return wide_out, coverage
