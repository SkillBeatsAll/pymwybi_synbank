"""Register every analytical input as a DuckDB view.

One module owns "where the data is". Feature modules receive a connection with
these views already present and never touch a file path.

Nothing here mutates a source. The cleaned Parquet files produced by
:mod:`src.syn_wallet.clean_data` and the prepared CSVs in ``data/finances`` are
read-only inputs to this stage.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from . import config


class MissingSourceError(FileNotFoundError):
    """Raised when a required analytical input is absent from the working tree."""


def _quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def missing_sources(
    processed_dir: Path | None = None, finances_dir: Path | None = None
) -> list[Path]:
    """Return the input paths that do not exist, in load order."""
    processed_dir = processed_dir or config.PROCESSED_DIR
    finances_dir = finances_dir or config.FINANCES_DIR
    candidates = [processed_dir / path.name for path in config.INTERNAL_PARQUET.values()]
    candidates += [finances_dir / path.name for path in config.FINANCE_CSV.values()]
    return [path for path in candidates if not path.is_file()]


def connect(
    processed_dir: Path | None = None,
    finances_dir: Path | None = None,
    database: str = ":memory:",
) -> duckdb.DuckDBPyConnection:
    """Open a connection with all analytical inputs registered as views.

    Views created:

    ``txn``, ``xb``, ``tf``
        The three cleaned internal flow datasets.
    ``entities_src``, ``ext_norm``, ``ext_wide``, ``fx_daily``, ``fx_fy_window``,
    ``fx_self_reported``, ``fx_crosscheck``, ``data_dictionary``, ``dq_exceptions``
        The prepared external financial inputs.
    """
    processed_dir = processed_dir or config.PROCESSED_DIR
    finances_dir = finances_dir or config.FINANCES_DIR

    absent = missing_sources(processed_dir, finances_dir)
    if absent:
        names = ", ".join(str(path) for path in absent)
        raise MissingSourceError(
            f"Missing analytical inputs: {names}. Restore the raw CSVs with "
            "`tar -xzf data/data.tgz -C data/` and rebuild with "
            "`python -m src.syn_wallet.clean_data --overwrite`."
        )

    connection = duckdb.connect(database)
    parquet_views = {
        "txn": "transactional_banking.parquet",
        "xb": "cross_border_payments.parquet",
        "tf": "trade_finance.parquet",
    }
    for view, filename in parquet_views.items():
        connection.execute(
            f"CREATE OR REPLACE VIEW {view} AS "
            f"SELECT * FROM read_parquet({_quote(processed_dir / filename)})"
        )

    csv_views = {
        "entities_src": "entities.csv",
        "ext_norm": "external_financials_normalized.csv",
        "ext_wide": "external_financials_wide.csv",
        "fx_self_reported": "fx_rates_normalized.csv",
        "fx_fy_window": "fx_rates_fy_window.csv",
        "fx_daily": "fx_rates_sarb_daily.csv",
        "fx_crosscheck": "fx_rate_crosscheck.csv",
        "data_dictionary": "data_dictionary.csv",
        "dq_exceptions": "data_quality_exceptions.csv",
    }
    for view, filename in csv_views.items():
        connection.execute(
            f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM "
            f"read_csv_auto({_quote(finances_dir / filename)}, header = true, nullstr = '')"
        )

    _create_helpers(connection)
    return connection


def _create_helpers(connection: duckdb.DuckDBPyConnection) -> None:
    """Install SQL macros used across the feature modules."""
    # Division that yields NULL rather than an error or an infinity when the
    # denominator is absent, zero, or negative. A zero denominator in this data
    # is either a genuine "the client discloses zero" or an unrecoverable gap;
    # neither produces a meaningful ratio.
    connection.execute(
        "CREATE OR REPLACE MACRO safe_div(numerator, denominator) AS "
        "CASE WHEN denominator IS NULL OR denominator <= 0 OR numerator IS NULL "
        "THEN NULL ELSE CAST(numerator AS DOUBLE) / CAST(denominator AS DOUBLE) END"
    )
    # Herfindahl-Hirschman concentration over an already-aggregated share column.
    connection.execute(
        "CREATE OR REPLACE MACRO share_squared(part, whole) AS "
        "CASE WHEN whole IS NULL OR whole <= 0 THEN NULL "
        "ELSE POWER(CAST(part AS DOUBLE) / CAST(whole AS DOUBLE), 2) END"
    )


def entity_dimension_sql() -> str:
    """One row per entity: identity, sector, reporting currency and fiscal period.

    ``entities.csv`` is authoritative for ``fy_label``, ``reporting_currency``
    and ``fiscal_year_end``. ``sector`` exists only on the flow datasets, where
    it is identical across all three, so it is taken from the union of them.

    The fiscal year is the closed interval ``[fiscal_year_end - 1 year + 1 day,
    fiscal_year_end]``. Every entity's window falls inside the internal flow
    window, so no client is period-aligned against partial internal data.
    """
    return f"""
    WITH flow_identity AS (
        SELECT entity_id, entity_name, sector FROM txn
        UNION
        SELECT entity_id, entity_name, sector FROM xb
        UNION
        SELECT entity_id, entity_name, sector FROM tf
    ),
    sectors AS (
        SELECT entity_id,
               any_value(sector) AS sector,
               COUNT(DISTINCT sector) AS distinct_sectors,
               COUNT(DISTINCT entity_name) AS distinct_names
        FROM flow_identity
        GROUP BY entity_id
    )
    SELECT e.entity_id,
           e.entity_name,
           s.sector,
           e.fy_label,
           e.reporting_currency,
           CAST(e.fiscal_year_end AS DATE) AS fiscal_year_end,
           CAST(CAST(e.fiscal_year_end AS DATE) - INTERVAL 1 YEAR + INTERVAL 1 DAY AS DATE) AS fy_start,
           e.fye_basis,
           s.distinct_sectors,
           s.distinct_names,
           DATE '{config.FLOW_WINDOW_START}' AS flow_window_start,
           DATE '{config.FLOW_WINDOW_END}' AS flow_window_end
    FROM entities_src e
    JOIN sectors s USING (entity_id)
    """
