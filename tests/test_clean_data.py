import csv
from pathlib import Path

import duckdb

from src.syn_wallet.clean_data import SPECS, run_pipeline


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _base_row(spec, identifier: str) -> dict[str, str]:
    row = {column: "x" for column in spec.columns}
    row.update(
        {
            spec.identifier: identifier,
            "entity_id": "E01",
            "entity_name": "Example Corp",
            "sector": "consumer",
            "date": "2025-01-01",
            spec.amount: "100.123456789012",
            "beneficiary_name": "Beneficiary",
            "reference": "REF-1",
            "memo": "",
        }
    )
    if "currency" in row:
        row.update({"leg_type": "collections", "direction": "inbound", "currency": "zar", "channel": "EFT"})
    if "currency_pair" in row:
        row.update({"direction": "inbound", "currency_pair": "USD/ZAR", "counterparty_country": "", "corridor_type": "trade"})
    if "instrument_type" in row:
        row.update(
            {
                "instrument_type": "letters_of_credit",
                "direction": "import",
                "tenor_days": "90",
                "counterparty_country": "",
                "commodity_or_contract_type": "consumer_goods",
                "status": "active",
            }
        )
    return row


def test_pipeline_normalises_exact_duplicates_and_retains_identifier_conflicts(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for index, spec in enumerate(SPECS):
        first = _base_row(spec, f"ID-{index}-A")
        duplicate = first.copy()
        canonical_duplicate = first.copy()
        if "currency" in canonical_duplicate:
            canonical_duplicate["currency"] = "ZAR"
        conflict_a = _base_row(spec, f"ID-{index}-B")
        conflict_b = conflict_a.copy()
        conflict_b[spec.amount] = "200.123456789012"
        _write_csv(raw_dir / spec.filename, spec.columns, [first, duplicate, canonical_duplicate, conflict_a, conflict_b])

    output_dir = tmp_path / "processed"
    report = run_pipeline(raw_dir, output_dir)

    for spec in SPECS:
        result = report["datasets"][spec.output_name]
        assert result["source_rows"] == 5
        assert result["clean_rows"] == 3
        assert result["canonical_exact_duplicate_rows_removed"] == 2
        assert result["identifier_conflict_groups_retained"] == 1

        rows = duckdb.sql(
            f"SELECT * FROM read_parquet('{output_dir / (spec.output_name + '.parquet')}')"
        ).fetchall()
        columns = [item[0] for item in duckdb.sql(
            f"DESCRIBE SELECT * FROM read_parquet('{output_dir / (spec.output_name + '.parquet')}')"
        ).fetchall()]
        assert len(rows) == 3
        assert "has_identifier_conflict" in columns
        if "currency" in spec.columns:
            assert {row[columns.index("currency")] for row in rows} == {"ZAR"}


def test_supplied_data_passes_full_reconciliation(tmp_path: Path) -> None:
    """Exercise the same assertions against the actual hackathon source files."""
    repository_root = Path(__file__).resolve().parents[1]
    report = run_pipeline(repository_root / "data", tmp_path / "processed")

    expected = {
        "transactional_banking": (2_802_875, 2_791_803, 11_072),
        "cross_border_payments": (241_117, 240_191, 926),
        "trade_finance": (20_303, 20_215, 88),
    }
    for dataset, (source_rows, clean_rows, removed_rows) in expected.items():
        result = report["datasets"][dataset]
        assert result["source_rows"] == source_rows
        assert result["clean_rows"] == clean_rows
        assert result["canonical_exact_duplicate_rows_removed"] == removed_rows
        assert result["clean_rows"] == source_rows - removed_rows
