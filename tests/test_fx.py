"""FX conversion: the rate grid, the flow/stock rule, and the crosschecks."""

from __future__ import annotations

import duckdb
import pytest

from src.syn_wallet import config, external_features, fx
from src.syn_wallet.config import FxBasis

from .conftest import requires_full_data

# ---------------------------------------------------------------------------
# Policy, provable without any data
# ---------------------------------------------------------------------------


def test_every_external_field_has_an_fx_basis() -> None:
    assert set(config.FX_BASIS_BY_FIELD) == set(config.EXTERNAL_FIELDS)
    assert all(isinstance(basis, FxBasis) for basis in config.FX_BASIS_BY_FIELD.values())


def test_flow_fields_use_average_and_stock_fields_use_closing() -> None:
    """The documented rule, asserted field by field so a silent reclassification
    of, say, ``inventory`` to an average rate fails the suite."""
    flows = {"revenue_total", "revenue_south_africa", "revenue_foreign", "cost_of_sales",
             "finance_costs", "capex"}
    stocks = {"inventory", "trade_receivables", "trade_payables", "gross_debt", "debt_current",
              "debt_noncurrent", "cash_and_equivalents", "fx_forward_notional",
              "committed_facilities_total", "undrawn_facilities"}
    for field in flows:
        assert config.FX_BASIS_BY_FIELD[field] is FxBasis.AVERAGE, field
    for field in stocks:
        assert config.FX_BASIS_BY_FIELD[field] is FxBasis.CLOSING, field
    assert config.FX_BASIS_BY_FIELD["employees"] is FxBasis.NONE
    assert set(config.MONETARY_FIELDS) == flows | stocks


# ---------------------------------------------------------------------------
# Conversion arithmetic, on a controlled two-entity fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_conversion() -> duckdb.DuckDBPyConnection:
    """A USD reporter and a ZAR reporter with deliberately different rates.

    An average of 20.0 and a closing of 10.0 are far enough apart that applying
    the wrong one to any field is unmistakable.
    """
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE MACRO safe_div(n, d) AS "
        "CASE WHEN d IS NULL OR d <= 0 OR n IS NULL THEN NULL ELSE CAST(n AS DOUBLE)/CAST(d AS DOUBLE) END"
    )
    connection.execute(
        """
        CREATE TABLE entity_dim AS SELECT * FROM (VALUES
            ('E01', 'Foreign Co', 'mining', 'FY2025', 'USD', DATE '2025-12-31', DATE '2025-01-01'),
            ('E02', 'Local Co',   'consumer', 'FY2025', 'ZAR', DATE '2025-12-31', DATE '2025-01-01')
        ) AS t(entity_id, entity_name, sector, fy_label, reporting_currency, fiscal_year_end, fy_start)
        """
    )
    connection.execute(
        """
        CREATE TABLE entity_fx AS SELECT * FROM (VALUES
            ('E01', 20.0, 10.0, 'sarb_daily_fy_window'),
            ('E02', 1.0, 1.0, 'no_conversion')
        ) AS t(entity_id, fx_avg_rate_zar_per_unit, fx_closing_rate_zar_per_unit, fx_conversion_basis)
        """
    )
    connection.execute(
        """
        CREATE TABLE ext_norm AS SELECT * FROM (VALUES
            ('E01', 'revenue_total', 'currency', 100.0, NULL, 'OK', 'as_reported', NULL),
            ('E01', 'inventory',     'currency',  50.0, NULL, 'OK', 'as_reported', NULL),
            ('E01', 'gross_debt',    'currency', NULL, 'Not disclosed', 'NOT_DISCLOSED',
             'as_reported', 'no borrowings note located'),
            ('E01', 'employees',     'count',   1000.0, NULL, 'OK', 'as_reported', NULL),
            ('E02', 'revenue_total', 'currency', 100.0, NULL, 'OK', 'pro_forma', NULL),
            ('E02', 'inventory',     'currency',  50.0, NULL, 'OK', 'as_reported', NULL)
        ) AS t(entity_id, field, unit_type, value_numeric, value_text, status, basis, gap_reason)
        """
    )
    external_features.build_external_financials_zar(connection)
    yield connection
    connection.close()


def _value(connection: duckdb.DuckDBPyConnection, entity: str, field: str, column: str):
    return connection.execute(
        f"SELECT {column} FROM external_financials_zar WHERE entity_id = ? AND field = ?",
        [entity, field],
    ).fetchone()[0]


def test_flow_field_converts_at_the_average_rate(synthetic_conversion) -> None:
    assert _value(synthetic_conversion, "E01", "revenue_total", "value_zar") == 100.0 * 20.0
    assert _value(synthetic_conversion, "E01", "revenue_total", "fx_rate_used") == 20.0
    assert _value(synthetic_conversion, "E01", "revenue_total", "fx_rate_type") == FxBasis.AVERAGE


def test_stock_field_converts_at_the_closing_rate(synthetic_conversion) -> None:
    assert _value(synthetic_conversion, "E01", "inventory", "value_zar") == 50.0 * 10.0
    assert _value(synthetic_conversion, "E01", "inventory", "fx_rate_used") == 10.0
    assert _value(synthetic_conversion, "E01", "inventory", "fx_rate_type") == FxBasis.CLOSING


def test_zar_reporter_passes_through_unchanged(synthetic_conversion) -> None:
    for field in ("revenue_total", "inventory"):
        assert _value(synthetic_conversion, "E02", field, "value_zar") == _value(
            synthetic_conversion, "E02", field, "value_native"
        )
        assert _value(synthetic_conversion, "E02", field, "fx_conversion_basis") == "no_conversion"


def test_explained_absence_never_becomes_a_number(synthetic_conversion) -> None:
    """A NOT_DISCLOSED gross debt must stay NULL. Zeroing it would erase a
    lending opportunity signal."""
    assert _value(synthetic_conversion, "E01", "gross_debt", "value_zar") is None
    assert _value(synthetic_conversion, "E01", "gross_debt", "is_usable") is False
    assert _value(synthetic_conversion, "E01", "gross_debt", "gap_reason") is not None


def test_non_monetary_field_is_not_converted(synthetic_conversion) -> None:
    assert _value(synthetic_conversion, "E01", "employees", "value_zar") is None
    assert _value(synthetic_conversion, "E01", "employees", "fx_rate_used") is None
    assert _value(synthetic_conversion, "E01", "employees", "value_native") == 1000.0


def test_soft_basis_is_flagged_not_dropped(synthetic_conversion) -> None:
    assert _value(synthetic_conversion, "E02", "revenue_total", "is_soft_basis") is True
    assert _value(synthetic_conversion, "E02", "revenue_total", "is_usable") is True
    assert _value(synthetic_conversion, "E01", "revenue_total", "is_soft_basis") is False


# ---------------------------------------------------------------------------
# The real rate grid
# ---------------------------------------------------------------------------


@requires_full_data
def test_rate_grid_covers_every_entity_and_currency(built) -> None:
    rows = built.execute("SELECT COUNT(*), COUNT(DISTINCT entity_id) FROM fy_fx_rates").fetchone()
    assert rows == (config.EXPECTED_ENTITY_COUNT * len(config.FOREIGN_CURRENCIES),
                    config.EXPECTED_ENTITY_COUNT)
    assert built.execute(
        "SELECT COUNT(*) FROM fy_fx_rates WHERE avg_rate IS NULL OR closing_rate IS NULL "
        "OR avg_rate <= 0 OR closing_rate <= 0"
    ).fetchone()[0] == 0


@requires_full_data
def test_derived_rates_reproduce_the_prepared_fy_window_file(built) -> None:
    """Same method, so every OK row must reproduce, including observation counts."""
    compared, worst = built.execute(
        """
        SELECT COUNT(*), MAX(GREATEST(avg_abs_diff, closing_abs_diff))
        FROM fx_rate_reconciliation WHERE prepared_status = 'OK'
        """
    ).fetchone()
    assert compared == 51
    assert worst <= fx.FY_WINDOW_TOLERANCE

    obs_mismatch = built.execute(
        """
        SELECT COUNT(*) FROM fy_fx_rates d
        JOIN fx_fy_window w USING (entity_id, foreign_currency)
        WHERE w.status = 'OK' AND d.n_obs <> w.n_obs
        """
    ).fetchone()[0]
    assert obs_mismatch == 0


@requires_full_data
def test_the_nine_blocked_rows_are_filled(built) -> None:
    """E11, E12 and E13 are stale in the prepared file, not unresolvable."""
    filled = built.execute(
        """
        SELECT COUNT(*) FROM fx_rate_reconciliation
        WHERE prepared_status = 'BLOCKED_NO_FYE'
          AND derived_avg_rate IS NOT NULL AND derived_closing_rate IS NOT NULL
        """
    ).fetchone()[0]
    assert filled == 9

    # NEPI Rockcastle is the one of the three that actually needs conversion.
    avg_rate, closing_rate = built.execute(
        "SELECT ROUND(avg_rate, 4), ROUND(closing_rate, 4) FROM fy_fx_rates "
        "WHERE entity_id = 'E13' AND foreign_currency = 'EUR'"
    ).fetchone()
    assert (float(avg_rate), float(closing_rate)) == (20.1810, 19.4686)


@requires_full_data
def test_derived_rates_agree_with_entity_published_rates(built) -> None:
    compared, worst = built.execute(
        """
        SELECT COUNT(*), MAX(ABS(pct_diff)) FROM (
            SELECT 100.0 * (CASE WHEN s.rate_type = 'average' THEN d.avg_rate ELSE d.closing_rate END
                            - s.zar_per_unit) / s.zar_per_unit AS pct_diff
            FROM fx_self_reported s
            JOIN fy_fx_rates d USING (entity_id, foreign_currency)
            WHERE s.status = 'OK' AND s.zar_per_unit > 0
        )
        """
    ).fetchone()
    assert compared > 0
    assert worst <= fx.SELF_REPORTED_TOLERANCE_PCT


@requires_full_data
def test_nine_foreign_reporters_are_converted_and_eleven_are_not(features) -> None:
    foreign, local = features.execute(
        """
        SELECT COUNT(*) FILTER (WHERE fx_conversion_basis = 'sarb_daily_fy_window'),
               COUNT(*) FILTER (WHERE fx_conversion_basis = 'no_conversion')
        FROM client_master
        """
    ).fetchone()
    assert (foreign, local) == (9, 11)

    scaled = features.execute(
        """
        SELECT COUNT(*) FROM client_master
        WHERE reporting_currency <> 'ZAR'
          AND ABS(revenue_total_zar / revenue_total_native - fx_avg_rate_zar_per_unit) > 1e-9
        """
    ).fetchone()[0]
    assert scaled == 0
