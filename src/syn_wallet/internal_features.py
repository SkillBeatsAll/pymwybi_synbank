"""Client-level features from the three internal flow pillars.

Every metric is computed once per entity per **scope**, where a scope is a date
window:

``full_window``
    2023-07-01 to 2026-06-30 -- the complete 36-month internal history.
``fiscal_year``
    The entity's own fiscal year. This is the only scope that may be divided by
    an external financial denominator, because it is the only one covering the
    same period the accounts report on. All 20 fiscal-year windows fall inside
    the flow window, so no client is aligned against partial internal data.
``recent_12m`` / ``prior_12m``
    Portfolio-common trailing years anchored on the flow-window end, used for
    trend measurement. Headline volume and count only.

Aggregates preserve the source ``DECIMAL(30,16)`` scale; nothing is rounded.
The three pillars are aggregated independently and never summed -- see
:data:`config.PILLARS`.
"""

from __future__ import annotations

import duckdb

from . import config

#: Zero literal at the aggregate decimal scale, so COALESCE keeps the type.
_ZERO = "CAST(0 AS DECIMAL(38,16))"


def build_entity_windows(connection: duckdb.DuckDBPyConnection) -> None:
    """Create ``entity_windows``: one row per entity x scope with its date bounds."""
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE entity_windows AS
        WITH bounds AS (
            SELECT entity_id, 'full_window' AS scope,
                   flow_window_start AS window_start, flow_window_end AS window_end
            FROM entity_dim
            UNION ALL
            SELECT entity_id, 'fiscal_year', fy_start, fiscal_year_end FROM entity_dim
            UNION ALL
            SELECT entity_id, 'recent_12m',
                   DATE '{config.RECENT_12M_START}', DATE '{config.RECENT_12M_END}'
            FROM entity_dim
            UNION ALL
            SELECT entity_id, 'prior_12m',
                   DATE '{config.PRIOR_12M_START}', DATE '{config.PRIOR_12M_END}'
            FROM entity_dim
        )
        SELECT *,
               DATEDIFF('month', window_start, window_end + INTERVAL 1 DAY) AS window_months,
               DATEDIFF('day', window_start, window_end) + 1 AS window_days
        FROM bounds
        """
    )


def _leg_volume(leg: str) -> str:
    """Volume, count, and the non-SWIFT (domestic-channel) volume for one leg.

    The domestic split is carried per leg, not just per client, because the
    wallet models need a leg-level numerator that provably excludes the
    SWIFT-channel rows overlapping the cross-border pillar. Deriving it by
    applying the client's overall SWIFT share to each leg would be an
    assumption; this is a measurement.
    """
    return (
        f"COALESCE(SUM(t.amount_zar) FILTER (WHERE t.leg_type = '{leg}'), {_ZERO}) "
        f"AS txn_{leg}_volume_zar,\n               "
        f"COALESCE(SUM(t.amount_zar) FILTER (WHERE t.leg_type = '{leg}' AND t.channel <> 'SWIFT'), "
        f"{_ZERO}) AS txn_{leg}_domestic_volume_zar,\n               "
        f"COUNT(*) FILTER (WHERE t.leg_type = '{leg}') AS txn_{leg}_count"
    )


def build_transactional_features(connection: duckdb.DuckDBPyConnection) -> None:
    """Create ``txn_features_by_scope``.

    ``memo`` is populated on only 0.13% of rows, and every populated memo
    describes lending Syn Bank is not the lender on (facility drawdowns,
    bridging finance, syndicate participations settling through the account).
    The count is carried as a competitor-lending intensity signal, not treated
    as missing data.

    ``SWIFT``-channel volume is separated from domestic volume because those
    rows conceptually overlap the cross-border pillar; keeping them addressable
    lets a downstream model exclude them without re-reading the ledger.
    """
    leg_columns = ",\n               ".join(_leg_volume(leg) for leg in config.LEG_TYPES)
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE txn_features_by_scope AS
        WITH aggregated AS (
            SELECT w.entity_id,
                   w.scope,
                   w.window_start,
                   w.window_end,
                   w.window_months,
                   w.window_days,
                   COALESCE(SUM(t.amount_zar), {_ZERO}) AS txn_total_volume_zar,
                   COALESCE(SUM(t.amount_zar) FILTER (WHERE t.direction = 'inbound'), {_ZERO})
                       AS txn_inbound_volume_zar,
                   COALESCE(SUM(t.amount_zar) FILTER (WHERE t.direction = 'outbound'), {_ZERO})
                       AS txn_outbound_volume_zar,
                   {leg_columns},
                   COALESCE(SUM(t.amount_zar) FILTER (WHERE t.channel = 'SWIFT'), {_ZERO})
                       AS txn_swift_channel_volume_zar,
                   COALESCE(SUM(t.amount_zar) FILTER (WHERE t.channel <> 'SWIFT'), {_ZERO})
                       AS txn_domestic_volume_zar,
                   COUNT(t.transaction_id) AS txn_transaction_count,
                   COUNT(DISTINCT t.date) AS txn_active_days,
                   COUNT(DISTINCT t.beneficiary_name) AS txn_distinct_beneficiaries,
                   COUNT(*) FILTER (WHERE t.memo IS NOT NULL) AS txn_memo_count,
                   COUNT(*) FILTER (WHERE t.has_identifier_conflict) AS txn_conflict_flagged_rows
            FROM entity_windows w
            LEFT JOIN txn t
                   ON t.entity_id = w.entity_id
                  AND t.date BETWEEN w.window_start AND w.window_end
            GROUP BY ALL
        )
        SELECT *,
               txn_inbound_volume_zar - txn_outbound_volume_zar AS txn_net_flow_zar,
               safe_div(txn_total_volume_zar, window_months) AS txn_monthly_avg_volume_zar,
               safe_div(txn_total_volume_zar, txn_transaction_count) AS txn_avg_transaction_zar,
               safe_div(txn_inbound_volume_zar, txn_outbound_volume_zar)
                   AS txn_inbound_outbound_ratio,
               safe_div(txn_active_days, window_days) AS txn_active_day_rate
        FROM aggregated
        """
    )


def _corridor_volume(corridor: str) -> str:
    return (
        f"COALESCE(SUM(x.value_zar) FILTER (WHERE x.corridor_type = '{corridor}'), {_ZERO}) "
        f"AS xb_{corridor}_corridor_volume_zar,\n               "
        f"COUNT(*) FILTER (WHERE x.corridor_type = '{corridor}') AS xb_{corridor}_corridor_count"
    )


def _pair_volume(pair: str) -> str:
    slug = pair.split("/")[0].lower()
    return (
        f"COALESCE(SUM(x.value_zar) FILTER (WHERE x.currency_pair = '{pair}'), {_ZERO}) "
        f"AS xb_pair_{slug}_volume_zar"
    )


def build_cross_border_features(connection: duckdb.DuckDBPyConnection) -> None:
    """Create ``xb_features_by_scope``.

    ``counterparty_country`` is NULL on 3,646 rows. Those rows are kept in the
    volume totals and bucketed as ``UNKNOWN`` for concentration, so country
    shares always sum to the pillar total.
    """
    corridor_columns = ",\n               ".join(
        _corridor_volume(corridor) for corridor in config.CORRIDOR_TYPES
    )
    pair_columns = ",\n               ".join(_pair_volume(pair) for pair in config.CURRENCY_PAIRS)
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE xb_features_by_scope AS
        WITH aggregated AS (
            SELECT w.entity_id,
                   w.scope,
                   w.window_start,
                   w.window_end,
                   w.window_months,
                   w.window_days,
                   COALESCE(SUM(x.value_zar), {_ZERO}) AS xb_total_volume_zar,
                   COALESCE(SUM(x.value_zar) FILTER (WHERE x.direction = 'inbound'), {_ZERO})
                       AS xb_inbound_volume_zar,
                   COALESCE(SUM(x.value_zar) FILTER (WHERE x.direction = 'outbound'), {_ZERO})
                       AS xb_outbound_volume_zar,
                   {corridor_columns},
                   {pair_columns},
                   COUNT(x.transaction_id) AS xb_transaction_count,
                   COUNT(DISTINCT x.date) AS xb_active_days,
                   -- COUNT(DISTINCT ...) ignores NULLs, so an unnamed
                   -- counterparty country is not counted as a country. The
                   -- unnamed rows stay in every volume total and are counted
                   -- separately so the gap is visible rather than hidden.
                   COUNT(DISTINCT x.counterparty_country) AS xb_active_countries,
                   COUNT(*) FILTER (WHERE x.counterparty_country IS NULL)
                       AS xb_unknown_country_count,
                   COUNT(DISTINCT x.currency_pair) AS xb_active_currency_pairs,
                   COUNT(*) FILTER (WHERE x.memo IS NOT NULL) AS xb_memo_count
            FROM entity_windows w
            LEFT JOIN xb x
                   ON x.entity_id = w.entity_id
                  AND x.date BETWEEN w.window_start AND w.window_end
            GROUP BY ALL
        ),
        by_country AS (
            SELECT w.entity_id,
                   w.scope,
                   COALESCE(x.counterparty_country, 'UNKNOWN') AS country,
                   SUM(x.value_zar) AS country_volume_zar
            FROM entity_windows w
            JOIN xb x
              ON x.entity_id = w.entity_id
             AND x.date BETWEEN w.window_start AND w.window_end
            GROUP BY ALL
        ),
        country_totals AS (
            SELECT entity_id, scope, SUM(country_volume_zar) AS total_volume_zar
            FROM by_country GROUP BY entity_id, scope
        ),
        country_stats AS (
            SELECT b.entity_id,
                   b.scope,
                   arg_max(b.country, b.country_volume_zar) AS xb_top_country,
                   MAX(b.country_volume_zar) AS xb_top_country_volume_zar,
                   SUM(share_squared(b.country_volume_zar, t.total_volume_zar)) AS xb_country_hhi
            FROM by_country b
            JOIN country_totals t USING (entity_id, scope)
            GROUP BY b.entity_id, b.scope
        )
        SELECT a.*,
               a.xb_inbound_volume_zar - a.xb_outbound_volume_zar AS xb_net_flow_zar,
               safe_div(a.xb_total_volume_zar, a.window_months) AS xb_monthly_avg_volume_zar,
               safe_div(a.xb_total_volume_zar, a.xb_transaction_count) AS xb_avg_transaction_zar,
               safe_div(a.xb_inbound_volume_zar, a.xb_outbound_volume_zar)
                   AS xb_inbound_outbound_ratio,
               c.xb_top_country,
               COALESCE(c.xb_top_country_volume_zar, {_ZERO}) AS xb_top_country_volume_zar,
               safe_div(c.xb_top_country_volume_zar, a.xb_total_volume_zar)
                   AS xb_top_country_share,
               c.xb_country_hhi
        FROM aggregated a
        LEFT JOIN country_stats c USING (entity_id, scope)
        """
    )


def _instrument_value(instrument: str, alias: str) -> str:
    return (
        f"COALESCE(SUM(f.value_zar) FILTER (WHERE f.instrument_type = '{instrument}'), {_ZERO}) "
        f"AS tf_{alias}_value_zar,\n               "
        f"COUNT(*) FILTER (WHERE f.instrument_type = '{instrument}') AS tf_{alias}_count"
    )


def _status_value(status: str) -> str:
    return (
        f"COALESCE(SUM(f.value_zar) FILTER (WHERE f.status = '{status}'), {_ZERO}) "
        f"AS tf_{status}_value_zar,\n               "
        f"COUNT(*) FILTER (WHERE f.status = '{status}') AS tf_{status}_count"
    )


def build_trade_finance_features(connection: duckdb.DuckDBPyConnection) -> None:
    """Create ``tf_features_by_scope``.

    The four instrument statuses are **not** equivalent cash flows, so each is
    carried separately alongside ``tf_live_value_zar`` (``active`` + ``issued``,
    50.15% of the book by value). ``commodity_or_contract_type`` is deliberately
    excluded: it is drawn independently of sector in the source data and carries
    no client-level signal.
    """
    instrument_aliases = {
        "letters_of_credit": "letters_of_credit",
        "guarantees": "guarantees",
        "export_collections": "export_collections",
    }
    instrument_columns = ",\n               ".join(
        _instrument_value(instrument, alias) for instrument, alias in instrument_aliases.items()
    )
    status_columns = ",\n               ".join(_status_value(status) for status in config.TRADE_STATUSES)
    live_list = ", ".join(f"'{status}'" for status in config.LIVE_TRADE_STATUSES)
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE tf_features_by_scope AS
        WITH aggregated AS (
            SELECT w.entity_id,
                   w.scope,
                   w.window_start,
                   w.window_end,
                   w.window_months,
                   COALESCE(SUM(f.value_zar), {_ZERO}) AS tf_total_value_zar,
                   {instrument_columns},
                   {status_columns},
                   COALESCE(SUM(f.value_zar) FILTER (WHERE f.direction = 'import'), {_ZERO})
                       AS tf_import_value_zar,
                   COUNT(*) FILTER (WHERE f.direction = 'import') AS tf_import_count,
                   COALESCE(SUM(f.value_zar) FILTER (WHERE f.direction = 'export'), {_ZERO})
                       AS tf_export_value_zar,
                   COUNT(*) FILTER (WHERE f.direction = 'export') AS tf_export_count,
                   COALESCE(SUM(f.value_zar) FILTER (WHERE f.status IN ({live_list})), {_ZERO})
                       AS tf_live_value_zar,
                   COUNT(*) FILTER (WHERE f.status IN ({live_list})) AS tf_live_count,
                   COUNT(f.instrument_id) AS tf_instrument_count,
                   COUNT(DISTINCT f.counterparty_country) AS tf_active_countries,
                   COUNT(*) FILTER (WHERE f.counterparty_country IS NULL)
                       AS tf_unknown_country_count,
                   COUNT(*) FILTER (WHERE f.memo IS NOT NULL) AS tf_memo_count,
                   AVG(f.tenor_days) AS tf_avg_tenor_days,
                   safe_div(SUM(f.tenor_days * f.value_zar), SUM(f.value_zar))
                       AS tf_weighted_avg_tenor_days,
                   safe_div(
                       SUM(f.tenor_days * f.value_zar) FILTER (WHERE f.status IN ({live_list})),
                       SUM(f.value_zar) FILTER (WHERE f.status IN ({live_list}))
                   ) AS tf_live_weighted_avg_tenor_days
            FROM entity_windows w
            LEFT JOIN tf f
                   ON f.entity_id = w.entity_id
                  AND f.date BETWEEN w.window_start AND w.window_end
            GROUP BY ALL
        ),
        by_country AS (
            SELECT w.entity_id,
                   w.scope,
                   COALESCE(f.counterparty_country, 'UNKNOWN') AS country,
                   SUM(f.value_zar) AS country_value_zar
            FROM entity_windows w
            JOIN tf f
              ON f.entity_id = w.entity_id
             AND f.date BETWEEN w.window_start AND w.window_end
            GROUP BY ALL
        ),
        country_totals AS (
            SELECT entity_id, scope, SUM(country_value_zar) AS total_value_zar
            FROM by_country GROUP BY entity_id, scope
        ),
        country_stats AS (
            SELECT b.entity_id,
                   b.scope,
                   arg_max(b.country, b.country_value_zar) AS tf_top_country,
                   MAX(b.country_value_zar) AS tf_top_country_value_zar,
                   SUM(share_squared(b.country_value_zar, t.total_value_zar)) AS tf_country_hhi
            FROM by_country b
            JOIN country_totals t USING (entity_id, scope)
            GROUP BY b.entity_id, b.scope
        )
        SELECT a.*,
               safe_div(a.tf_total_value_zar, a.window_months) AS tf_monthly_avg_value_zar,
               safe_div(a.tf_total_value_zar, a.tf_instrument_count) AS tf_avg_instrument_zar,
               safe_div(a.tf_import_value_zar, a.tf_export_value_zar) AS tf_import_export_ratio,
               c.tf_top_country,
               COALESCE(c.tf_top_country_value_zar, {_ZERO}) AS tf_top_country_value_zar,
               safe_div(c.tf_top_country_value_zar, a.tf_total_value_zar) AS tf_top_country_share,
               c.tf_country_hhi
        FROM aggregated a
        LEFT JOIN country_stats c USING (entity_id, scope)
        """
    )


def build_corridor_breakdown(connection: duckdb.DuckDBPyConnection) -> None:
    """Create ``client_corridor_breakdown``: the long by-country / by-pair detail.

    ``client_features`` stays strictly one row per entity, so the full
    counterparty-country and currency-pair distributions live here instead of
    becoming 70-odd sparse columns.
    """
    connection.execute(
        f"""
        CREATE OR REPLACE TABLE client_corridor_breakdown AS
        WITH detail AS (
            SELECT w.entity_id, w.scope, 'cross_border' AS pillar,
                   'counterparty_country' AS dimension,
                   COALESCE(x.counterparty_country, 'UNKNOWN') AS dimension_value,
                   SUM(x.value_zar) AS volume_zar,
                   COUNT(*) AS transaction_count
            FROM entity_windows w
            JOIN xb x ON x.entity_id = w.entity_id AND x.date BETWEEN w.window_start AND w.window_end
            GROUP BY ALL
            UNION ALL
            SELECT w.entity_id, w.scope, 'cross_border', 'currency_pair', x.currency_pair,
                   SUM(x.value_zar), COUNT(*)
            FROM entity_windows w
            JOIN xb x ON x.entity_id = w.entity_id AND x.date BETWEEN w.window_start AND w.window_end
            GROUP BY ALL
            UNION ALL
            SELECT w.entity_id, w.scope, 'trade_finance', 'counterparty_country',
                   COALESCE(f.counterparty_country, 'UNKNOWN'),
                   SUM(f.value_zar), COUNT(*)
            FROM entity_windows w
            JOIN tf f ON f.entity_id = w.entity_id AND f.date BETWEEN w.window_start AND w.window_end
            GROUP BY ALL
        )
        SELECT d.*,
               e.entity_name,
               e.sector,
               safe_div(d.volume_zar,
                        SUM(d.volume_zar) OVER (PARTITION BY d.entity_id, d.scope, d.pillar, d.dimension))
                   AS share_of_pillar
        FROM detail d
        JOIN entity_dim e USING (entity_id)
        ORDER BY entity_id, scope, pillar, dimension, volume_zar DESC
        """
    )


#: Columns that key a ``*_features_by_scope`` table rather than measure something.
SCOPE_KEY_COLUMNS = frozenset(
    {"entity_id", "scope", "window_start", "window_end", "window_months", "window_days"}
)


def scope_columns(connection: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    """Return the measure columns of a by-scope feature table, in table order."""
    rows = connection.execute(f"DESCRIBE {table}").fetchall()
    return [name for name, *_ in rows if name not in SCOPE_KEY_COLUMNS]


def scope_projection(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    scope: str,
    columns: list[str] | None = None,
) -> str:
    """Return SQL selecting one scope's measures, suffixed with the scope tag.

    ``txn_total_volume_zar`` in scope ``fiscal_year`` becomes
    ``txn_total_volume_zar_fy``.
    """
    suffix = config.SCOPE_SUFFIX[scope]
    measures = columns if columns is not None else scope_columns(connection, table)
    projected = ",\n               ".join(f'"{name}" AS "{name}_{suffix}"' for name in measures)
    return f"SELECT entity_id,\n               {projected}\n        FROM {table} WHERE scope = '{scope}'"


def build(connection: duckdb.DuckDBPyConnection) -> None:
    """Build every by-scope internal feature table plus the corridor detail."""
    build_entity_windows(connection)
    build_transactional_features(connection)
    build_cross_border_features(connection)
    build_trade_finance_features(connection)
    build_corridor_breakdown(connection)
