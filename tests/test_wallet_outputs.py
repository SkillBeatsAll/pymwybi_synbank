"""The wallet engine's generated outputs, against the real 20-client portfolio."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.syn_wallet import build_wallet
from src.syn_wallet.wallet import assumptions, diagnostics

from .conftest import requires_full_data

EXPECTED_CLIENTS = 20


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


@requires_full_data
def test_one_row_per_client_per_product(wallet) -> None:
    rows, clients, products = wallet.execute(
        "SELECT COUNT(*), COUNT(DISTINCT entity_id), COUNT(DISTINCT product) FROM wallet_estimates"
    ).fetchone()
    assert clients == EXPECTED_CLIENTS
    assert products == len(assumptions.PRODUCTS)
    assert rows == EXPECTED_CLIENTS * len(assumptions.PRODUCTS)


@requires_full_data
def test_no_duplicate_client_product_rows(wallet) -> None:
    duplicates = wallet.execute(
        "SELECT COUNT(*) FROM (SELECT entity_id, product FROM wallet_estimates "
        "GROUP BY 1, 2 HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    assert duplicates == 0


@requires_full_data
def test_all_five_pillars_are_represented_for_every_client(wallet) -> None:
    incomplete = wallet.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT entity_id, COUNT(DISTINCT product) AS n FROM wallet_estimates GROUP BY 1
        ) WHERE n <> {len(assumptions.PRODUCTS)}
        """
    ).fetchone()[0]
    assert incomplete == 0

    observed_products = {
        row[0] for row in wallet.execute("SELECT DISTINCT product FROM wallet_estimates").fetchall()
    }
    assert observed_products == set(assumptions.PRODUCTS)


@requires_full_data
def test_every_client_appears_in_the_ranked_opportunities(wallet) -> None:
    rows, clients = wallet.execute(
        "SELECT COUNT(*), COUNT(DISTINCT entity_id) FROM opportunities"
    ).fetchone()
    assert clients == EXPECTED_CLIENTS
    assert rows == EXPECTED_CLIENTS * len(assumptions.PRODUCTS)


# ---------------------------------------------------------------------------
# Value ranges
# ---------------------------------------------------------------------------


@requires_full_data
def test_no_negative_estimates_gaps_or_observed_values(wallet) -> None:
    violations = wallet.execute(
        """
        SELECT COUNT(*) FROM wallet_estimates
        WHERE estimate_zar < 0 OR gap_zar < 0 OR observed_zar < 0
        """
    ).fetchone()[0]
    assert violations == 0


@requires_full_data
def test_shares_are_valid_percentages(wallet) -> None:
    violations = wallet.execute(
        'SELECT COUNT(*) FROM wallet_estimates WHERE "share" < 0 OR "share" > 1'
    ).fetchone()[0]
    assert violations == 0


@requires_full_data
def test_confidence_and_opportunity_scores_are_between_zero_and_one(wallet) -> None:
    violations = wallet.execute(
        """
        SELECT COUNT(*) FROM wallet_estimates
        WHERE confidence < 0 OR confidence > 1
           OR opportunity_score < 0 OR opportunity_score > 1
           OR confidence IS NULL OR opportunity_score IS NULL
        """
    ).fetchone()[0]
    assert violations == 0


@requires_full_data
def test_confidence_bands_match_their_declared_thresholds(wallet) -> None:
    violations = wallet.execute(
        f"""
        SELECT COUNT(*) FROM wallet_estimates
        WHERE (confidence >= {assumptions.CONFIDENCE_BAND_HIGH} AND confidence_band <> 'HIGH')
           OR (confidence >= {assumptions.CONFIDENCE_BAND_MEDIUM}
               AND confidence < {assumptions.CONFIDENCE_BAND_HIGH} AND confidence_band <> 'MEDIUM')
           OR (confidence < {assumptions.CONFIDENCE_BAND_MEDIUM} AND confidence_band <> 'LOW')
        """
    ).fetchone()[0]
    assert violations == 0


@requires_full_data
def test_gap_reconciles_to_estimate_less_observed(wallet) -> None:
    violations = wallet.execute(
        """
        SELECT COUNT(*) FROM wallet_estimates
        WHERE gap_zar IS NOT NULL AND observed_zar IS NOT NULL
          AND ABS(gap_zar - GREATEST(estimate_zar - observed_zar, 0)) > 1.0
        """
    ).fetchone()[0]
    assert violations == 0


@requires_full_data
def test_share_reconciles_to_observed_over_estimate(wallet) -> None:
    violations = wallet.execute(
        """
        SELECT COUNT(*) FROM wallet_estimates
        WHERE "share" IS NOT NULL
          AND ABS("share" - LEAST(observed_zar / estimate_zar, 1.0)) > 1e-9
        """
    ).fetchone()[0]
    assert violations == 0


# ---------------------------------------------------------------------------
# NULL handling
# ---------------------------------------------------------------------------


@requires_full_data
def test_pillars_without_observed_data_declare_it_rather_than_reporting_zero(wallet) -> None:
    """Syn Bank's datasets contain no loan book and no deal pipeline. Reporting
    a zero share for lending would be a claim; reporting NULL with a reason is a
    statement of what the data does not contain."""
    rows = wallet.execute(
        """
        SELECT product, COUNT(*), COUNT("share"), COUNT(observed_zar), ANY_VALUE(share_basis)
        FROM wallet_estimates WHERE product IN ('lending', 'investment_banking')
        GROUP BY product
        """
    ).fetchall()
    assert len(rows) == 2
    for _, total, shares, observed, basis in rows:
        assert total == EXPECTED_CLIENTS
        assert shares == 0
        assert observed == 0
        assert basis == "no_observed_activity_in_dataset"


@requires_full_data
def test_every_null_share_carries_a_reason(wallet) -> None:
    unexplained = wallet.execute(
        'SELECT COUNT(*) FROM wallet_estimates WHERE "share" IS NULL AND share_basis IS NULL'
    ).fetchone()[0]
    assert unexplained == 0


@requires_full_data
def test_investment_banking_produces_no_rand_amount(wallet) -> None:
    """The pillar the data cannot size. A rand figure here would be invented."""
    rows, estimates, signals = wallet.execute(
        """
        SELECT COUNT(*), COUNT(estimate_zar), COUNT(signal_score)
        FROM wallet_estimates WHERE product = 'investment_banking'
        """
    ).fetchone()
    assert rows == EXPECTED_CLIENTS
    assert estimates == 0
    assert signals == EXPECTED_CLIENTS


@requires_full_data
def test_a_missing_driver_never_becomes_zero(wallet) -> None:
    """An undisclosed driver must produce a NULL component, not a zero one."""
    zeroed = wallet.execute(
        """
        SELECT COUNT(*) FROM wallet_components
        WHERE driver_source = 'unavailable' AND component_zar IS NOT NULL
        """
    ).fetchone()[0]
    assert zeroed == 0


# ---------------------------------------------------------------------------
# Pillar separation and double counting
# ---------------------------------------------------------------------------


@requires_full_data
def test_cash_and_fx_never_claim_the_same_rand(wallet) -> None:
    """SWIFT-channel transactional volume is excluded from the cash numerator
    and not added to the FX numerator, so it is counted in neither."""
    features = wallet.execute(
        """
        SELECT entity_id,
               txn_collections_volume_zar_fy + txn_supplier_payments_volume_zar_fy AS all_channels,
               txn_collections_domestic_volume_zar_fy
                   + txn_supplier_payments_domestic_volume_zar_fy AS domestic,
               xb_total_volume_zar_fy
        FROM client_features
        """
    ).df()
    cash = wallet.execute(
        "SELECT entity_id, observed_zar, overlap_excluded_zar FROM wallet_estimates "
        "WHERE product = 'cash_management'"
    ).df()
    fx = wallet.execute(
        "SELECT entity_id, observed_zar FROM wallet_estimates WHERE product = 'fx_global_markets'"
    ).df()

    merged = features.merge(cash, on="entity_id").merge(fx, on="entity_id", suffixes=("_cash", "_fx"))
    # The cash numerator is exactly the domestic-channel in-scope legs.
    assert (
        (merged["observed_zar_cash"] - merged["domestic"].astype(float)).abs() < 1.0
    ).all()
    # The FX numerator is exactly the cross-border pillar, with nothing added.
    assert (
        (merged["observed_zar_fx"] - merged["xb_total_volume_zar_fy"].astype(float)).abs() < 1.0
    ).all()
    # The excluded overlap is real and positive for every client.
    assert (merged["overlap_excluded_zar"] > 0).all()


@requires_full_data
def test_no_output_sums_the_internal_pillars(wallet) -> None:
    """No row and no column may present transactional plus cross-border as one
    number. The two overlap by an amount the supplied fields cannot resolve."""
    columns = [row[0].lower() for row in wallet.execute("DESCRIBE wallet_estimates").fetchall()]
    banned = ("combined", "all_pillar", "total_flow", "total_banking")
    assert [name for name in columns if any(token in name for token in banned)] == []

    products = wallet.execute("SELECT DISTINCT product FROM portfolio_summary").fetchall()
    assert len(products) == len(assumptions.PRODUCTS)
    # portfolio_summary is per product; there is no all-product total row.
    assert wallet.execute(
        "SELECT COUNT(*) FROM portfolio_summary WHERE product NOT IN "
        + "(" + ", ".join(f"'{product}'" for product in assumptions.PRODUCTS) + ")"
    ).fetchone()[0] == 0


@requires_full_data
def test_no_pricing_or_fee_assumption_appears_anywhere(wallet) -> None:
    for table in ("wallet_estimates", "opportunities", "portfolio_summary", "wallet_components"):
        columns = [row[0].lower() for row in wallet.execute(f"DESCRIBE {table}").fetchall()]
        forbidden = ("fee", "margin", "bps", "basis_point", "revenue_earned", "income_estimate")
        offenders = [name for name in columns if any(token in name for token in forbidden)]
        assert offenders == [], (table, offenders)


# ---------------------------------------------------------------------------
# Sector methodology
# ---------------------------------------------------------------------------


@requires_full_data
def test_non_goods_sectors_receive_only_guarantee_trade_finance(wallet) -> None:
    rows = wallet.execute(
        """
        SELECT c.entity_id, c.component, c.component_zar
        FROM wallet_components c
        JOIN client_features f USING (entity_id)
        WHERE c.product = 'trade_finance' AND f.sector IN ('insurance', 'real_estate')
        """
    ).df()
    goods = rows[rows["component"].isin(["import_documentary", "export_documentary"])]
    assert goods["component_zar"].isna().all()
    guarantees = rows[rows["component"] == "guarantees"]
    assert (guarantees["component_zar"] > 0).all()


@requires_full_data
def test_insurers_do_not_receive_mining_scale_trade_estimates(wallet) -> None:
    """The specific implausibility the sector rules exist to prevent."""
    by_sector = wallet.execute(
        """
        SELECT sector, MAX(estimate_zar) AS largest
        FROM wallet_estimates WHERE product = 'trade_finance' GROUP BY sector
        """
    ).df().set_index("sector")["largest"]
    assert by_sector["insurance"] < by_sector["mining"] / 100
    assert by_sector["real_estate"] < by_sector["mining"] / 100


@requires_full_data
def test_cost_of_sales_is_never_imputed_for_insurance_or_real_estate(wallet) -> None:
    imputed = wallet.execute(
        """
        SELECT COUNT(*) FROM wallet_components c
        JOIN client_features f USING (entity_id)
        WHERE f.sector IN ('insurance', 'real_estate')
          AND c.component IN ('supplier_payments', 'import_settlement', 'import_documentary')
          AND c.driver_source IN ('sector_benchmark', 'portfolio_benchmark')
        """
    ).fetchone()[0]
    assert imputed == 0


@requires_full_data
def test_insurance_cash_estimates_are_flagged_as_a_non_cash_denominator(wallet) -> None:
    flagged = wallet.execute(
        """
        SELECT COUNT(*) FROM wallet_estimates e
        JOIN client_features f USING (entity_id)
        WHERE e.product = 'cash_management' AND f.sector = 'insurance'
          AND e.diagnostic_flags NOT LIKE '%revenue_not_a_cash_measure%'
        """
    ).fetchone()[0]
    assert flagged == 0


# ---------------------------------------------------------------------------
# Formula reproducibility
# ---------------------------------------------------------------------------


@requires_full_data
def test_every_estimate_reproduces_from_its_component_breakdown(wallet) -> None:
    """The published wallet must equal the sum of its published parts, except
    where the observed floor deliberately raised it."""
    mismatches = wallet.execute(
        """
        WITH summed AS (
            SELECT entity_id, product, SUM(component_zar) AS total
            FROM wallet_components WHERE component_zar IS NOT NULL
            GROUP BY entity_id, product
        )
        SELECT COUNT(*)
        FROM wallet_estimates e
        JOIN summed s USING (entity_id, product)
        WHERE e.product <> 'investment_banking'
          AND e.estimate_modelled_zar IS NOT NULL
          AND ABS(e.estimate_modelled_zar - s.total) > 1.0
        """
    ).fetchone()[0]
    assert mismatches == 0


@requires_full_data
def test_floored_estimates_are_the_only_ones_that_exceed_their_components(wallet) -> None:
    unexplained = wallet.execute(
        """
        SELECT COUNT(*) FROM wallet_estimates
        WHERE estimate_modelled_zar IS NOT NULL
          AND estimate_zar > estimate_modelled_zar + 1.0
          AND diagnostic_flags NOT LIKE '%wallet_floored_at_observed%'
        """
    ).fetchone()[0]
    assert unexplained == 0


@requires_full_data
def test_opportunity_score_reproduces_from_its_declared_weights(wallet) -> None:
    weights = assumptions.OPPORTUNITY_WEIGHTS
    mismatches = wallet.execute(
        f"""
        SELECT COUNT(*) FROM wallet_estimates
        WHERE ABS(opportunity_score - LEAST(
              {weights['gap']} * opportunity_gap_scale
            + {weights['confidence']} * confidence
            + {weights['headroom']} * opportunity_headroom, 1.0)) > 1e-9
        """
    ).fetchone()[0]
    assert mismatches == 0


@requires_full_data
def test_ranks_are_a_dense_permutation_of_the_estimates(wallet) -> None:
    overall = [row[0] for row in wallet.execute(
        "SELECT rank_overall FROM wallet_estimates ORDER BY rank_overall"
    ).fetchall()]
    assert overall == list(range(1, EXPECTED_CLIENTS * len(assumptions.PRODUCTS) + 1))

    bad = wallet.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT product, COUNT(*) AS n, MAX(rank_in_product) AS mx,
                   COUNT(DISTINCT rank_in_product) AS distinct_ranks
            FROM wallet_estimates GROUP BY product
        ) WHERE n <> {EXPECTED_CLIENTS} OR mx <> {EXPECTED_CLIENTS}
             OR distinct_ranks <> {EXPECTED_CLIENTS}
        """
    ).fetchone()[0]
    assert bad == 0


@requires_full_data
def test_rebuilding_the_engine_reproduces_the_same_numbers(tmp_path: Path, wallet_run) -> None:
    """The whole point of a transparent model: run it again, get the same answer."""
    second = build_wallet.run(output_dir=tmp_path, overwrite=True)
    assert second["methodology_version"] == wallet_run["methodology_version"]
    for left, right in zip(
        wallet_run["portfolio_summary"], second["portfolio_summary"], strict=True
    ):
        assert left["product"] == right["product"]
        assert left["total_estimate_zar"] == pytest.approx(right["total_estimate_zar"], nan_ok=True)
        assert left["total_gap_zar"] == pytest.approx(right["total_gap_zar"], nan_ok=True)


# ---------------------------------------------------------------------------
# Explanations and diagnostics
# ---------------------------------------------------------------------------


@requires_full_data
def test_every_estimate_carries_an_explanation_built_from_real_values(wallet) -> None:
    missing = wallet.execute(
        "SELECT COUNT(*) FROM wallet_estimates WHERE explanation IS NULL OR LENGTH(explanation) < 80"
    ).fetchone()[0]
    assert missing == 0


@requires_full_data
def test_explanations_never_claim_competitor_ownership(wallet) -> None:
    """A gap is addressable business not observed in Syn Bank's data. It is not
    a claim that a named competitor holds it."""
    claims = wallet.execute(
        """
        SELECT COUNT(*) FROM wallet_estimates
        WHERE lower(explanation) LIKE '%held by a competitor%'
           OR lower(explanation) LIKE '%competitor holds%'
           OR lower(explanation) LIKE '%currently banked by%'
        """
    ).fetchone()[0]
    assert claims == 0


@requires_full_data
def test_diagnostics_flag_every_capped_or_floored_estimate(wallet) -> None:
    floored = wallet.execute(
        "SELECT COUNT(*) FROM wallet_estimates WHERE diagnostic_flags LIKE '%floored%'"
    ).fetchone()[0]
    assert floored > 0, "the floor should bind somewhere in this portfolio"

    unflagged = wallet.execute(
        """
        SELECT COUNT(*) FROM wallet_estimates
        WHERE share_uncapped > 1.0000001 AND diagnostic_flags NOT LIKE '%observed_exceeds%'
        """
    ).fetchone()[0]
    assert unflagged == 0


@requires_full_data
def test_diagnostics_use_the_declared_severity_vocabulary(wallet) -> None:
    unknown = wallet.execute(
        "SELECT COUNT(*) FROM model_diagnostics WHERE severity NOT IN "
        f"('{diagnostics.HIGH}', '{diagnostics.MEDIUM}', '{diagnostics.INFO}')"
    ).fetchone()[0]
    assert unknown == 0

    scopes = {row[0] for row in wallet.execute("SELECT DISTINCT scope FROM model_diagnostics").fetchall()}
    assert scopes <= {"client_product", "product", "sector_product"}


@requires_full_data
def test_the_size_driven_ranking_is_disclosed_not_hidden(wallet) -> None:
    """Cash-management gap ranks track revenue ranks almost perfectly. That is a
    real property of an identity-anchored denominator and it must be published."""
    found = wallet.execute(
        """
        SELECT COUNT(*) FROM model_diagnostics
        WHERE diagnostic = 'product_ranking_tracks_company_size' AND product = 'cash_management'
        """
    ).fetchone()[0]
    assert found == 1


@requires_full_data
def test_low_confidence_products_are_declared(wallet) -> None:
    declared = {
        row[0]
        for row in wallet.execute(
            "SELECT product FROM model_diagnostics "
            "WHERE diagnostic = 'product_model_insufficient_evidence'"
        ).fetchall()
    }
    assert {"fx_global_markets", "trade_finance", "investment_banking"} <= declared


# ---------------------------------------------------------------------------
# Worked examples
# ---------------------------------------------------------------------------


@requires_full_data
def test_worked_examples_cover_three_clients_and_all_five_products(worked_examples) -> None:
    assert len(worked_examples) >= 3
    for example in worked_examples:
        assert {product["product"] for product in example["products"]} == set(assumptions.PRODUCTS)
        assert example["inputs"]["revenue_total_zar"] is not None


@requires_full_data
def test_worked_example_components_sum_to_the_published_estimate(worked_examples) -> None:
    for example in worked_examples:
        for product in example["products"]:
            if product["product"] == assumptions.IB or product["estimate_zar"] is None:
                continue
            total = sum(
                component["component_zar"]
                for component in product["components"]
                if component["component_zar"] is not None and pd.notna(component["component_zar"])
            )
            assert product["estimate_zar"] >= total - 1.0, (example["entity_id"], product["product"])
