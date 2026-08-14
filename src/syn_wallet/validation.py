"""Assertions that make the feature layer safe to build a wallet model on.

Each check answers one question that would otherwise be discovered late, in a
number nobody can reproduce: did a join fan out, did an FX rate silently fail to
apply, did a fiscal-year numerator get paired with a 36-month denominator, did a
required field arrive NULL.

Checks return structured results rather than raising, so a run reports every
failure at once. :func:`assert_all` turns the collection into an exception for
the pipeline entry point and for tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import duckdb

from . import config, fx, ratios


@dataclass(frozen=True)
class Check:
    """One validation outcome."""

    name: str
    passed: bool
    detail: str
    observed: Any = None


@dataclass
class ValidationReport:
    """The outcome of a full validation pass."""

    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.passed]

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks_run": len(self.checks),
            "failures": len(self.failures),
            "results": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "detail": check.detail,
                    "observed": check.observed,
                }
                for check in self.checks
            ],
        }


class ValidationError(AssertionError):
    """Raised when the feature layer fails one or more checks."""


def _scalar(connection: duckdb.DuckDBPyConnection, sql: str) -> Any:
    return connection.execute(sql).fetchone()[0]


def _sql_list(values: tuple[str, ...]) -> str:
    """Render a Python tuple as a SQL ``IN`` list, safe at any length."""
    return "(" + ", ".join("'" + value.replace("'", "''") + "'" for value in values) + ")"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_entity_count(connection: duckdb.DuckDBPyConnection) -> list[Check]:
    """Every output holds exactly the 20 portfolio clients, once each."""
    results = []
    for table in ("client_master", "client_features"):
        rows, distinct = connection.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT entity_id) FROM {table}"
        ).fetchone()
        results.append(
            Check(
                f"entity_count.{table}",
                rows == distinct == config.EXPECTED_ENTITY_COUNT,
                f"{table} must hold {config.EXPECTED_ENTITY_COUNT} unique entities",
                {"rows": rows, "distinct_entity_ids": distinct},
            )
        )
    return results


def check_entity_joins(connection: duckdb.DuckDBPyConnection) -> list[Check]:
    """Entity keys agree across every source; no join drops or invents a client."""
    results = []
    for view, label in (("txn", "transactional"), ("xb", "cross_border"), ("tf", "trade_finance")):
        unmatched = _scalar(
            connection,
            f"""
            SELECT COUNT(*) FROM (
                SELECT DISTINCT entity_id, entity_name FROM {view}
                EXCEPT
                SELECT entity_id, entity_name FROM entity_dim
            )
            """,
        )
        results.append(
            Check(
                f"entity_join.{label}",
                unmatched == 0,
                f"every (entity_id, entity_name) in {label} must exist in the entity dimension",
                {"unmatched_pairs": unmatched},
            )
        )

    missing_from_flows = _scalar(
        connection,
        """
        SELECT COUNT(*) FROM entity_dim e
        WHERE NOT EXISTS (SELECT 1 FROM txn WHERE entity_id = e.entity_id)
           OR NOT EXISTS (SELECT 1 FROM xb  WHERE entity_id = e.entity_id)
           OR NOT EXISTS (SELECT 1 FROM tf  WHERE entity_id = e.entity_id)
        """,
    )
    results.append(
        Check(
            "entity_join.all_entities_in_all_pillars",
            missing_from_flows == 0,
            "all 20 clients appear in all three internal datasets",
            {"entities_missing_a_pillar": missing_from_flows},
        )
    )

    external_orphans = _scalar(
        connection,
        "SELECT COUNT(DISTINCT entity_id) FROM ext_norm "
        "WHERE entity_id NOT IN (SELECT entity_id FROM entity_dim)",
    )
    results.append(
        Check(
            "entity_join.external_financials",
            external_orphans == 0,
            "external financials reference only known entities",
            {"orphan_entity_ids": external_orphans},
        )
    )
    return results


def check_sectors(connection: duckdb.DuckDBPyConnection) -> list[Check]:
    """Sector is populated, unambiguous per entity, and drawn from the known set."""
    ambiguous = _scalar(
        connection,
        "SELECT COUNT(*) FROM entity_dim WHERE distinct_sectors <> 1 OR distinct_names <> 1",
    )
    null_sectors = _scalar(connection, "SELECT COUNT(*) FROM client_features WHERE sector IS NULL")
    observed = {
        row[0] for row in connection.execute("SELECT DISTINCT sector FROM client_features").fetchall()
    }
    return [
        Check(
            "sector.unambiguous_per_entity",
            ambiguous == 0,
            "each entity carries one sector and one name across all three pillars",
            {"ambiguous_entities": ambiguous},
        ),
        Check(
            "sector.populated",
            null_sectors == 0,
            "sector is non-null for every client",
            {"null_sectors": null_sectors},
        ),
        Check(
            "sector.expected_population",
            observed == set(config.EXPECTED_SECTORS),
            "the portfolio spans exactly the expected sectors",
            {"observed": sorted(observed), "expected": sorted(config.EXPECTED_SECTORS)},
        ),
    ]


def check_fx_rates(connection: duckdb.DuckDBPyConnection, crosscheck: fx.FxCrosscheck) -> list[Check]:
    """Rates exist for every client, ZAR reporters are untouched, and the derived
    basis reproduces both prepared rate files."""
    zar_wrong = _scalar(
        connection,
        f"""
        SELECT COUNT(*) FROM entity_fx
        WHERE reporting_currency = '{config.BASE_CURRENCY}'
          AND (fx_avg_rate_zar_per_unit <> 1.0
               OR fx_closing_rate_zar_per_unit <> 1.0
               OR fx_conversion_basis <> 'no_conversion')
        """,
    )
    foreign_missing = _scalar(
        connection,
        f"""
        SELECT COUNT(*) FROM entity_fx
        WHERE reporting_currency <> '{config.BASE_CURRENCY}'
          AND (fx_avg_rate_zar_per_unit IS NULL OR fx_avg_rate_zar_per_unit <= 0
               OR fx_closing_rate_zar_per_unit IS NULL OR fx_closing_rate_zar_per_unit <= 0)
        """,
    )
    rate_rows = _scalar(connection, "SELECT COUNT(*) FROM fy_fx_rates")
    expected_rate_rows = config.EXPECTED_ENTITY_COUNT * len(config.FOREIGN_CURRENCIES)
    return [
        Check(
            "fx.zar_reporters_unconverted",
            zar_wrong == 0,
            "ZAR reporters convert at exactly 1.0 with basis no_conversion",
            {"violations": zar_wrong},
        ),
        Check(
            "fx.foreign_reporters_have_rates",
            foreign_missing == 0,
            "every non-ZAR reporter has a positive average and closing rate",
            {"entities_without_rates": foreign_missing},
        ),
        Check(
            "fx.rate_grid_complete",
            rate_rows == expected_rate_rows,
            f"the FY rate grid holds {expected_rate_rows} rows (20 entities x 3 currencies)",
            {"rows": rate_rows},
        ),
        Check(
            "fx.reproduces_prepared_fy_window",
            crosscheck.fy_window_max_abs_diff <= fx.FY_WINDOW_TOLERANCE,
            "derived rates reproduce every OK row of fx_rates_fy_window.csv",
            {
                "rows_compared": crosscheck.fy_window_rows_compared,
                "max_abs_diff": crosscheck.fy_window_max_abs_diff,
                "tolerance": fx.FY_WINDOW_TOLERANCE,
            },
        ),
        Check(
            "fx.agrees_with_self_reported",
            crosscheck.self_reported_max_abs_pct_diff <= fx.SELF_REPORTED_TOLERANCE_PCT,
            "derived rates agree with entity-published rates",
            {
                "rows_compared": crosscheck.self_reported_rows_compared,
                "max_abs_pct_diff": crosscheck.self_reported_max_abs_pct_diff,
                "tolerance_pct": fx.SELF_REPORTED_TOLERANCE_PCT,
            },
        ),
    ]


def check_fx_conversion(connection: duckdb.DuckDBPyConnection) -> list[Check]:
    """Each converted value equals its native value times the rate its field's
    basis prescribes -- average for flows, closing for stocks."""
    mispriced = _scalar(
        connection,
        f"""
        SELECT COUNT(*)
        FROM external_financials_zar z
        JOIN entity_fx f USING (entity_id)
        WHERE z.value_zar IS NOT NULL
          AND ABS(z.value_zar - z.value_native * CASE z.fx_rate_type
                    WHEN '{config.FxBasis.AVERAGE.value}' THEN f.fx_avg_rate_zar_per_unit
                    WHEN '{config.FxBasis.CLOSING.value}' THEN f.fx_closing_rate_zar_per_unit
                  END) > 1e-6 * GREATEST(ABS(z.value_zar), 1.0)
        """,
    )
    zar_changed = _scalar(
        connection,
        f"""
        SELECT COUNT(*) FROM external_financials_zar
        WHERE reporting_currency = '{config.BASE_CURRENCY}'
          AND value_zar IS NOT NULL
          AND value_zar IS DISTINCT FROM value_native
        """,
    )
    unconverted = _scalar(
        connection,
        f"""
        SELECT COUNT(*) FROM external_financials_zar
        WHERE unit_type = 'currency' AND is_usable
          AND reporting_currency <> '{config.BASE_CURRENCY}'
          AND (value_zar IS NULL OR value_zar = value_native)
          AND value_native <> 0
        """,
    )
    non_monetary_converted = _scalar(
        connection,
        f"""
        SELECT COUNT(*) FROM external_financials_zar
        WHERE fx_rate_type = '{config.FxBasis.NONE.value}'
          AND (value_zar IS NOT NULL OR fx_rate_used IS NOT NULL)
        """,
    )
    unmapped = _scalar(
        connection,
        "SELECT COUNT(*) FROM external_financials_zar WHERE fx_rate_type = 'unmapped'",
    )
    gaps_zeroed = _scalar(
        connection,
        "SELECT COUNT(*) FROM external_financials_zar WHERE NOT is_usable AND value_zar IS NOT NULL",
    )
    return [
        Check(
            "fx_conversion.correct_rate_applied",
            mispriced == 0,
            "value_zar equals value_native times the rate prescribed by the field's FX basis",
            {"mispriced_values": mispriced},
        ),
        Check(
            "fx_conversion.zar_values_unchanged",
            zar_changed == 0,
            "ZAR reporters' values pass through unchanged",
            {"changed_values": zar_changed},
        ),
        Check(
            "fx_conversion.foreign_values_converted",
            unconverted == 0,
            "every usable foreign-currency value has a distinct ZAR value",
            {"unconverted_values": unconverted},
        ),
        Check(
            "fx_conversion.non_monetary_untouched",
            non_monetary_converted == 0,
            "employees and text fields carry no ZAR value and no rate",
            {"violations": non_monetary_converted},
        ),
        Check(
            "fx_conversion.every_field_has_a_basis",
            unmapped == 0,
            "every external field maps to an FX basis in config.FX_BASIS_BY_FIELD",
            {"unmapped_fields": unmapped},
        ),
        Check(
            "fx_conversion.gaps_stay_null",
            gaps_zeroed == 0,
            "an unusable value never becomes a number; explained absences stay NULL",
            {"gaps_with_values": gaps_zeroed},
        ),
    ]


def check_fiscal_alignment(connection: duckdb.DuckDBPyConnection) -> list[Check]:
    """Fiscal windows are well formed, sit inside the flow window, and the
    fiscal-year aggregates are a strict subset of the full-window aggregates."""
    bad_window = _scalar(
        connection,
        """
        SELECT COUNT(*) FROM entity_dim
        WHERE fy_start <> CAST(fiscal_year_end - INTERVAL 1 YEAR + INTERVAL 1 DAY AS DATE)
           OR fy_start < flow_window_start
           OR fiscal_year_end > flow_window_end
        """,
    )
    bad_months = _scalar(
        connection,
        "SELECT COUNT(*) FROM entity_windows WHERE window_months <> 12 AND scope <> 'full_window'",
    )
    full_months = _scalar(
        connection,
        "SELECT COUNT(*) FROM entity_windows WHERE scope = 'full_window' AND window_months <> 36",
    )
    fy_exceeds_full = _scalar(
        connection,
        """
        SELECT COUNT(*) FROM client_features
        WHERE txn_total_volume_zar_fy > txn_total_volume_zar_36m
           OR xb_total_volume_zar_fy  > xb_total_volume_zar_36m
           OR tf_total_value_zar_fy   > tf_total_value_zar_36m
           OR txn_transaction_count_fy > txn_transaction_count_36m
        """,
    )
    fy_empty = _scalar(
        connection,
        "SELECT COUNT(*) FROM client_features "
        "WHERE txn_transaction_count_fy = 0 OR xb_transaction_count_fy = 0",
    )
    scope_rows = _scalar(
        connection,
        f"""
        SELECT COUNT(*) FROM (
            SELECT scope, COUNT(*) AS n FROM entity_windows GROUP BY scope
        ) WHERE n <> {config.EXPECTED_ENTITY_COUNT}
        """,
    )
    return [
        Check(
            "fiscal_alignment.window_definition",
            bad_window == 0,
            "fy_start is the day after the prior year end and the window sits inside the flow window",
            {"violations": bad_window},
        ),
        Check(
            "fiscal_alignment.twelve_month_scopes",
            bad_months == 0 and full_months == 0,
            "fiscal-year and trailing scopes span 12 months; the full window spans 36",
            {"non_12_month_scopes": bad_months, "non_36_month_full_windows": full_months},
        ),
        Check(
            "fiscal_alignment.fy_subset_of_full_window",
            fy_exceeds_full == 0,
            "no fiscal-year aggregate exceeds its full-window counterpart",
            {"violations": fy_exceeds_full},
        ),
        Check(
            "fiscal_alignment.fy_windows_populated",
            fy_empty == 0,
            "every client has transactional and cross-border activity inside its own fiscal year",
            {"empty_fiscal_years": fy_empty},
        ),
        Check(
            "fiscal_alignment.scope_cardinality",
            scope_rows == 0,
            "every scope holds exactly one window per client",
            {"scopes_with_wrong_row_count": scope_rows},
        ),
    ]


def check_aggregates(connection: duckdb.DuckDBPyConnection) -> list[Check]:
    """Full-window aggregates reconcile exactly to the cleaned Parquet totals.

    Compared as text at the source ``DECIMAL`` scale, so a rounding difference
    fails rather than hides.
    """
    cases = (
        ("transactional", "txn", "amount_zar", "txn_total_volume_zar_36m", "txn_transaction_count_36m"),
        ("cross_border", "xb", "value_zar", "xb_total_volume_zar_36m", "xb_transaction_count_36m"),
        ("trade_finance", "tf", "value_zar", "tf_total_value_zar_36m", "tf_instrument_count_36m"),
    )
    results = []
    for label, view, amount, volume_column, count_column in cases:
        source_total, source_rows = connection.execute(
            f"SELECT CAST(SUM({amount}) AS VARCHAR), COUNT(*) FROM {view}"
        ).fetchone()
        feature_total, feature_rows = connection.execute(
            f"SELECT CAST(SUM({volume_column}) AS VARCHAR), SUM({count_column}) FROM client_features"
        ).fetchone()
        results.append(
            Check(
                f"aggregate.{label}_value",
                source_total == feature_total,
                f"{label} full-window volume sums exactly to the cleaned Parquet total",
                {"source": source_total, "features": feature_total},
            )
        )
        results.append(
            Check(
                f"aggregate.{label}_count",
                int(source_rows) == int(feature_rows),
                f"{label} full-window row count sums exactly to the cleaned Parquet count",
                {"source": int(source_rows), "features": int(feature_rows)},
            )
        )

    leg_mismatch = _scalar(
        connection,
        """
        SELECT COUNT(*) FROM (
            SELECT CAST(SUM(txn_collections_volume_zar_36m + txn_supplier_payments_volume_zar_36m
                        + txn_intercompany_sweeps_volume_zar_36m + txn_payroll_volume_zar_36m
                        + txn_tax_volume_zar_36m) AS VARCHAR) AS legs,
                   CAST(SUM(txn_total_volume_zar_36m) AS VARCHAR) AS total
            FROM client_features
        ) WHERE legs <> total
        """,
    )
    direction_mismatch = _scalar(
        connection,
        """
        SELECT COUNT(*) FROM client_features
        WHERE txn_inbound_volume_zar_36m + txn_outbound_volume_zar_36m <> txn_total_volume_zar_36m
           OR xb_inbound_volume_zar_36m + xb_outbound_volume_zar_36m <> xb_total_volume_zar_36m
           OR tf_import_value_zar_36m + tf_export_value_zar_36m <> tf_total_value_zar_36m
        """,
    )
    status_mismatch = _scalar(
        connection,
        """
        SELECT COUNT(*) FROM client_features
        WHERE tf_active_value_zar_36m + tf_issued_value_zar_36m + tf_settled_value_zar_36m
              + tf_expired_value_zar_36m <> tf_total_value_zar_36m
           OR tf_active_value_zar_36m + tf_issued_value_zar_36m <> tf_live_value_zar_36m
        """,
    )
    corridor_shares = _scalar(
        connection,
        """
        SELECT COUNT(*) FROM (
            SELECT entity_id, scope, pillar, dimension, SUM(share_of_pillar) AS total
            FROM client_corridor_breakdown GROUP BY ALL
        ) WHERE ABS(total - 1.0) > 1e-9
        """,
    )
    negatives = _scalar(
        connection,
        """
        SELECT COUNT(*) FROM client_features
        WHERE txn_total_volume_zar_36m < 0 OR xb_total_volume_zar_36m < 0
           OR tf_total_value_zar_36m < 0
        """,
    )
    results += [
        Check(
            "aggregate.leg_decomposition",
            leg_mismatch == 0,
            "the five leg-type volumes sum exactly to total transactional volume",
            {"mismatches": leg_mismatch},
        ),
        Check(
            "aggregate.direction_decomposition",
            direction_mismatch == 0,
            "inbound + outbound (and import + export) equal the pillar total for every client",
            {"mismatches": direction_mismatch},
        ),
        Check(
            "aggregate.trade_status_decomposition",
            status_mismatch == 0,
            "the four trade-finance statuses sum to the total; active + issued equal the live book",
            {"mismatches": status_mismatch},
        ),
        Check(
            "aggregate.corridor_shares_sum_to_one",
            corridor_shares == 0,
            "every corridor breakdown's shares sum to 1 within its entity, scope and dimension",
            {"violations": corridor_shares},
        ),
        Check(
            "aggregate.no_negative_volumes",
            negatives == 0,
            "no client carries a negative pillar volume",
            {"violations": negatives},
        ),
    ]
    return results


def check_no_duplication(connection: duckdb.DuckDBPyConnection) -> list[Check]:
    """The join chain never fans a client out, and the pillars are never summed."""
    results = []
    for table in ("txn_features_by_scope", "xb_features_by_scope", "tf_features_by_scope"):
        duplicates = _scalar(
            connection,
            f"SELECT COUNT(*) FROM (SELECT entity_id, scope FROM {table} "
            "GROUP BY 1, 2 HAVING COUNT(*) > 1)",
        )
        results.append(
            Check(
                f"no_duplication.{table}",
                duplicates == 0,
                f"{table} holds one row per entity per scope",
                {"duplicate_keys": duplicates},
            )
        )

    external_rows = _scalar(connection, "SELECT COUNT(*) FROM external_financials_zar")
    expected_external = config.EXPECTED_ENTITY_COUNT * len(config.EXTERNAL_FIELDS)
    results.append(
        Check(
            "no_duplication.external_financials_zar",
            external_rows == expected_external,
            f"the external table holds {expected_external} rows (20 entities x "
            f"{len(config.EXTERNAL_FIELDS)} fields)",
            {"rows": external_rows},
        )
    )

    # A column equal to transactional + cross-border volume would be a
    # cross-pillar total. Those pillars overlap on the SWIFT channel by an
    # unresolvable amount, so such a column must not exist.
    blended = _scalar(
        connection,
        """
        SELECT COUNT(*) FROM (
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'client_features'
              AND (lower(column_name) LIKE '%combined%'
                   OR lower(column_name) LIKE '%all_pillar%'
                   OR lower(column_name) LIKE '%total_flow%')
        )
        """,
    )
    results.append(
        Check(
            "no_duplication.no_cross_pillar_total",
            blended == 0,
            "no feature blends the transactional and cross-border pillars into one number",
            {"suspect_columns": blended},
        )
    )
    return results


def check_required_not_null(connection: duckdb.DuckDBPyConnection) -> list[Check]:
    """Required feature columns are fully populated, and ratios are NULL only
    where their denominator is genuinely absent."""
    condition = " OR ".join(f'"{column}" IS NULL' for column in config.REQUIRED_FEATURE_COLUMNS)
    nulls = _scalar(connection, f"SELECT COUNT(*) FROM client_features WHERE {condition}")
    results = [
        Check(
            "not_null.required_feature_columns",
            nulls == 0,
            "every required column is populated for all 20 clients",
            {"rows_with_nulls": nulls},
        )
    ]

    revenue_missing = _scalar(
        connection, "SELECT COUNT(*) FROM client_master WHERE revenue_total_zar IS NULL"
    )
    results.append(
        Check(
            "not_null.revenue_denominator_available",
            revenue_missing == 0,
            "a ZAR revenue denominator exists for all 20 clients",
            {"clients_without_revenue": revenue_missing},
        )
    )

    # Every unexplained NULL ratio must trace to an absent denominator, which is
    # a documented data gap rather than a pipeline defect.
    unexplained = []
    for ratio in ratios.RATIOS:
        count = _scalar(
            connection,
            f"SELECT COUNT(*) FROM client_features "
            f"WHERE {ratio.name} IS NULL AND {ratio.denominator} IS NOT NULL "
            f"AND {ratio.denominator} > 0 AND {ratio.numerator} IS NOT NULL",
        )
        if count:
            unexplained.append({"ratio": ratio.name, "rows": count})
    results.append(
        Check(
            "not_null.ratios_null_only_without_denominator",
            not unexplained,
            "a ratio is NULL only when its denominator is absent or non-positive",
            {"unexplained": unexplained},
        )
    )
    return results


def check_external_coverage(connection: duckdb.DuckDBPyConnection) -> list[Check]:
    """The external store keeps its documented shape and vocabulary."""
    unknown_status = _scalar(
        connection,
        "SELECT COUNT(*) FROM external_financials_zar WHERE status NOT IN "
        + _sql_list(config.KNOWN_STATUSES),
    )
    unknown_fields = _scalar(
        connection,
        "SELECT COUNT(DISTINCT field) FROM ext_norm WHERE field NOT IN "
        + _sql_list(config.EXTERNAL_FIELDS),
    )
    missing_fields = _scalar(
        connection,
        f"""
        SELECT COUNT(*) FROM (
            SELECT entity_id, COUNT(DISTINCT field) AS n FROM external_financials_zar
            GROUP BY entity_id
        ) WHERE n <> {len(config.EXTERNAL_FIELDS)}
        """,
    )
    return [
        Check(
            "external.status_vocabulary",
            unknown_status == 0,
            "every status is a recognised reason code",
            {"unknown_status_rows": unknown_status},
        ),
        Check(
            "external.field_vocabulary",
            unknown_fields == 0,
            "the source carries no field missing from config.FX_BASIS_BY_FIELD",
            {"unmapped_fields": unknown_fields},
        ),
        Check(
            "external.every_entity_has_every_field",
            missing_fields == 0,
            "the external store is a complete 20 x 19 grid",
            {"entities_with_missing_fields": missing_fields},
        ),
    ]


def check_wide_projection(connection: duckdb.DuckDBPyConnection, discrepancies: int) -> Check:
    """The display-only wide CSV still matches the canonical long store."""
    return Check(
        "external.wide_projection_reconciles",
        discrepancies == 0,
        "external_financials_wide.csv remains a faithful projection of the long store",
        {"discrepant_cells": discrepancies},
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def run_all(
    connection: duckdb.DuckDBPyConnection,
    crosscheck: fx.FxCrosscheck,
    wide_discrepancies: int,
) -> ValidationReport:
    """Run every check and return the collected report."""
    report = ValidationReport()
    report.checks.extend(check_entity_count(connection))
    report.checks.extend(check_entity_joins(connection))
    report.checks.extend(check_sectors(connection))
    report.checks.extend(check_fx_rates(connection, crosscheck))
    report.checks.extend(check_fx_conversion(connection))
    report.checks.extend(check_fiscal_alignment(connection))
    report.checks.extend(check_aggregates(connection))
    report.checks.extend(check_no_duplication(connection))
    report.checks.extend(check_required_not_null(connection))
    report.checks.extend(check_external_coverage(connection))
    report.checks.append(check_wide_projection(connection, wide_discrepancies))
    return report


def assert_all(report: ValidationReport) -> None:
    """Raise :class:`ValidationError` if any check failed."""
    if report.passed:
        return
    lines = [f"{check.name}: {check.detail} -> {check.observed}" for check in report.failures]
    raise ValidationError(
        f"{len(report.failures)} of {len(report.checks)} feature-layer checks failed:\n  "
        + "\n  ".join(lines)
    )
