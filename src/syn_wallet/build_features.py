"""Stage 2 of the pipeline: the client-level analytical feature layer.

Stage 1 (:mod:`src.syn_wallet.clean_data`) turns the three raw CSVs into cleaned
Parquet. This stage turns that Parquet plus the prepared external financials
into a modelling-ready feature table, one row per client.

::

    data/processed/*.parquet ─┐
                              ├─► entity_dim ─► fy_fx_rates ─► entity_fx
    data/finances/*.csv ──────┘        │              │
                                       │              ▼
                                       │      external_financials_zar
                                       │              │
                                       ▼              ▼
                              internal features   external_wide_zar
                                       └──────┬───────┘
                                              ▼
                                   client_master ─► client_features

What this stage deliberately does **not** do:

* it does not estimate share of wallet, total wallet, or a competitor's wallet;
* it does not apply a fee, margin, or pricing assumption -- Syn Bank is
  fictional and has no disclosed pricing, so any rand of "revenue" would be
  invented;
* it does not sum the transactional and cross-border pillars, which overlap on
  279,389 SWIFT-channel rows by an unresolvable amount;
* it does not fit a model.

Run it with::

    python -m src.syn_wallet.build_features --overwrite
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from . import config, external_features, fx, internal_features, ratios, sources, validation

#: Headline measures carried for the trailing-year trend scopes.
TREND_MEASURES = {
    "txn_features_by_scope": ["txn_total_volume_zar", "txn_transaction_count"],
    "xb_features_by_scope": ["xb_total_volume_zar", "xb_transaction_count"],
    "tf_features_by_scope": ["tf_total_value_zar", "tf_instrument_count"],
}


def build_entity_dim(connection: duckdb.DuckDBPyConnection) -> None:
    """Materialise the entity dimension used by every downstream table."""
    connection.execute(
        f"CREATE OR REPLACE TABLE entity_dim AS {sources.entity_dimension_sql()}"
    )


def build_client_master(connection: duckdb.DuckDBPyConnection) -> None:
    """Create ``client_master``: the one-row-per-client spine.

    Identity, sector, fiscal period, the FX rates that converted this client's
    accounts, and every external financial metric in both native currency and
    ZAR. Deliberately free of internal flow measures so it can be joined to any
    future model output without carrying the feature table's width.
    """
    external_columns = [
        name
        for name, *_ in connection.execute("DESCRIBE external_wide_zar").fetchall()
        if name != "entity_id"
    ]
    projected = ",\n               ".join(f'w."{name}"' for name in external_columns)
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE client_master AS
        SELECT e.entity_id,
               e.entity_name,
               e.sector,
               e.fy_label,
               e.fiscal_year_end,
               e.fy_start,
               e.reporting_currency,
               f.fx_avg_rate_zar_per_unit,
               f.fx_closing_rate_zar_per_unit,
               f.fx_conversion_basis,
               f.fx_observation_count,
               e.flow_window_start,
               e.flow_window_end,
               e.fye_basis,
               {projected}
        FROM entity_dim e
        JOIN entity_fx f USING (entity_id)
        JOIN external_wide_zar w USING (entity_id)
        ORDER BY e.entity_id
        """
    )


def build_client_features(connection: duckdb.DuckDBPyConnection) -> None:
    """Create ``client_features``: the modelling-ready table.

    ``client_master`` plus the complete internal feature set for the full window
    and the client's own fiscal year, the trailing-year headline measures, the
    year-on-year trends, and the analytical ratios.

    Metric suffixes: ``_36m`` full window, ``_fy`` client fiscal year,
    ``_r12m`` / ``_p12m`` trailing years.
    """
    projections: list[str] = []
    for table in ("txn_features_by_scope", "xb_features_by_scope", "tf_features_by_scope"):
        for scope in config.FULL_METRIC_SCOPES:
            projections.append(internal_features.scope_projection(connection, table, scope))
        for scope in config.TREND_SCOPES:
            projections.append(
                internal_features.scope_projection(connection, table, scope, TREND_MEASURES[table])
            )

    joins = "\n        ".join(
        f"JOIN ({sql}) AS s{index} USING (entity_id)" for index, sql in enumerate(projections)
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE client_features_base AS
        SELECT m.*
               {"".join(f", s{index}.* EXCLUDE (entity_id)" for index in range(len(projections)))}
        FROM client_master m
        {joins}
        """
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE client_features AS
        SELECT *,
               {ratios.trend_sql()},
               {ratios.ratio_sql()}
        FROM client_features_base
        ORDER BY entity_id
        """
    )


def write_outputs(connection: duckdb.DuckDBPyConnection, output_dir: Path) -> dict[str, str]:
    """Write every output table to ZSTD Parquet and return the paths written."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for name in config.OUTPUT_PARQUET:
        path = output_dir / f"{name}.parquet"
        connection.execute(
            f"COPY {name} TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        written[name] = str(path)
    return written


def _summary(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Portfolio-level counts and side-by-side pillar totals for the run report."""
    totals = connection.execute(
        """
        SELECT CAST(SUM(txn_total_volume_zar_36m) AS VARCHAR),
               CAST(SUM(xb_total_volume_zar_36m) AS VARCHAR),
               CAST(SUM(tf_total_value_zar_36m) AS VARCHAR),
               CAST(SUM(tf_live_value_zar_36m) AS VARCHAR),
               SUM(txn_transaction_count_36m),
               SUM(xb_transaction_count_36m),
               SUM(tf_instrument_count_36m)
        FROM client_features
        """
    ).fetchone()
    feature_columns = connection.execute("DESCRIBE client_features").fetchall()
    return {
        "clients": connection.execute("SELECT COUNT(*) FROM client_features").fetchone()[0],
        "feature_columns": len(feature_columns),
        "ratios": len(ratios.RATIOS),
        # Reported side by side and never summed: the transactional and
        # cross-border pillars overlap on the SWIFT channel by an amount the
        # supplied fields cannot resolve.
        "pillar_totals_zar_36m": {
            "transactional": totals[0],
            "cross_border": totals[1],
            "trade_finance_all_statuses": totals[2],
            "trade_finance_live_only": totals[3],
        },
        "pillar_row_counts_36m": {
            "transactional": int(totals[4]),
            "cross_border": int(totals[5]),
            "trade_finance": int(totals[6]),
        },
    }


def run(
    processed_dir: Path | None = None,
    finances_dir: Path | None = None,
    output_dir: Path | None = None,
    overwrite: bool = False,
    strict: bool = True,
) -> dict[str, Any]:
    """Build the whole feature layer and write it to ``output_dir``.

    Args:
        processed_dir: Where stage 1's cleaned Parquet lives.
        finances_dir: Where the prepared external financial CSVs live.
        output_dir: Where to write the feature Parquet files.
        overwrite: Replace existing outputs instead of refusing.
        strict: Raise on any validation failure. Set False to inspect a failing
            run's report without losing the outputs.

    Returns:
        The run report, also written to ``feature_report.json``.
    """
    processed_dir = (processed_dir or config.PROCESSED_DIR).resolve()
    finances_dir = (finances_dir or config.FINANCES_DIR).resolve()
    output_dir = (output_dir or config.PROCESSED_DIR).resolve()

    targets = [output_dir / f"{name}.parquet" for name in config.OUTPUT_PARQUET]
    targets.append(output_dir / config.FEATURE_REPORT_PATH.name)
    existing = [path for path in targets if path.exists()]
    if existing and not overwrite:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(f"Output already exists ({names}). Re-run with --overwrite.")

    connection = sources.connect(processed_dir, finances_dir)
    try:
        build_entity_dim(connection)
        fx_crosscheck = fx.build(connection)
        external_coverage = external_features.build(connection)
        internal_features.build(connection)
        build_client_master(connection)
        build_client_features(connection)

        report = validation.run_all(
            connection, fx_crosscheck, external_coverage["wide_projection_discrepancies"]
        )
        if strict:
            validation.assert_all(report)

        written = write_outputs(connection, output_dir)
        run_report = {
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "policy": {
                "fx_flow_fields": "fiscal-year average SARB rate over the entity's own fiscal year",
                "fx_stock_fields": (
                    "fiscal-year-end closing SARB rate: the last observation on or before "
                    "the fiscal year end"
                ),
                "fx_basis_source": "fx_rates_sarb_daily.csv, windowed by entities.csv fiscal_year_end",
                "period_alignment": (
                    "ratios pair fiscal-year internal flow with same-fiscal-year external financials"
                ),
                "pillar_policy": (
                    "transactional, cross-border and trade finance are reported side by side "
                    "and never summed"
                ),
                "pricing_policy": "no fee, margin or basis-point assumption is applied anywhere",
                "gap_policy": "an explained absence stays NULL and is never imputed to zero",
            },
            "fx_crosscheck": {
                "fy_window_rows_compared": fx_crosscheck.fy_window_rows_compared,
                "fy_window_max_abs_diff": fx_crosscheck.fy_window_max_abs_diff,
                "self_reported_rows_compared": fx_crosscheck.self_reported_rows_compared,
                "self_reported_max_abs_pct_diff": fx_crosscheck.self_reported_max_abs_pct_diff,
            },
            "external_coverage": external_coverage,
            "summary": _summary(connection),
            "validation": report.to_dict(),
            "outputs": written,
        }
        report_path = output_dir / config.FEATURE_REPORT_PATH.name
        report_path.write_text(json.dumps(run_report, indent=2, default=str) + "\n", encoding="utf-8")
        return run_report
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the client-level feature layer.")
    parser.add_argument("--processed-dir", type=Path, default=config.PROCESSED_DIR)
    parser.add_argument("--finances-dir", type=Path, default=config.FINANCES_DIR)
    parser.add_argument("--output-dir", type=Path, default=config.PROCESSED_DIR)
    parser.add_argument("--overwrite", action="store_true", help="Replace existing feature outputs.")
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="Write outputs even when validation fails, for diagnosis.",
    )
    args = parser.parse_args()
    report = run(
        processed_dir=args.processed_dir,
        finances_dir=args.finances_dir,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
        strict=not args.no_strict,
    )
    summary = report["summary"]
    checks = report["validation"]
    print(
        f"client_features: {summary['clients']} clients x {summary['feature_columns']} columns "
        f"({summary['ratios']} ratios)"
    )
    print(
        f"validation: {checks['checks_run'] - checks['failures']}/{checks['checks_run']} checks passed"
    )
    for pillar, total in summary["pillar_totals_zar_36m"].items():
        print(f"  {pillar:<28} R{float(total):,.2f}")
    for name, path in report["outputs"].items():
        print(f"  wrote {name} -> {path}")


if __name__ == "__main__":
    main()
