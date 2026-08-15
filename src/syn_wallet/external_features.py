"""Canonical external financial table, converted to ZAR on an explicit basis.

Input is ``external_financials_normalized.csv`` -- the long-format store of 380
rows (20 entities x 19 fields). ``external_financials_wide.csv`` is **not** an
analytical input: it has no ``status`` column, so its 86 absent cells are bare
empty strings that become indistinguishable from the 10 genuine zeros after any
``fillna(0)``. It is loaded only to reconcile against, as a display-only check.

Conversion rule, applied per field from :data:`config.FX_BASIS_BY_FIELD`:

* flow measures (revenue, cost of sales, finance costs, capex) convert at the
  **fiscal-year average** rate;
* stock measures (inventory, receivables, payables, debt, cash, facilities, FX
  notional) convert at the **fiscal-year-end closing** rate;
* ``employees`` and the two text fields are never converted.

A value converts only when ``status = 'OK'`` and a number is present. An
explained absence stays NULL and never becomes a zero -- the difference between
"this client discloses zero debt" and "we could not find this client's debt" is
exactly the difference a wallet gap measure depends on.
"""

from __future__ import annotations

import duckdb

from . import config
from .config import FxBasis

#: Reconciliation tolerance between the long store and the wide projection.
#: Both hold IEEE doubles parsed from the same decimal text.
WIDE_RECONCILIATION_TOLERANCE = 1e-6

#: Relative tolerance for the internal accounting identities. Reported figures
#: are exact to the rand, so this only absorbs floating-point error from the FX
#: multiplication -- it is not a materiality threshold. A genuine reconciliation
#: failure of a few hundred million rand must fail the identity, not round away.
IDENTITY_TOLERANCE = 1e-9


def _fx_basis_case(column: str) -> str:
    """SQL CASE mapping a field name to its FX basis label."""
    branches = "\n               ".join(
        f"WHEN '{field}' THEN '{basis.value}'"
        for field, basis in config.FX_BASIS_BY_FIELD.items()
    )
    return f"CASE {column}\n               {branches}\n               ELSE 'unmapped' END"


def build_external_financials_zar(connection: duckdb.DuckDBPyConnection) -> None:
    """Create ``external_financials_zar``: one row per entity x field.

    Carries the native value, the ZAR value, the rate used, the rate type, the
    conversion basis, and the source ``status``/``basis``/``gap_reason`` so a
    downstream model can down-weight a soft figure without re-reading the CSV.
    """
    soft_basis_list = ", ".join(f"'{value}'" for value in config.SOFT_BASIS)
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE external_financials_zar AS
        WITH mapped AS (
            SELECT n.entity_id,
                   d.entity_name,
                   d.sector,
                   d.fy_label,
                   d.fiscal_year_end,
                   d.fy_start,
                   d.reporting_currency,
                   n.field,
                   n.unit_type,
                   n.value_numeric AS value_native,
                   n.value_text,
                   n.status,
                   n.basis,
                   n.gap_reason,
                   {_fx_basis_case('n.field')} AS fx_rate_type,
                   f.fx_avg_rate_zar_per_unit,
                   f.fx_closing_rate_zar_per_unit,
                   f.fx_conversion_basis
            FROM ext_norm n
            JOIN entity_dim d USING (entity_id)
            JOIN entity_fx  f USING (entity_id)
        ),
        priced AS (
            SELECT *,
                   CASE fx_rate_type
                        WHEN '{FxBasis.AVERAGE.value}' THEN fx_avg_rate_zar_per_unit
                        WHEN '{FxBasis.CLOSING.value}' THEN fx_closing_rate_zar_per_unit
                        ELSE NULL
                   END AS fx_rate_used
            FROM mapped
        )
        SELECT entity_id,
               entity_name,
               sector,
               fy_label,
               fiscal_year_end,
               fy_start,
               reporting_currency,
               field,
               unit_type,
               value_native,
               CASE
                    WHEN status <> 'OK' OR value_native IS NULL THEN NULL
                    WHEN fx_rate_type = '{FxBasis.NONE.value}' THEN NULL
                    ELSE value_native * fx_rate_used
               END AS value_zar,
               fx_rate_used,
               fx_rate_type,
               CASE WHEN fx_rate_type = '{FxBasis.NONE.value}'
                    THEN '{FxBasis.NONE.value}' ELSE fx_conversion_basis END AS fx_conversion_basis,
               value_text,
               status,
               basis,
               basis IN ({soft_basis_list}) AS is_soft_basis,
               -- "A usable number is present." Text fields are never usable by
               -- this definition; they are projected separately.
               status = 'OK' AND value_native IS NOT NULL AS is_usable,
               gap_reason
        FROM priced
        ORDER BY entity_id, field
        """
    )


#: The two text fields hold no number, so the wide projection carries what is
#: actually usable from them. ``lenders_named`` is direct competitor evidence --
#: the banks a client names in its facilities note, to be read against the
#: competitor-lending memos in the transactional ledger.
_TEXT_PROJECTIONS = {
    # ``is_usable`` means "a usable *number* is present", which is never true of
    # a text field, so these filter on status and text presence directly.
    "lenders_named": "MAX(CASE WHEN field = 'lenders_named' AND status = 'OK' "
    "THEN value_text END) AS lenders_named",
    "debt_maturity_note_page": "MAX(CASE WHEN field = 'debt_maturity_note_page' AND status = 'OK' "
    "AND value_text IS NOT NULL THEN TRUE ELSE FALSE END) AS has_debt_maturity_disclosure",
}


def _wide_column(field: str) -> str:
    basis = config.FX_BASIS_BY_FIELD[field]
    if field in _TEXT_PROJECTIONS:
        return _TEXT_PROJECTIONS[field]
    if basis is FxBasis.NONE:
        return f"MAX(CASE WHEN field = '{field}' THEN value_native END) AS {field}"
    return (
        f"MAX(CASE WHEN field = '{field}' THEN value_native END) AS {field}_native,\n"
        f"           MAX(CASE WHEN field = '{field}' THEN value_zar END) AS {field}_zar"
    )


def build_external_wide_zar(connection: duckdb.DuckDBPyConnection) -> None:
    """Create ``external_wide_zar``: one row per entity, ZAR-converted metrics.

    Adds the derived balances a wallet model reaches for immediately -- net
    debt, working capital, the two internal identity checks -- and the
    denominator-quality flags that determine how far a ratio built on
    ``revenue_total`` can be trusted.
    """
    columns = ",\n           ".join(_wide_column(field) for field in config.EXTERNAL_FIELDS)
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE external_wide_zar AS
        WITH pivoted AS (
            SELECT entity_id,
                   {columns},
                   MAX(CASE WHEN field = 'revenue_total' THEN basis END) AS revenue_total_basis,
                   MAX(CASE WHEN field = 'revenue_total' THEN is_soft_basis END)
                       AS revenue_total_is_soft_basis,
                   COUNT(*) FILTER (WHERE unit_type = 'currency' AND is_usable) AS usable_monetary_fields,
                   COUNT(*) FILTER (WHERE unit_type = 'currency') AS total_monetary_fields
            FROM external_financials_zar
            GROUP BY entity_id
        )
        SELECT *,
               -- Competitor evidence: how many banks the client names in its
               -- facilities note. NULL where the note was not disclosed.
               CASE WHEN lenders_named IS NULL THEN NULL
                    ELSE len(str_split(trim(lenders_named), ';')) END AS named_lender_count,
               -- Derived balances.
               gross_debt_zar - cash_and_equivalents_zar AS net_debt_zar,
               trade_receivables_zar + inventory_zar - trade_payables_zar AS working_capital_zar,
               committed_facilities_total_zar - undrawn_facilities_zar AS drawn_facilities_zar,
               -- Identity checks. NULL where a leg is absent, so a missing leg
               -- never reads as a failed identity.
               CASE WHEN debt_current_zar IS NULL OR debt_noncurrent_zar IS NULL
                         OR gross_debt_zar IS NULL THEN NULL
                    ELSE ABS(gross_debt_zar - (debt_current_zar + debt_noncurrent_zar))
                         <= {IDENTITY_TOLERANCE} * GREATEST(ABS(gross_debt_zar), 1.0)
               END AS gross_debt_identity_ok,
               CASE WHEN revenue_south_africa_zar IS NULL OR revenue_foreign_zar IS NULL
                         OR revenue_total_zar IS NULL THEN NULL
                    ELSE ABS(revenue_total_zar - (revenue_south_africa_zar + revenue_foreign_zar))
                         <= {IDENTITY_TOLERANCE} * GREATEST(ABS(revenue_total_zar), 1.0)
               END AS revenue_split_identity_ok,
               -- Signed size of any revenue-split failure, so a geographic
               -- wallet split can be corrected rather than merely rejected.
               revenue_total_zar - (revenue_south_africa_zar + revenue_foreign_zar)
                   AS revenue_split_residual_zar
        FROM pivoted
        """
    )


def reconcile_wide_projection(connection: duckdb.DuckDBPyConnection) -> int:
    """Return the number of cells where the wide CSV disagrees with the long store.

    ``external_financials_wide.csv`` is display-only. This confirms it remains a
    faithful projection of the canonical long file rather than a second source
    of truth that has drifted.
    """
    monetary = ", ".join(f"'{field}'" for field in config.EXTERNAL_FIELDS)
    unpivot_columns = ", ".join(
        field for field in config.EXTERNAL_FIELDS if config.FX_BASIS_BY_FIELD[field] is not FxBasis.NONE
    )
    unpivot_columns += ", employees"
    return connection.execute(
        f"""
        WITH wide_long AS (
            UNPIVOT (SELECT entity_id, {unpivot_columns} FROM ext_wide)
            ON {unpivot_columns}
            INTO NAME field VALUE wide_value
        )
        SELECT COUNT(*)
        FROM wide_long w
        FULL JOIN (
            SELECT entity_id, field, value_native
            FROM external_financials_zar
            WHERE field IN ({monetary}) AND value_native IS NOT NULL
        ) n USING (entity_id, field)
        WHERE w.wide_value IS DISTINCT FROM NULL
          AND (n.value_native IS NULL
               OR ABS(w.wide_value - n.value_native)
                  > {WIDE_RECONCILIATION_TOLERANCE} * GREATEST(ABS(n.value_native), 1.0))
        """
    ).fetchone()[0]


def build(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Build the canonical ZAR external tables and return coverage counts."""
    build_external_financials_zar(connection)
    build_external_wide_zar(connection)
    rows, usable, converted = connection.execute(
        """
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE is_usable),
               COUNT(*) FILTER (WHERE value_zar IS NOT NULL)
        FROM external_financials_zar
        """
    ).fetchone()
    return {
        "external_rows": int(rows),
        "usable_values": int(usable),
        "zar_converted_values": int(converted),
        "wide_projection_discrepancies": reconcile_wide_projection(connection),
    }
