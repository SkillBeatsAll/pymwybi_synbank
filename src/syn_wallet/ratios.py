"""Analytical intensity ratios linking internal flow to external financials.

These are **signals, not wallet estimates**. A ratio here says how much of a
client's disclosed economic activity is visible in Syn Bank's ledger; it does
not assert what the client's total banking wallet is, what a competitor holds,
or what any of it is worth in fees. No pricing, fee schedule or basis-point
assumption exists anywhere in this repository, and none is introduced here.

Two rules make every ratio comparable across the portfolio:

**Period alignment.** Numerators are the ``fiscal_year`` scope of the internal
features -- the client's own fiscal year -- never the 36-month window. A
36-month numerator over a 12-month denominator would inflate every ratio by
roughly 3x, unevenly across clients with different year ends.

**Currency alignment.** Denominators are the ZAR-converted external values.
Nine of twenty clients report in USD, EUR or GBP; a ZAR numerator over a native
denominator understates those clients by 17-24x.

Every ratio is NULL rather than zero when its denominator is absent or
non-positive. ``safe_div`` enforces this.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ratio:
    """One analytical ratio and the reason it is commercially interesting."""

    name: str
    numerator: str
    denominator: str
    rationale: str


#: Ratios computed onto ``client_features``. Numerator columns carry the ``_fy``
#: suffix (client's own fiscal year); denominator columns carry ``_zar``.
RATIOS: tuple[Ratio, ...] = (
    Ratio(
        "txn_volume_to_revenue",
        "txn_total_volume_zar_fy",
        "revenue_total_zar",
        "Headline transactional penetration: turnover visible in the ledger per rand of revenue.",
    ),
    Ratio(
        "txn_domestic_volume_to_revenue",
        "txn_domestic_volume_zar_fy",
        "revenue_total_zar",
        "As above but excluding SWIFT-channel rows, which overlap the cross-border pillar.",
    ),
    Ratio(
        "collections_to_revenue",
        "txn_collections_volume_zar_fy",
        "revenue_total_zar",
        "How much of the client's sales cash actually lands in a Syn Bank account.",
    ),
    Ratio(
        "supplier_payments_to_cogs",
        "txn_supplier_payments_volume_zar_fy",
        "cost_of_sales_zar",
        "How much of the client's procurement spend is paid out of Syn Bank.",
    ),
    Ratio(
        "payroll_volume_to_revenue",
        "txn_payroll_volume_zar_fy",
        "revenue_total_zar",
        "Payroll is the stickiest transactional product; a low share implies a competitor primary bank.",
    ),
    Ratio(
        "payroll_volume_per_employee_zar",
        "txn_payroll_volume_zar_fy",
        "employees",
        "Payroll rand per head; an order-of-magnitude shortfall means payroll runs elsewhere.",
    ),
    Ratio(
        "tax_volume_to_revenue",
        "txn_tax_volume_zar_fy",
        "revenue_total_zar",
        "Tax settlement is a mandate-level product and tracks the primary transactional bank.",
    ),
    Ratio(
        "cross_border_volume_to_revenue",
        "xb_total_volume_zar_fy",
        "revenue_total_zar",
        "FX and cross-border penetration against the client's total turnover.",
    ),
    Ratio(
        "cross_border_volume_to_foreign_revenue",
        "xb_total_volume_zar_fy",
        "revenue_foreign_zar",
        "The sharper FX denominator where the client discloses a foreign revenue leg.",
    ),
    Ratio(
        "cross_border_volume_to_fx_notional",
        "xb_total_volume_zar_fy",
        "fx_forward_notional_zar",
        "Flow routed through Syn Bank against the client's disclosed hedging programme.",
    ),
    Ratio(
        "trade_finance_to_cogs",
        "tf_live_value_zar_fy",
        "cost_of_sales_zar",
        "Live trade instruments against procurement cost; the core trade-finance penetration measure.",
    ),
    Ratio(
        "trade_finance_to_inventory",
        "tf_live_value_zar_fy",
        "inventory_zar",
        "Inventory is the classic trade-finance need driver.",
    ),
    Ratio(
        "trade_finance_import_to_cogs",
        "tf_import_value_zar_fy",
        "cost_of_sales_zar",
        "Import instruments specifically, which map to purchases rather than sales.",
    ),
    # Purely external structure ratios -- they size the need, not the capture.
    Ratio(
        "inventory_to_cogs",
        "inventory_zar",
        "cost_of_sales_zar",
        "Inventory days proxy; a high value means a larger latent trade-finance need.",
    ),
    Ratio(
        "receivables_to_revenue",
        "trade_receivables_zar",
        "revenue_total_zar",
        "Receivables intensity; drives receivables finance and collections appetite.",
    ),
    Ratio(
        "payables_to_cogs",
        "trade_payables_zar",
        "cost_of_sales_zar",
        "Payables intensity; drives supply-chain finance appetite.",
    ),
    Ratio(
        "debt_to_revenue",
        "gross_debt_zar",
        "revenue_total_zar",
        "Leverage intensity; sizes the lending and DCM wallet.",
    ),
    Ratio(
        "net_debt_to_revenue",
        "net_debt_zar",
        "revenue_total_zar",
        "Leverage net of cash, which is what a coverage banker actually pitches against.",
    ),
    Ratio(
        "finance_costs_to_debt",
        "finance_costs_zar",
        "gross_debt_zar",
        "Implied blended cost of debt; a refinancing conversation opener.",
    ),
    Ratio(
        "capex_to_revenue",
        "capex_zar",
        "revenue_total_zar",
        "Investment intensity; forward-looking asset and project finance demand.",
    ),
    Ratio(
        "cash_to_revenue",
        "cash_and_equivalents_zar",
        "revenue_total_zar",
        "Liquidity intensity; sizes the deposit and liquidity-management wallet.",
    ),
    Ratio(
        "foreign_revenue_share",
        "revenue_foreign_zar",
        "revenue_total_zar",
        "Structural FX exposure, independent of what flows through Syn Bank.",
    ),
    Ratio(
        "undrawn_to_gross_debt",
        "undrawn_facilities_zar",
        "gross_debt_zar",
        "Unused committed headroom; a direct read on where competitor lenders sit.",
    ),
    Ratio(
        "undrawn_to_committed_facilities",
        "undrawn_facilities_zar",
        "committed_facilities_total_zar",
        "Utilisation of the committed book.",
    ),
)


def ratio_sql() -> str:
    """Return the comma-separated ratio expressions for the assembly SELECT."""
    return ",\n               ".join(
        f"safe_div({ratio.numerator}, {ratio.denominator}) AS {ratio.name}"
        for ratio in RATIOS
    )


def trend_sql() -> str:
    """Return growth expressions comparing the recent and prior trailing years."""
    pillars = {
        "txn": "txn_total_volume_zar",
        "xb": "xb_total_volume_zar",
        "tf": "tf_total_value_zar",
    }
    expressions = []
    for prefix, column in pillars.items():
        expressions.append(
            f"safe_div({column}_r12m, {column}_p12m) - 1 AS {prefix}_volume_growth_yoy"
        )
        expressions.append(
            f"{column}_r12m - {column}_p12m AS {prefix}_volume_change_yoy_zar"
        )
    expressions.append(
        "safe_div(txn_transaction_count_r12m, txn_transaction_count_p12m) - 1 "
        "AS txn_count_growth_yoy"
    )
    return ",\n               ".join(expressions)


def ratio_names() -> tuple[str, ...]:
    return tuple(ratio.name for ratio in RATIOS)
