"""Fiscal-year ZAR conversion rates, derived from one basis for all 20 clients.

Nine of the twenty clients report in USD, EUR or GBP. Dividing a ZAR flow by an
unconverted foreign denominator understates those clients' wallets by roughly
17-24x and makes them look like the bank's best-penetrated accounts, so every
external monetary value is converted before any ratio is taken.

**Chosen basis: SARB daily mid-rates averaged over each entity's own fiscal
year.** ``fx_rates_sarb_daily.csv`` supplies 903 observations per currency from
2023-01-03 to 2026-08-14; ``entities.csv`` supplies all 20 fiscal year ends.
Every entity therefore gets a rate from the same method, with no per-entity
source switching.

* ``avg_rate`` -- arithmetic mean of daily mid-rates across the fiscal year.
  Applied to income-statement and cash-flow measures.
* ``closing_rate`` -- last observation on or before the fiscal year end.
  Applied to balance-sheet measures.

Two prepared files are used as **cross-checks, not as inputs**:

``fx_rates_fy_window.csv``
    Same method, but stale: its nine ``BLOCKED_NO_FYE`` rows for E11, E12 and
    E13 predate the fiscal year ends now in ``entities.csv``. The derivation
    here reproduces all 51 of its ``OK`` rows exactly and fills the nine gaps.
``fx_rates_normalized.csv``
    Each entity's self-reported average/closing rates (27 of 50 usable). Used
    only to confirm the derived rates agree with what the clients published.

ZAR reporters convert at exactly 1.0 with basis ``no_conversion``; no FX is
applied to them and no internal ZAR flow value is ever re-denominated.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from . import config

#: Maximum tolerated deviation between a derived rate and the corresponding
#: ``OK`` row of ``fx_rates_fy_window.csv``. Both use the same method, so this
#: is a reproduction check, not a reconciliation tolerance.
FY_WINDOW_TOLERANCE = 5e-4

#: Maximum tolerated deviation between a derived rate and an entity's own
#: published rate. The prepared crosscheck file reports agreement within 0.85%.
SELF_REPORTED_TOLERANCE_PCT = 1.5


@dataclass(frozen=True)
class FxCrosscheck:
    """Outcome of comparing derived rates against the two prepared rate files."""

    fy_window_rows_compared: int
    fy_window_max_abs_diff: float
    self_reported_rows_compared: int
    self_reported_max_abs_pct_diff: float


def build_fy_fx_rates(connection: duckdb.DuckDBPyConnection) -> None:
    """Create ``fy_fx_rates``: entity x foreign currency x (average, closing).

    Produces 60 rows (20 entities x 3 currencies) with no gaps.
    """
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE fy_fx_rates AS
        SELECT e.entity_id,
               e.entity_name,
               e.fy_label,
               d.foreign_currency,
               e.fy_start,
               e.fiscal_year_end AS fy_end,
               AVG(d.zar_per_unit) AS avg_rate,
               last(d.zar_per_unit ORDER BY d.date) AS closing_rate,
               COUNT(*) AS n_obs,
               MIN(d.date) AS first_observation,
               MAX(d.date) AS last_observation,
               'sarb_daily_fy_window' AS rate_source
        FROM entity_dim e
        JOIN fx_daily d
          ON d.date BETWEEN e.fy_start AND e.fiscal_year_end
        WHERE d.foreign_currency IN {config.FOREIGN_CURRENCIES}
        GROUP BY ALL
        """
    )


def build_entity_fx(connection: duckdb.DuckDBPyConnection) -> None:
    """Create ``entity_fx``: the single pair of rates that converts each entity.

    One row per entity. ZAR reporters get 1.0/1.0 and ``no_conversion``; foreign
    reporters get their reporting currency's fiscal-year average and closing
    rates and ``sarb_daily_fy_window``.
    """
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE entity_fx AS
        SELECT e.entity_id,
               e.entity_name,
               e.reporting_currency,
               e.fy_label,
               e.fy_start,
               e.fiscal_year_end,
               CASE WHEN e.reporting_currency = '{config.BASE_CURRENCY}'
                    THEN 1.0 ELSE r.avg_rate END AS fx_avg_rate_zar_per_unit,
               CASE WHEN e.reporting_currency = '{config.BASE_CURRENCY}'
                    THEN 1.0 ELSE r.closing_rate END AS fx_closing_rate_zar_per_unit,
               CASE WHEN e.reporting_currency = '{config.BASE_CURRENCY}'
                    THEN 'no_conversion' ELSE 'sarb_daily_fy_window' END AS fx_conversion_basis,
               CASE WHEN e.reporting_currency = '{config.BASE_CURRENCY}'
                    THEN NULL ELSE r.n_obs END AS fx_observation_count
        FROM entity_dim e
        LEFT JOIN fy_fx_rates r
               ON r.entity_id = e.entity_id
              AND r.foreign_currency = e.reporting_currency
        """
    )


def crosscheck(connection: duckdb.DuckDBPyConnection) -> FxCrosscheck:
    """Compare derived rates against both prepared rate files.

    Also creates ``fx_rate_reconciliation``, the row-level comparison against
    ``fx_rates_fy_window.csv`` including the nine rows that file could not
    populate.
    """
    connection.execute(
        """
        CREATE OR REPLACE TABLE fx_rate_reconciliation AS
        SELECT d.entity_id,
               d.entity_name,
               d.foreign_currency,
               d.avg_rate       AS derived_avg_rate,
               d.closing_rate   AS derived_closing_rate,
               d.n_obs          AS derived_n_obs,
               w.avg_rate       AS prepared_avg_rate,
               w.closing_rate   AS prepared_closing_rate,
               w.status         AS prepared_status,
               ABS(d.avg_rate - w.avg_rate)         AS avg_abs_diff,
               ABS(d.closing_rate - w.closing_rate) AS closing_abs_diff
        FROM fy_fx_rates d
        LEFT JOIN fx_fy_window w
               ON w.entity_id = d.entity_id
              AND w.foreign_currency = d.foreign_currency
        """
    )
    fy_rows, fy_max = connection.execute(
        """
        SELECT COUNT(*),
               COALESCE(MAX(GREATEST(avg_abs_diff, closing_abs_diff)), 0.0)
        FROM fx_rate_reconciliation
        WHERE prepared_status = 'OK'
        """
    ).fetchone()

    self_rows, self_max = connection.execute(
        """
        SELECT COUNT(*),
               COALESCE(MAX(ABS(pct_diff)), 0.0)
        FROM (
            SELECT 100.0 * (
                       CASE WHEN s.rate_type = 'average' THEN d.avg_rate ELSE d.closing_rate END
                       - s.zar_per_unit
                   ) / s.zar_per_unit AS pct_diff
            FROM fx_self_reported s
            JOIN fy_fx_rates d
              ON d.entity_id = s.entity_id
             AND d.foreign_currency = s.foreign_currency
            WHERE s.status = 'OK' AND s.zar_per_unit > 0
        )
        """
    ).fetchone()

    return FxCrosscheck(
        fy_window_rows_compared=int(fy_rows),
        fy_window_max_abs_diff=float(fy_max),
        self_reported_rows_compared=int(self_rows),
        self_reported_max_abs_pct_diff=float(self_max),
    )


def build(connection: duckdb.DuckDBPyConnection) -> FxCrosscheck:
    """Build both FX tables and return the crosscheck result."""
    build_fy_fx_rates(connection)
    build_entity_fx(connection)
    return crosscheck(connection)
