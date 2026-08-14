"""Transform the raw AFS extraction in ``data/finances/`` into the locked
``external_financials.csv`` hand-off contract:

    entity_id, entity_name, fy_label, field, value_zar, unit, confidence, source_page

Source data (``data/finances/external_financials_normalized.csv``) already has a value,
a disclosure ``status``, and a ``source_reliability`` rating per (entity, field). This
script does not re-extract or re-verify anything; it only:

* converts every currency field to ZAR using the entity's own fiscal-year FX window
  (``data/finances/fx_rates_fy_window.csv``, derived from daily SARB rates), using the
  period-average rate for income/cash-flow items and the period-closing rate for
  balance-sheet stock items;
* derives a 0-1 ``confidence`` score from disclosure status and source reliability; and
* drops the two free-text fields (``lenders_named``, ``debt_maturity_note_page``) since
  they have no numeric ``value_zar`` to report — read them directly from
  ``external_financials_normalized.csv`` where needed (e.g. competitor-lender evidence).

A currency field for a non-ZAR reporter whose FX window is blocked (no verified fiscal
year end) is written with a null ``value_zar`` and confidence 0 rather than guessed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

FLOW_CURRENCY_FIELDS = (
    "revenue_total", "revenue_south_africa", "revenue_foreign",
    "cost_of_sales", "finance_costs", "capex",
)
STOCK_CURRENCY_FIELDS = (
    "inventory", "trade_receivables", "trade_payables", "gross_debt",
    "debt_current", "debt_noncurrent", "cash_and_equivalents",
    "fx_forward_notional", "committed_facilities_total", "undrawn_facilities",
)
TEXT_FIELDS_EXCLUDED = ("lenders_named", "debt_maturity_note_page")

CONFIDENCE_BY_RELIABILITY = {
    "AFS": 0.9,
    "NON_AFS": 0.6,
    "AFS_URL_UNSUPPORTED": 0.6,
    "UNSOURCED": 0.3,
}


def build(finances_dir: Path, output_path: Path) -> dict[str, int]:
    connection = duckdb.connect(":memory:")
    try:
        normalized = finances_dir / "external_financials_normalized.csv"
        fx_window = finances_dir / "fx_rates_fy_window.csv"
        connection.execute(f"CREATE TEMP TABLE normalized AS SELECT * FROM read_csv_auto('{normalized}', header = true)")
        connection.execute(f"CREATE TEMP TABLE fx_window AS SELECT * FROM read_csv_auto('{fx_window}', header = true)")

        flow_list = ", ".join(f"'{f}'" for f in FLOW_CURRENCY_FIELDS)
        stock_list = ", ".join(f"'{f}'" for f in STOCK_CURRENCY_FIELDS)
        excluded_list = ", ".join(f"'{f}'" for f in TEXT_FIELDS_EXCLUDED)

        connection.execute(
            f"""
            CREATE TEMP TABLE result AS
            WITH source AS (
                SELECT * FROM normalized WHERE field NOT IN ({excluded_list})
            ),
            fx AS (
                SELECT entity_id, fy_label, foreign_currency, avg_rate, closing_rate, status AS fx_status
                FROM fx_window
            ),
            joined AS (
                SELECT
                    s.entity_id, s.entity_name, s.fy_label, s.field, s.unit_type,
                    s.reporting_currency, s.value_numeric, s.status, s.source_reliability,
                    s.source_ref, s.source_doc,
                    f.avg_rate, f.closing_rate, f.fx_status,
                    CASE WHEN s.field IN ({flow_list}) THEN f.avg_rate
                         WHEN s.field IN ({stock_list}) THEN f.closing_rate
                         ELSE NULL END AS applicable_rate
                FROM source s
                LEFT JOIN fx f
                       ON f.entity_id = s.entity_id
                      AND f.fy_label = s.fy_label
                      AND f.foreign_currency = s.reporting_currency
            )
            SELECT
                entity_id,
                entity_name,
                fy_label,
                field,
                CASE
                    WHEN unit_type != 'currency' THEN value_numeric
                    WHEN status != 'OK' OR value_numeric IS NULL THEN NULL
                    WHEN reporting_currency = 'ZAR' THEN value_numeric
                    WHEN applicable_rate IS NOT NULL AND fx_status = 'OK' THEN value_numeric * applicable_rate
                    ELSE NULL
                END AS value_zar,
                CASE WHEN unit_type = 'currency' THEN 'ZAR' ELSE unit_type END AS unit,
                CASE
                    WHEN status != 'OK' THEN 0.0
                    WHEN unit_type = 'currency' AND reporting_currency != 'ZAR'
                         AND (applicable_rate IS NULL OR fx_status != 'OK') THEN 0.0
                    ELSE COALESCE(
                        {", ".join(f"CASE WHEN source_reliability = '{k}' THEN {v} END" for k, v in CONFIDENCE_BY_RELIABILITY.items())},
                        0.0
                    )
                END AS confidence,
                COALESCE(NULLIF(source_ref, ''), NULLIF(source_doc, '')) AS source_page
            FROM joined
            ORDER BY entity_id, field
            """
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        connection.execute(f"COPY result TO '{output_path}' (FORMAT CSV, HEADER true)")

        total = connection.execute("SELECT COUNT(*) FROM result").fetchone()[0]
        with_value = connection.execute("SELECT COUNT(*) FROM result WHERE value_zar IS NOT NULL").fetchone()[0]
        return {"rows_written": total, "rows_with_value_zar": with_value, "rows_null_or_fx_blocked": total - with_value}
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finances-dir", type=Path, default=Path("data/finances"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/external_financials.csv"))
    args = parser.parse_args()
    summary = build(args.finances_dir, args.output)
    print(
        f"external_financials.csv: {summary['rows_written']:,} rows -> "
        f"{summary['rows_with_value_zar']:,} with a value_zar, "
        f"{summary['rows_null_or_fx_blocked']:,} null (not disclosed / not extracted / FX window blocked)"
    )


if __name__ == "__main__":
    main()
