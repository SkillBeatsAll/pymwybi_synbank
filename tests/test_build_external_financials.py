import csv
from pathlib import Path

from src.syn_wallet.build_external_financials import build

FINANCES_DIR = Path(__file__).resolve().parents[1] / "data" / "finances"


def _rows(output_path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(output_path.open()))


def test_output_schema_and_row_count(tmp_path: Path) -> None:
    output_path = tmp_path / "external_financials.csv"
    summary = build(FINANCES_DIR, output_path)
    rows = _rows(output_path)

    assert list(rows[0].keys()) == [
        "entity_id", "entity_name", "fy_label", "field",
        "value_zar", "unit", "confidence", "source_page",
    ]
    # 380 source rows minus the 40 free-text rows (lenders_named, debt_maturity_note_page).
    assert summary["rows_written"] == len(rows) == 340
    assert all(row["unit"] in {"ZAR", "count"} for row in rows)
    assert all(row["field"] not in {"lenders_named", "debt_maturity_note_page"} for row in rows)


def test_undisclosed_and_fx_blocked_values_are_null_not_guessed(tmp_path: Path) -> None:
    output_path = tmp_path / "external_financials.csv"
    build(FINANCES_DIR, output_path)
    rows = {(row["entity_id"], row["field"]): row for row in _rows(output_path)}

    # Sanlam's fx_forward_notional was NOT_EXTRACTED in the source - must stay null, not estimated.
    sanlam_fx = rows[("E08", "fx_forward_notional")]
    assert sanlam_fx["value_zar"] == ""
    assert float(sanlam_fx["confidence"]) == 0.0

    # NEPI Rockcastle (EUR reporter) has an unverified fiscal year end, so its FX window is
    # blocked - every currency field must be null, never converted with a guessed rate.
    nepi_currency_rows = [row for row in rows.values() if row["entity_id"] == "E13" and row["unit"] == "ZAR"]
    assert nepi_currency_rows
    assert all(row["value_zar"] == "" and float(row["confidence"]) == 0.0 for row in nepi_currency_rows)

    # employees is a headcount, not a currency field, so it is unaffected by the FX block.
    nepi_employees = rows[("E13", "employees")]
    assert nepi_employees["value_zar"] == "687.0"
    assert nepi_employees["unit"] == "count"


def test_zar_conversion_uses_the_fiscal_year_average_rate(tmp_path: Path) -> None:
    output_path = tmp_path / "external_financials.csv"
    build(FINANCES_DIR, output_path)
    rows = {(row["entity_id"], row["field"]): row for row in _rows(output_path)}

    bhp_revenue = rows[("E01", "revenue_total")]
    # 51,262,000,000 USD * 18.1569 (BHP's FY2025 average rate) - not the closing rate.
    assert abs(float(bhp_revenue["value_zar"]) - 51_262_000_000.0 * 18.1569) < 1.0
    assert float(bhp_revenue["confidence"]) == 0.9


def test_confidence_is_always_between_zero_and_one(tmp_path: Path) -> None:
    output_path = tmp_path / "external_financials.csv"
    build(FINANCES_DIR, output_path)
    for row in _rows(output_path):
        assert 0.0 <= float(row["confidence"]) <= 1.0
