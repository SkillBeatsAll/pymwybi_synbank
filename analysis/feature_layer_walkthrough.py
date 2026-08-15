"""Walkthrough of the client feature layer, for a reader who has not seen it.

Runs as a plain script or, because it is split into ``# %%`` cells, as an
interactive notebook in VS Code or Jupyter (``jupytext --to notebook`` if you
want a real ``.ipynb``)::

    .venv/bin/python -m analysis.feature_layer_walkthrough

It reads ``data/processed/*.parquet`` if the feature layer has already been
built, and builds it into a temporary directory otherwise, so it never depends
on state a fresh clone would not have.

Everything here is descriptive. No share of wallet is estimated, no competitor
wallet is invented, and no fee assumption is applied -- those belong to the next
stage, which consumes this table.
"""

# %%
from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb
import pandas as pd

from src.syn_wallet import build_features, config

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)
pd.set_option("display.float_format", lambda value: f"{value:,.4f}")


def load() -> duckdb.DuckDBPyConnection:
    """Return a connection with the feature outputs registered as views."""
    paths = {name: config.PROCESSED_DIR / f"{name}.parquet" for name in config.OUTPUT_PARQUET}
    if not all(path.is_file() for path in paths.values()):
        print("Feature outputs not found; building into a temporary directory...")
        output_dir = Path(tempfile.mkdtemp(prefix="syn_wallet_features_"))
        report = build_features.run(output_dir=output_dir, overwrite=True)
        paths = {name: Path(path) for name, path in report["outputs"].items()}
    connection = duckdb.connect(":memory:")
    for name, path in paths.items():
        connection.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}')")
    return connection


def show(title: str, frame: pd.DataFrame) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    print(frame.to_string(index=False))


con = load()

# %% [markdown]
# ## 1. The portfolio, side by side
#
# The three internal pillars are reported separately and never summed. 279,389
# transactional rows sit on the SWIFT channel and conceptually overlap
# cross-border payments; the overlap cannot be resolved from the supplied
# fields, so a single "total flow" figure would double-count an unknown amount.

# %%
show(
    "Portfolio flow by pillar, full 36-month window (ZAR)",
    con.execute(
        """
        SELECT 'transactional' AS pillar,
               SUM(txn_total_volume_zar_36m)::DOUBLE AS value_zar,
               SUM(txn_transaction_count_36m) AS records
        FROM client_features
        UNION ALL
        SELECT 'cross_border', SUM(xb_total_volume_zar_36m)::DOUBLE, SUM(xb_transaction_count_36m)
        FROM client_features
        UNION ALL
        SELECT 'trade_finance (all statuses)', SUM(tf_total_value_zar_36m)::DOUBLE,
               SUM(tf_instrument_count_36m)
        FROM client_features
        UNION ALL
        SELECT 'trade_finance (active + issued)', SUM(tf_live_value_zar_36m)::DOUBLE,
               SUM(tf_live_count_36m)
        FROM client_features
        """
    ).df(),
)

# %% [markdown]
# ## 2. Fiscal-period and currency alignment
#
# Nine of twenty clients report in USD, EUR or GBP, across five distinct fiscal
# year ends spanning nine months. Flow measures convert at the fiscal-year
# average SARB rate; balance-sheet measures at the year-end closing rate.

# %%
show(
    "Reporting basis per client",
    con.execute(
        """
        SELECT entity_id, entity_name, sector, reporting_currency AS ccy, fy_label,
               fiscal_year_end, fy_start,
               ROUND(fx_avg_rate_zar_per_unit, 4) AS avg_rate,
               ROUND(fx_closing_rate_zar_per_unit, 4) AS closing_rate,
               fx_conversion_basis,
               ROUND(revenue_total_zar / 1e9, 2) AS revenue_zar_bn,
               revenue_total_basis
        FROM client_master ORDER BY entity_id
        """
    ).df(),
)

# %% [markdown]
# ## 3. Internal engagement features
#
# Product breadth is uniform -- all 20 clients use all five leg types, all five
# channels, and appear in all three pillars. The variation is in *depth*, which
# is what these features measure.

# %%
show(
    "Transactional depth, full window",
    con.execute(
        """
        SELECT entity_id, entity_name,
               ROUND(txn_total_volume_zar_36m / 1e9, 3) AS txn_zar_bn,
               txn_transaction_count_36m AS txn_rows,
               txn_active_days_36m AS active_days,
               txn_payroll_count_36m AS payroll_rows,
               ROUND(txn_payroll_volume_zar_36m / 1e6, 2) AS payroll_zar_m,
               txn_memo_count_36m AS competitor_lending_memos,
               ROUND(txn_inbound_outbound_ratio_36m, 3) AS in_out_ratio,
               ROUND(txn_volume_growth_yoy, 3) AS yoy_growth
        FROM client_features ORDER BY payroll_rows
        """
    ).df(),
)

# %% [markdown]
# The sector-peer contrast is the sharpest fact in the data: MTN runs 2,032
# payroll rows against Vodacom's 11, while Vodacom's cross-border flow is 46.6%
# of MTN's. The gap is specific to domestic transactional banking, not to size.

# %%
show(
    "MTN vs Vodacom -- same sector, opposite engagement",
    con.execute(
        """
        SELECT entity_id, entity_name,
               txn_payroll_count_36m AS payroll_rows,
               ROUND(txn_domestic_volume_zar_36m / 1e9, 3) AS domestic_zar_bn,
               ROUND(xb_total_volume_zar_36m / 1e9, 3) AS cross_border_zar_bn,
               ROUND(txn_volume_to_revenue, 4) AS txn_per_rand_of_revenue,
               ROUND(cross_border_volume_to_revenue, 4) AS xb_per_rand_of_revenue
        FROM client_features WHERE entity_id IN ('E16', 'E17')
        """
    ).df(),
)

# %%
show(
    "Cross-border and trade-finance shape, full window",
    con.execute(
        """
        SELECT entity_id, entity_name,
               ROUND(xb_total_volume_zar_36m / 1e9, 3) AS xb_zar_bn,
               xb_active_countries_36m AS countries,
               ROUND(xb_country_hhi_36m, 3) AS country_hhi,
               xb_top_country_36m AS top_country,
               ROUND(tf_live_value_zar_36m / 1e9, 3) AS tf_live_zar_bn,
               tf_live_count_36m AS live_instruments,
               ROUND(tf_weighted_avg_tenor_days_36m, 1) AS wtd_tenor_days,
               ROUND(tf_import_export_ratio_36m, 2) AS import_export
        FROM client_features ORDER BY xb_zar_bn DESC
        """
    ).df(),
)

# %% [markdown]
# ## 4. Intensity ratios
#
# Fiscal-year internal flow over same-fiscal-year, ZAR-converted external
# financials. These size how much of a client's disclosed economic activity is
# visible in Syn Bank's ledger. They are **not** wallet estimates.

# %%
show(
    "Flow-to-financials intensity, ranked by transactional penetration",
    con.execute(
        """
        SELECT entity_id, entity_name, sector,
               ROUND(txn_volume_to_revenue, 4) AS txn_rev,
               ROUND(collections_to_revenue, 4) AS coll_rev,
               ROUND(supplier_payments_to_cogs, 4) AS sup_cogs,
               ROUND(cross_border_volume_to_revenue, 4) AS xb_rev,
               ROUND(trade_finance_to_cogs, 5) AS tf_cogs,
               ROUND(trade_finance_to_inventory, 5) AS tf_inv
        FROM client_features ORDER BY txn_rev
        """
    ).df(),
)

# %%
show(
    "External structure -- what the client needs, independent of what we see",
    con.execute(
        """
        SELECT entity_id, entity_name,
               ROUND(foreign_revenue_share, 3) AS foreign_rev_share,
               ROUND(inventory_to_cogs, 3) AS inv_cogs,
               ROUND(receivables_to_revenue, 3) AS recv_rev,
               ROUND(debt_to_revenue, 3) AS debt_rev,
               ROUND(finance_costs_to_debt, 4) AS cost_of_debt,
               ROUND(capex_to_revenue, 3) AS capex_rev,
               ROUND(undrawn_to_gross_debt, 3) AS undrawn_debt,
               named_lender_count AS named_lenders,
               has_debt_maturity_disclosure AS debt_maturity_note
        FROM client_features ORDER BY entity_id
        """
    ).df(),
)

# %% [markdown]
# ## 5. Where a wallet model would start
#
# Low transactional penetration paired with substantial disclosed activity is
# the shape a wallet model has to explain. Naming these clients is *not* a
# wallet estimate -- it is a ranked list of the questions the model must answer.

# %%
show(
    "Lowest transactional penetration, with the external scale behind it",
    con.execute(
        """
        SELECT entity_id, entity_name, sector,
               ROUND(revenue_total_zar / 1e9, 1) AS revenue_zar_bn,
               ROUND(txn_volume_to_revenue, 4) AS txn_rev,
               ROUND(cross_border_volume_to_revenue, 4) AS xb_rev,
               txn_payroll_count_fy AS payroll_rows_fy,
               txn_memo_count_36m AS competitor_memos,
               revenue_total_basis AS denominator_basis
        FROM client_features
        ORDER BY txn_volume_to_revenue
        LIMIT 8
        """
    ).df(),
)

# %% [markdown]
# ## 6. Known gaps a wallet model must handle
#
# Every one of these is a disclosed absence carried through as NULL, never
# imputed to zero.

# %%
show(
    "Coverage of each external field across the 20 clients",
    con.execute(
        """
        -- `is_usable` means "a usable *number* is present", so the two text
        -- fields score zero there and are counted under usable_text instead.
        SELECT field, ANY_VALUE(unit_type) AS unit,
               COUNT(*) FILTER (WHERE is_usable) AS usable_numeric,
               COUNT(*) FILTER (WHERE status = 'OK' AND value_text IS NOT NULL) AS usable_text,
               COUNT(*) FILTER (WHERE value_zar IS NOT NULL) AS converted_to_zar,
               COUNT(*) FILTER (WHERE is_soft_basis AND is_usable) AS soft_basis,
               COUNT(*) FILTER (WHERE is_usable AND value_native = 0) AS genuine_zeros,
               STRING_AGG(DISTINCT status, ', ') AS statuses
        FROM external_financials_zar
        GROUP BY field ORDER BY usable_numeric + usable_text, field
        """
    ).df(),
)

# %%
show(
    "Internal identity failures -- geographic splits built on these will not reconcile",
    con.execute(
        """
        SELECT entity_id, entity_name, revenue_total_basis,
               ROUND(revenue_split_residual_zar / 1e6, 1) AS split_residual_zar_m,
               gross_debt_identity_ok, revenue_split_identity_ok
        FROM client_master
        WHERE revenue_split_identity_ok = FALSE OR gross_debt_identity_ok = FALSE
        ORDER BY entity_id
        """
    ).df(),
)

# %%
print(
    "\nFeature table shape:",
    con.execute("SELECT COUNT(*) FROM client_features").fetchone()[0],
    "clients x",
    len(con.execute("DESCRIBE client_features").fetchall()),
    "columns",
)
con.close()
