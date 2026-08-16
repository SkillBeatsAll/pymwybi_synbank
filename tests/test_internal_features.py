"""Internal flow features: aggregate correctness, scoping, and known signals."""

from __future__ import annotations

from src.syn_wallet import config

from .conftest import requires_full_data

#: Exact totals of the cleaned Parquet, re-derived from the files themselves.
#: They are asserted here so an aggregation change that silently drops or
#: double-counts rows fails immediately.
PILLAR_TOTALS = {
    "transactional": ("403838506594.2760679160315000", 2_791_803),
    "cross_border": ("133235605738.9100000000000000", 240_191),
    "trade_finance": ("38305641003.3500000000000000", 20_215),
}


@requires_full_data
def test_full_window_aggregates_reconcile_exactly(features) -> None:
    total, count = features.execute(
        "SELECT CAST(SUM(txn_total_volume_zar_36m) AS VARCHAR), SUM(txn_transaction_count_36m) "
        "FROM client_features"
    ).fetchone()
    assert (total, int(count)) == PILLAR_TOTALS["transactional"]

    total, count = features.execute(
        "SELECT CAST(SUM(xb_total_volume_zar_36m) AS VARCHAR), SUM(xb_transaction_count_36m) "
        "FROM client_features"
    ).fetchone()
    assert (total, int(count)) == PILLAR_TOTALS["cross_border"]

    total, count = features.execute(
        "SELECT CAST(SUM(tf_total_value_zar_36m) AS VARCHAR), SUM(tf_instrument_count_36m) "
        "FROM client_features"
    ).fetchone()
    assert (total, int(count)) == PILLAR_TOTALS["trade_finance"]


@requires_full_data
def test_live_trade_book_is_active_plus_issued(features) -> None:
    """active + issued is 50.15% of the book by value; the four statuses are not
    equivalent cash flows and must stay separable."""
    live, total, count = features.execute(
        "SELECT CAST(SUM(tf_live_value_zar_36m) AS VARCHAR), "
        "CAST(SUM(tf_total_value_zar_36m) AS VARCHAR), SUM(tf_live_count_36m) FROM client_features"
    ).fetchone()
    assert live == "19211548308.9300000000000000"
    assert int(count) == 10_022
    assert 0.5014 < float(live) / float(total) < 0.5016


@requires_full_data
def test_decompositions_sum_to_their_totals(features) -> None:
    """Leg types, directions and statuses must partition their pillar exactly."""
    mismatches = features.execute(
        """
        SELECT COUNT(*) FROM client_features
        WHERE txn_collections_volume_zar_36m + txn_supplier_payments_volume_zar_36m
              + txn_intercompany_sweeps_volume_zar_36m + txn_payroll_volume_zar_36m
              + txn_tax_volume_zar_36m <> txn_total_volume_zar_36m
           OR txn_inbound_volume_zar_36m + txn_outbound_volume_zar_36m <> txn_total_volume_zar_36m
           OR txn_swift_channel_volume_zar_36m + txn_domestic_volume_zar_36m
              <> txn_total_volume_zar_36m
           OR xb_trade_corridor_volume_zar_36m + xb_intercompany_corridor_volume_zar_36m
              + xb_other_corridor_volume_zar_36m <> xb_total_volume_zar_36m
           OR tf_letters_of_credit_value_zar_36m + tf_guarantees_value_zar_36m
              + tf_export_collections_value_zar_36m <> tf_total_value_zar_36m
        """
    ).fetchone()[0]
    assert mismatches == 0


@requires_full_data
def test_currency_pair_volumes_partition_the_cross_border_pillar(features) -> None:
    mismatches = features.execute(
        """
        SELECT COUNT(*) FROM client_features
        WHERE xb_pair_usd_volume_zar_36m + xb_pair_eur_volume_zar_36m + xb_pair_gbp_volume_zar_36m
              + xb_pair_aed_volume_zar_36m + xb_pair_cny_volume_zar_36m <> xb_total_volume_zar_36m
        """
    ).fetchone()[0]
    assert mismatches == 0


@requires_full_data
def test_every_scope_holds_one_row_per_client(built) -> None:
    for table in ("txn_features_by_scope", "xb_features_by_scope", "tf_features_by_scope"):
        counts = built.execute(
            f"SELECT scope, COUNT(*), COUNT(DISTINCT entity_id) FROM {table} GROUP BY scope"
        ).fetchall()
        assert len(counts) == len(config.SCOPE_SUFFIX)
        for _, rows, distinct in counts:
            assert rows == distinct == config.EXPECTED_ENTITY_COUNT


@requires_full_data
def test_fiscal_year_scope_is_a_strict_subset_of_the_full_window(features) -> None:
    violations = features.execute(
        """
        SELECT COUNT(*) FROM client_features
        WHERE txn_total_volume_zar_fy > txn_total_volume_zar_36m
           OR xb_total_volume_zar_fy  > xb_total_volume_zar_36m
           OR tf_total_value_zar_fy   > tf_total_value_zar_36m
           OR txn_active_days_fy > txn_active_days_36m
        """
    ).fetchone()[0]
    assert violations == 0


@requires_full_data
def test_trailing_windows_partition_two_of_the_three_years(features) -> None:
    """The recent and prior trailing years are disjoint and together cover
    2024-07-01 to 2026-06-30, so their sum cannot exceed the 36-month total."""
    violations = features.execute(
        """
        SELECT COUNT(*) FROM client_features
        WHERE txn_total_volume_zar_r12m + txn_total_volume_zar_p12m > txn_total_volume_zar_36m
           OR txn_transaction_count_r12m + txn_transaction_count_p12m > txn_transaction_count_36m
        """
    ).fetchone()[0]
    assert violations == 0


@requires_full_data
def test_payroll_engagement_signal_survives_aggregation(features) -> None:
    """Payroll row counts are the sharpest engagement signal in the dataset.
    Pinning the extremes catches a leg-type filter that quietly stops matching."""
    observed = dict(
        features.execute(
            "SELECT entity_id, txn_payroll_count_36m FROM client_features "
            "WHERE entity_id IN ('E06', 'E20', 'E13', 'E17', 'E12', 'E07', 'E11', 'E08', 'E10')"
        ).fetchall()
    )
    assert observed == {
        "E06": 1, "E20": 2, "E13": 4, "E17": 11, "E12": 13, "E07": 19,
        "E11": 3630, "E08": 2375, "E10": 2140,
    }


@requires_full_data
def test_competitor_lending_memos_are_counted_not_discarded(features) -> None:
    """memo is populated on 0.13% of rows; every populated one is evidence of
    lending Syn Bank is not the lender on."""
    txn, xb, tf = features.execute(
        "SELECT SUM(txn_memo_count_36m), SUM(xb_memo_count_36m), SUM(tf_memo_count_36m) "
        "FROM client_features"
    ).fetchone()
    assert (int(txn), int(xb), int(tf)) == (3_645, 445, 94)

    zero_memo_clients = features.execute(
        "SELECT entity_id FROM client_features WHERE txn_memo_count_36m = 0 ORDER BY entity_id"
    ).fetchall()
    assert [row[0] for row in zero_memo_clients] == ["E01", "E03", "E08", "E11"]


@requires_full_data
def test_concentration_measures_are_well_formed(features) -> None:
    violations = features.execute(
        """
        SELECT COUNT(*) FROM client_features
        WHERE xb_country_hhi_36m NOT BETWEEN 0 AND 1
           OR tf_country_hhi_36m NOT BETWEEN 0 AND 1
           OR xb_top_country_share_36m NOT BETWEEN 0 AND 1
           OR tf_top_country_share_36m NOT BETWEEN 0 AND 1
           OR xb_top_country_36m IS NULL
           OR tf_top_country_36m IS NULL
        """
    ).fetchone()[0]
    assert violations == 0


@requires_full_data
def test_weighted_tenor_differs_from_the_simple_mean(features) -> None:
    """A value-weighted tenor that always equals the simple mean means the
    weighting was dropped."""
    identical = features.execute(
        """
        SELECT COUNT(*) FROM client_features
        WHERE ABS(tf_weighted_avg_tenor_days_36m - tf_avg_tenor_days_36m) < 1e-9
        """
    ).fetchone()[0]
    assert identical == 0

    out_of_range = features.execute(
        """
        SELECT COUNT(*) FROM client_features
        WHERE tf_weighted_avg_tenor_days_36m NOT BETWEEN 30 AND 365
           OR tf_avg_tenor_days_36m NOT BETWEEN 30 AND 365
        """
    ).fetchone()[0]
    assert out_of_range == 0


@requires_full_data
def test_corridor_breakdown_shares_sum_to_one(features) -> None:
    violations = features.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT entity_id, scope, pillar, dimension, SUM(share_of_pillar) AS total
            FROM client_corridor_breakdown GROUP BY ALL
        ) WHERE ABS(total - 1.0) > 1e-9
        """
    ).fetchone()[0]
    assert violations == 0


@requires_full_data
def test_corridor_breakdown_reconciles_to_the_feature_totals(features) -> None:
    mismatches = features.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT b.entity_id, SUM(b.volume_zar) AS breakdown_total, ANY_VALUE(f.xb_total_volume_zar_36m) AS feature_total
            FROM client_corridor_breakdown b
            JOIN client_features f USING (entity_id)
            WHERE b.scope = 'full_window' AND b.pillar = 'cross_border'
              AND b.dimension = 'counterparty_country'
            GROUP BY b.entity_id
        ) WHERE breakdown_total <> feature_total
        """
    ).fetchone()[0]
    assert mismatches == 0
