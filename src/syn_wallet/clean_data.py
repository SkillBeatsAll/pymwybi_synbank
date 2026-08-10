"""Create auditable, analysis-ready Parquet datasets from raw Syn Bank CSVs.

This module intentionally makes a small number of evidence-based changes:

* removes duplicate canonical business records;
* normalises currency codes to uppercase (the source contains ``ZAR`` and
  ``zar`` for the same currency); and
* represents blank optional values as NULL in typed Parquet.

It does *not* impute missing values, round monetary values, remove valid large
transactions, or choose an arbitrary record when an ID has conflicting payloads.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb


@dataclass(frozen=True)
class DatasetSpec:
    filename: str
    output_name: str
    identifier: str
    amount: str
    columns: tuple[str, ...]
    required: tuple[str, ...]
    date_column: str = "date"


TRANSACTIONAL_COLUMNS = (
    "transaction_id", "entity_id", "entity_name", "sector", "date",
    "leg_type", "direction", "amount_zar", "currency", "channel",
    "beneficiary_name", "reference", "memo",
)
CROSS_BORDER_COLUMNS = (
    "transaction_id", "entity_id", "entity_name", "sector", "date",
    "direction", "currency_pair", "value_zar", "counterparty_country",
    "corridor_type", "beneficiary_name", "reference", "memo",
)
TRADE_COLUMNS = (
    "instrument_id", "entity_id", "entity_name", "sector", "date",
    "instrument_type", "direction", "tenor_days", "value_zar",
    "counterparty_country", "commodity_or_contract_type", "status",
    "beneficiary_name", "reference", "memo",
)

SPECS = (
    DatasetSpec(
        "transactional_banking.csv", "transactional_banking", "transaction_id",
        "amount_zar", TRANSACTIONAL_COLUMNS,
        tuple(column for column in TRANSACTIONAL_COLUMNS if column != "memo"),
    ),
    DatasetSpec(
        "cross_border_payments.csv", "cross_border_payments", "transaction_id",
        "value_zar", CROSS_BORDER_COLUMNS,
        tuple(column for column in CROSS_BORDER_COLUMNS if column not in {"memo", "counterparty_country"}),
    ),
    DatasetSpec(
        "trade_finance.csv", "trade_finance", "instrument_id", "value_zar",
        TRADE_COLUMNS,
        tuple(column for column in TRADE_COLUMNS if column not in {"memo", "counterparty_country"}),
    ),
)


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_string(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _csv_relation(path: Path) -> str:
    """Return a DuckDB relation expression with blank cells represented as NULL."""
    return (
        f"read_csv_auto({_quote_string(path)}, header = true, all_varchar = true, "
        "nullstr = '', strict_mode = true)"
    )


def _typed_columns(spec: DatasetSpec) -> str:
    expressions: list[str] = []
    for column in spec.columns:
        quoted = _quote_identifier(column)
        if column == spec.date_column:
            expression = f"TRY_CAST({quoted} AS DATE) AS {quoted}"
        elif column == spec.amount:
            # Scale 16 preserves source monetary precision without rounding.
            expression = f"TRY_CAST({quoted} AS DECIMAL(30, 16)) AS {quoted}"
        elif column == "tenor_days":
            expression = f"TRY_CAST({quoted} AS INTEGER) AS {quoted}"
        elif column == "currency":
            expression = f"UPPER({quoted}) AS {quoted}"
        else:
            expression = f"{quoted}"
        expressions.append(expression)
    return ",\n                ".join(expressions)


def _assert_schema(connection: duckdb.DuckDBPyConnection, path: Path, spec: DatasetSpec) -> None:
    columns = [row[0] for row in connection.execute(f"DESCRIBE SELECT * FROM {_csv_relation(path)}").fetchall()]
    if tuple(columns) != spec.columns:
        raise AssertionError(
            f"{path.name} schema changed. Expected {spec.columns}; received {tuple(columns)}"
        )


def _required_nulls(connection: duckdb.DuckDBPyConnection, relation: str, spec: DatasetSpec) -> int:
    condition = " OR ".join(f"{_quote_identifier(column)} IS NULL" for column in spec.required)
    return connection.execute(f"SELECT COUNT(*) FROM {relation} WHERE {condition}").fetchone()[0]


def _dataset_report(connection: duckdb.DuckDBPyConnection, spec: DatasetSpec) -> dict[str, Any]:
    raw_count = connection.execute("SELECT COUNT(*) FROM raw").fetchone()[0]
    canonical_count = connection.execute("SELECT COUNT(*) FROM canonical").fetchone()[0]
    clean_count = connection.execute("SELECT COUNT(*) FROM cleaned").fetchone()[0]
    raw_distinct_count = connection.execute("SELECT COUNT(*) FROM (SELECT DISTINCT * FROM raw)").fetchone()[0]
    canonical_distinct_count = connection.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT * FROM canonical)"
    ).fetchone()[0]
    duplicate_id_groups = connection.execute(
        f"SELECT COUNT(*) FROM (SELECT {_quote_identifier(spec.identifier)} "
        f"FROM cleaned GROUP BY 1 HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    conflicting_rows = connection.execute(
        "SELECT COUNT(*) FROM cleaned WHERE has_identifier_conflict"
    ).fetchone()[0]
    amount_sum_raw = connection.execute(
        f"SELECT CAST(COALESCE(SUM({_quote_identifier(spec.amount)}), 0) AS VARCHAR) FROM canonical"
    ).fetchone()[0]
    amount_sum_clean = connection.execute(
        f"SELECT CAST(COALESCE(SUM({_quote_identifier(spec.amount)}), 0) AS VARCHAR) FROM cleaned"
    ).fetchone()[0]
    missing_raw = {
        column: connection.execute(
            f"SELECT COUNT(*) FROM raw WHERE {_quote_identifier(column)} IS NULL"
        ).fetchone()[0]
        for column in spec.columns
    }
    missing_clean = {
        column: connection.execute(
            f"SELECT COUNT(*) FROM cleaned WHERE {_quote_identifier(column)} IS NULL"
        ).fetchone()[0]
        for column in spec.columns
    }
    normalisation_counts: dict[str, int] = {}
    if "currency" in spec.columns:
        normalisation_counts["currency_uppercase"] = connection.execute(
            "SELECT COUNT(*) FROM raw WHERE currency IS NOT NULL AND currency <> UPPER(currency)"
        ).fetchone()[0]
    return {
        "source_rows": raw_count,
        "source_exact_duplicate_rows": raw_count - raw_distinct_count,
        "canonical_exact_duplicate_rows_removed": canonical_count - clean_count,
        "clean_rows": clean_count,
        "unique_identifiers_after_cleaning": connection.execute(
            f"SELECT COUNT(DISTINCT {_quote_identifier(spec.identifier)}) FROM cleaned"
        ).fetchone()[0],
        "identifier_conflict_groups_retained": duplicate_id_groups,
        "identifier_conflict_rows_retained": conflicting_rows,
        "source_amount_total_after_type_normalisation": amount_sum_raw,
        "clean_amount_total": amount_sum_clean,
        "missing_values_source": missing_raw,
        "missing_values_clean": missing_clean,
        "normalisation_counts": normalisation_counts,
    }


def _validate_dataset(connection: duckdb.DuckDBPyConnection, spec: DatasetSpec) -> None:
    """Assert that cleaning is complete and no business record was invented or lost."""
    if _required_nulls(connection, "canonical", spec) != 0:
        raise AssertionError(f"{spec.output_name}: a required source value is invalid or missing")
    if _required_nulls(connection, "cleaned", spec) != 0:
        raise AssertionError(f"{spec.output_name}: cleaning introduced a missing required value")

    business_columns = ", ".join(_quote_identifier(column) for column in spec.columns)
    clean_not_in_source = connection.execute(
        f"SELECT COUNT(*) FROM (SELECT {business_columns} FROM cleaned EXCEPT "
        f"SELECT {business_columns} FROM canonical)"
    ).fetchone()[0]
    source_not_in_clean = connection.execute(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT {business_columns} FROM canonical EXCEPT "
        f"SELECT {business_columns} FROM cleaned)"
    ).fetchone()[0]
    if clean_not_in_source or source_not_in_clean:
        raise AssertionError(
            f"{spec.output_name}: reconciliation failed; output contains changed or missing business records"
        )

    residual_exact_duplicates = connection.execute(
        f"SELECT COUNT(*) FROM (SELECT {business_columns}, COUNT(*) AS n FROM cleaned "
        f"GROUP BY {business_columns} HAVING n > 1)"
    ).fetchone()[0]
    if residual_exact_duplicates:
        raise AssertionError(f"{spec.output_name}: exact duplicate records remain")


def clean_dataset(input_path: Path, output_path: Path, spec: DatasetSpec) -> dict[str, Any]:
    """Clean one CSV and write a compressed Parquet file with reconciliation assertions."""
    connection = duckdb.connect(":memory:")
    try:
        _assert_schema(connection, input_path, spec)
        column_names = ", ".join(_quote_identifier(column) for column in spec.columns)
        connection.execute(f"CREATE TEMP TABLE raw AS SELECT {column_names} FROM {_csv_relation(input_path)}")
        connection.execute(
            f"CREATE TEMP TABLE canonical AS SELECT\n                {_typed_columns(spec)}\n            FROM raw"
        )
        connection.execute(
            f"CREATE TEMP TABLE deduplicated AS\n"
            f"SELECT {column_names}\n"
            "FROM (\n"
            f"    SELECT {column_names}, ROW_NUMBER() OVER (PARTITION BY {column_names}) AS duplicate_rank\n"
            "    FROM canonical\n"
            ")\n"
            "WHERE duplicate_rank = 1"
        )
        connection.execute(
            f"CREATE TEMP TABLE cleaned AS\n"
            f"SELECT {column_names},\n"
            f"       COUNT(*) OVER (PARTITION BY {_quote_identifier(spec.identifier)}) > 1 AS has_identifier_conflict\n"
            "FROM deduplicated"
        )
        _validate_dataset(connection, spec)
        report = _dataset_report(connection, spec)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        connection.execute(
            f"COPY cleaned TO {_quote_string(output_path)} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        return report
    finally:
        connection.close()


def run_pipeline(input_dir: Path, output_dir: Path, overwrite: bool = False) -> dict[str, Any]:
    """Run all cleaning steps and return the serialisable quality report."""
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    output_paths = [output_dir / f"{spec.output_name}.parquet" for spec in SPECS]
    report_path = output_dir / "quality_report.json"
    existing = [path for path in [*output_paths, report_path] if path.exists()]
    if existing and not overwrite:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(f"Output already exists ({names}). Re-run with --overwrite.")
    if output_dir == input_dir or output_dir == Path("/"):
        raise ValueError("The output directory must be a dedicated directory, not the raw input or filesystem root.")
    if overwrite:
        # Delete only known pipeline artefacts. Never recursively remove a user directory.
        for path in existing:
            path.unlink()
    output_dir.mkdir(parents=True, exist_ok=True)

    datasets: dict[str, Any] = {}
    for spec in SPECS:
        source = input_dir / spec.filename
        if not source.is_file():
            raise FileNotFoundError(f"Missing expected raw input: {source}")
        datasets[spec.output_name] = clean_dataset(source, output_dir / f"{spec.output_name}.parquet", spec)

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "policy": {
            "exact_duplicate_policy": "Remove duplicate canonical business records only.",
            "identifier_conflict_policy": "Retain differing records sharing an identifier and flag them.",
            "normalisations": ["Uppercase transactional currency codes."],
            "missing_value_policy": "Preserve optional unknowns as null; do not impute them.",
            "amount_policy": "Use DECIMAL(30,16); do not round source amounts.",
        },
        "datasets": datasets,
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--overwrite", action="store_true", help="Replace the pipeline-owned output directory.")
    args = parser.parse_args()
    report = run_pipeline(args.input_dir, args.output_dir, overwrite=args.overwrite)
    for dataset, result in report["datasets"].items():
        print(
            f"{dataset}: {result['source_rows']:,} source rows -> "
            f"{result['clean_rows']:,} clean rows; "
            f"{result['canonical_exact_duplicate_rows_removed']:,} canonical duplicates removed; "
            f"{result['identifier_conflict_groups_retained']:,} ID-conflict groups retained"
        )


if __name__ == "__main__":
    main()
