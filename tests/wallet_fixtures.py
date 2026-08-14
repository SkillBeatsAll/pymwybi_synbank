"""Synthetic feature frames for testing the wallet engine's failure modes.

The real portfolio is well behaved: twenty large listed companies with mostly
complete disclosure. It cannot exercise a zero denominator, a negative working
capital cycle, or a client whose observed activity dwarfs any wallet the model
can build. These fixtures do, deliberately.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

#: One well-behaved consumer client. Every field the five pillars read is
#: present, so a test can knock out exactly one thing and attribute the result.
BASE_CLIENT: dict[str, Any] = {
    "entity_id": "T01",
    "entity_name": "Test Retail",
    "sector": "consumer",
    "fy_label": "FY2025",
    "fiscal_year_end": pd.Timestamp("2025-06-30"),
    # External financials, ZAR.
    "revenue_total_zar": 100e9,
    "revenue_foreign_zar": 20e9,
    "cost_of_sales_zar": 70e9,
    "inventory_zar": 12e9,
    "trade_receivables_zar": 8e9,
    "trade_payables_zar": 10e9,
    "working_capital_zar": 10e9,
    "gross_debt_zar": 30e9,
    "debt_current_zar": 9e9,
    "debt_noncurrent_zar": 21e9,
    "cash_and_equivalents_zar": 5e9,
    "finance_costs_zar": 2.4e9,
    "capex_zar": 6e9,
    "fx_forward_notional_zar": 3e9,
    "undrawn_facilities_zar": 4e9,
    "committed_facilities_total_zar": 12e9,
    "employees": 20_000.0,
    "named_lender_count": 3.0,
    "has_debt_maturity_disclosure": True,
    "revenue_total_is_soft_basis": False,
    "revenue_split_identity_ok": True,
    "gross_debt_identity_ok": True,
    "net_debt_to_revenue": 0.25,
    "finance_costs_to_debt": 0.08,
    "capex_to_revenue": 0.06,
    # Internal flows, fiscal year, ZAR.
    "txn_collections_volume_zar_fy": 2.2e9,
    "txn_collections_domestic_volume_zar_fy": 2.0e9,
    "txn_collections_count_fy": 40_000,
    "txn_supplier_payments_volume_zar_fy": 1.1e9,
    "txn_supplier_payments_domestic_volume_zar_fy": 1.0e9,
    "txn_supplier_payments_count_fy": 20_000,
    "txn_intercompany_sweeps_volume_zar_fy": 3.0e9,
    "txn_payroll_volume_zar_fy": 5e6,
    "txn_payroll_count_fy": 400,
    "txn_tax_volume_zar_fy": 10e6,
    "txn_swift_channel_volume_zar_fy": 0.3e9,
    "txn_memo_count_fy": 50,
    "xb_total_volume_zar_fy": 1.5e9,
    "xb_inbound_volume_zar_fy": 0.9e9,
    "xb_outbound_volume_zar_fy": 0.6e9,
    "xb_transaction_count_fy": 5_000,
    "xb_active_countries_fy": 12,
    "xb_active_currency_pairs_fy": 5,
    "tf_total_value_zar_fy": 0.4e9,
    "tf_import_value_zar_fy": 0.25e9,
    "tf_export_value_zar_fy": 0.15e9,
    "tf_guarantees_value_zar_fy": 0.1e9,
    "tf_live_value_zar_fy": 0.2e9,
    "tf_instrument_count_fy": 300,
    "tf_weighted_avg_tenor_days_fy": 110.0,
}


def synthetic_features(*overrides: dict[str, Any], count: int = 6) -> pd.DataFrame:
    """Build a synthetic feature frame.

    ``count`` clients are cloned from :data:`BASE_CLIENT` with distinct ids, then
    the first ``len(overrides)`` rows have their overrides applied. Six clients
    by default so the portfolio benchmarks -- which need at least four
    contributors -- can actually be measured, and a test that breaks one client
    is not silently testing the "benchmark unavailable" path instead.
    """
    rows = []
    for position in range(count):
        row = dict(BASE_CLIENT)
        row["entity_id"] = f"T{position + 1:02d}"
        row["entity_name"] = f"Test Client {position + 1}"
        # Vary scale so percentile ranks and benchmarks are not degenerate.
        scale = 1.0 + position * 0.35
        for key, value in row.items():
            if isinstance(value, float) and key.endswith(("_zar", "_zar_fy")):
                row[key] = value * scale
        rows.append(row)
    for position, override in enumerate(overrides):
        rows[position].update(override)
    return pd.DataFrame(rows)


#: Scenarios designed to break the engine, each with the behaviour it must show.
BREAKING_SCENARIOS: dict[str, dict[str, Any]] = {
    "zero_revenue": {"revenue_total_zar": 0.0, "cost_of_sales_zar": 0.0},
    "null_revenue": {"revenue_total_zar": np.nan, "cost_of_sales_zar": np.nan},
    "negative_revenue": {"revenue_total_zar": -50e9, "cost_of_sales_zar": -30e9},
    "no_external_financials_at_all": {
        column: np.nan
        for column in BASE_CLIENT
        if column.endswith("_zar") or column in {"employees", "named_lender_count"}
    },
    "observed_dwarfs_every_driver": {
        "revenue_total_zar": 1e9,
        "cost_of_sales_zar": 0.5e9,
        "revenue_foreign_zar": 0.1e9,
        "xb_total_volume_zar_fy": 500e9,
        "tf_total_value_zar_fy": 500e9,
        "txn_collections_domestic_volume_zar_fy": 500e9,
        "txn_supplier_payments_domestic_volume_zar_fy": 500e9,
    },
    "negative_working_capital": {"working_capital_zar": -15e9},
    "insurer_with_manufacturing_cost_base": {
        "sector": "insurance",
        "cost_of_sales_zar": 90e9,
        "inventory_zar": 40e9,
    },
    "no_observed_activity": {
        "txn_collections_volume_zar_fy": 0.0,
        "txn_collections_domestic_volume_zar_fy": 0.0,
        "txn_supplier_payments_volume_zar_fy": 0.0,
        "txn_supplier_payments_domestic_volume_zar_fy": 0.0,
        "txn_collections_count_fy": 0,
        "txn_supplier_payments_count_fy": 0,
        "xb_total_volume_zar_fy": 0.0,
        "xb_inbound_volume_zar_fy": 0.0,
        "xb_outbound_volume_zar_fy": 0.0,
        "xb_transaction_count_fy": 0,
        "tf_total_value_zar_fy": 0.0,
        "tf_instrument_count_fy": 0,
        "tf_import_value_zar_fy": 0.0,
        "tf_export_value_zar_fy": 0.0,
        "tf_guarantees_value_zar_fy": 0.0,
    },
    "zero_debt_disclosed": {
        "gross_debt_zar": 0.0,
        "debt_current_zar": 0.0,
        "debt_noncurrent_zar": 0.0,
        "undrawn_facilities_zar": np.nan,
    },
}
