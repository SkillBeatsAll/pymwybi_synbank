"""Paths, vocabularies and policy constants for the analytical feature layer.

Everything in this module is a *decision*, not a derived value. If a downstream
number looks wrong, the explanation is usually a constant here.

Two policies are load-bearing and are stated in code so they cannot drift out of
the documentation:

``FX_BASIS_BY_FIELD``
    Income-statement and cash-flow (flow) measures convert at the **fiscal-year
    average** ZAR rate. Balance-sheet (stock) measures convert at the
    **fiscal-year-end closing** ZAR rate. Non-monetary fields are never
    converted. See :data:`FxBasis`.

``PILLARS``
    The three internal flow datasets are separate pillars and are never summed
    into one portfolio number. 279,389 transactional rows sit on the ``SWIFT``
    channel and conceptually overlap cross-border payments; the overlap cannot
    be resolved from the supplied fields, so adding the pillars double-counts an
    unknown amount.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = REPOSITORY_ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
FINANCES_DIR = DATA_DIR / "finances"

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

INTERNAL_PARQUET = {
    "transactional_banking": PROCESSED_DIR / "transactional_banking.parquet",
    "cross_border_payments": PROCESSED_DIR / "cross_border_payments.parquet",
    "trade_finance": PROCESSED_DIR / "trade_finance.parquet",
}

FINANCE_CSV = {
    "entities": FINANCES_DIR / "entities.csv",
    "external_financials_normalized": FINANCES_DIR / "external_financials_normalized.csv",
    "external_financials_wide": FINANCES_DIR / "external_financials_wide.csv",
    "fx_rates_normalized": FINANCES_DIR / "fx_rates_normalized.csv",
    "fx_rates_fy_window": FINANCES_DIR / "fx_rates_fy_window.csv",
    "fx_rates_sarb_daily": FINANCES_DIR / "fx_rates_sarb_daily.csv",
    "fx_rate_crosscheck": FINANCES_DIR / "fx_rate_crosscheck.csv",
    "data_dictionary": FINANCES_DIR / "data_dictionary.csv",
    "data_quality_exceptions": FINANCES_DIR / "data_quality_exceptions.csv",
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

OUTPUT_PARQUET = {
    "external_financials_zar": PROCESSED_DIR / "external_financials_zar.parquet",
    "client_master": PROCESSED_DIR / "client_master.parquet",
    "client_features": PROCESSED_DIR / "client_features.parquet",
    "client_corridor_breakdown": PROCESSED_DIR / "client_corridor_breakdown.parquet",
}
FEATURE_REPORT_PATH = PROCESSED_DIR / "feature_report.json"

# ---------------------------------------------------------------------------
# Portfolio shape
# ---------------------------------------------------------------------------

EXPECTED_ENTITY_COUNT = 20

EXPECTED_SECTORS = frozenset(
    {
        "mining",
        "insurance",
        "consumer",
        "real_estate",
        "tech",
        "telecoms",
        "industrials_pharma",
    }
)

#: The internal flow datasets all span this closed date range (1,096 dates).
FLOW_WINDOW_START = "2023-07-01"
FLOW_WINDOW_END = "2026-06-30"

#: Portfolio-common trailing windows, anchored on ``FLOW_WINDOW_END``.
RECENT_12M_START = "2025-07-01"
RECENT_12M_END = "2026-06-30"
PRIOR_12M_START = "2024-07-01"
PRIOR_12M_END = "2025-06-30"

# ---------------------------------------------------------------------------
# Internal vocabularies (verified against the cleaned Parquet)
# ---------------------------------------------------------------------------

LEG_TYPES = ("collections", "supplier_payments", "intercompany_sweeps", "payroll", "tax")
CHANNELS = ("EFT", "RTC", "Internal Transfer", "SWIFT", "Debit Order")
DIRECTIONS = ("inbound", "outbound")
CORRIDOR_TYPES = ("trade", "intercompany", "other")
CURRENCY_PAIRS = ("USD/ZAR", "EUR/ZAR", "GBP/ZAR", "AED/ZAR", "CNY/ZAR")
INSTRUMENT_TYPES = ("letters_of_credit", "guarantees", "export_collections")
TRADE_DIRECTIONS = ("import", "export")
TRADE_STATUSES = ("active", "issued", "settled", "expired")

#: Trade-finance statuses representing an instrument that is still on the book.
#: ``settled`` and ``expired`` are historical and are reported separately.
LIVE_TRADE_STATUSES = ("active", "issued")

#: The three pillars are reported side by side and never summed. See module docstring.
PILLARS = ("transactional", "cross_border", "trade_finance")

# ---------------------------------------------------------------------------
# FX policy
# ---------------------------------------------------------------------------


class FxBasis(StrEnum):
    """Which fiscal-year rate converts a given external financial field."""

    #: Flow measure (income statement / cash flow) -> fiscal-year average rate.
    AVERAGE = "fy_average"
    #: Stock measure (balance sheet, point in time) -> fiscal-year-end closing rate.
    CLOSING = "fy_closing"
    #: Not a monetary amount; never converted.
    NONE = "not_monetary"


#: Currencies with a SARB daily series in ``fx_rates_sarb_daily.csv``.
FOREIGN_CURRENCIES = ("USD", "EUR", "GBP")

#: Reporting currency of the base ledger. Values already in ZAR convert at 1.0.
BASE_CURRENCY = "ZAR"

FX_BASIS_BY_FIELD: dict[str, FxBasis] = {
    # Income statement / cash flow -- period flows.
    "revenue_total": FxBasis.AVERAGE,
    "revenue_south_africa": FxBasis.AVERAGE,
    "revenue_foreign": FxBasis.AVERAGE,
    "cost_of_sales": FxBasis.AVERAGE,
    "finance_costs": FxBasis.AVERAGE,
    "capex": FxBasis.AVERAGE,
    # Balance sheet / facility registers -- point-in-time stocks.
    "inventory": FxBasis.CLOSING,
    "trade_receivables": FxBasis.CLOSING,
    "trade_payables": FxBasis.CLOSING,
    "gross_debt": FxBasis.CLOSING,
    "debt_current": FxBasis.CLOSING,
    "debt_noncurrent": FxBasis.CLOSING,
    "cash_and_equivalents": FxBasis.CLOSING,
    "fx_forward_notional": FxBasis.CLOSING,
    "committed_facilities_total": FxBasis.CLOSING,
    "undrawn_facilities": FxBasis.CLOSING,
    # Non-monetary.
    "employees": FxBasis.NONE,
    "debt_maturity_note_page": FxBasis.NONE,
    "lenders_named": FxBasis.NONE,
}

#: Monetary fields, in the order they appear in the wide ZAR projection.
MONETARY_FIELDS = tuple(
    field for field, basis in FX_BASIS_BY_FIELD.items() if basis is not FxBasis.NONE
)

#: Numeric non-monetary fields carried through unconverted.
NON_MONETARY_NUMERIC_FIELDS = ("employees",)

#: Every field expected in ``external_financials_normalized.csv``.
EXTERNAL_FIELDS = tuple(FX_BASIS_BY_FIELD)

#: ``status`` values that mark a usable extracted number. Everything else is an
#: explained absence and must stay NULL rather than becoming a zero.
USABLE_STATUS = ("OK",)

#: The status vocabulary has drifted beyond the original closed set; validators
#: accept the union rather than failing on a legitimate new reason code.
KNOWN_STATUSES = (
    "OK",
    "NOT_APPLICABLE",
    "NOT_DISCLOSED",
    "NOT_FOUND",
    "NOT_COMPARABLE",
    "AFS_NOT_YET_AUDITED",
    "NOT_EXTRACTED",
    "BLOCKED_NO_FYE",
)

#: ``basis`` values that indicate the number is not a clean as-reported figure.
#: Carried into the output so a wallet model can down-weight them; never used to
#: drop a row.
SOFT_BASIS = ("pro_forma", "constructed", "commentary", "derived", "unknown")

# ---------------------------------------------------------------------------
# Feature-layer scopes
# ---------------------------------------------------------------------------

#: Suffix applied to a metric name for each aggregation window.
SCOPE_SUFFIX = {
    "full_window": "36m",
    "fiscal_year": "fy",
    "recent_12m": "r12m",
    "prior_12m": "p12m",
}

#: Scopes that receive the complete metric set. The trailing 12-month scopes
#: receive headline volume and count only, for trend measurement.
FULL_METRIC_SCOPES = ("full_window", "fiscal_year")
TREND_SCOPES = ("recent_12m", "prior_12m")

#: Columns that must never be NULL in ``client_features``.
REQUIRED_FEATURE_COLUMNS = (
    "entity_id",
    "entity_name",
    "sector",
    "fy_label",
    "fiscal_year_end",
    "fy_start",
    "reporting_currency",
    "fx_avg_rate_zar_per_unit",
    "fx_closing_rate_zar_per_unit",
    "fx_conversion_basis",
    "txn_total_volume_zar_36m",
    "txn_transaction_count_36m",
    "txn_active_days_36m",
    "xb_total_volume_zar_36m",
    "xb_transaction_count_36m",
    "tf_total_value_zar_36m",
    "tf_instrument_count_36m",
    "revenue_total_zar",
)
