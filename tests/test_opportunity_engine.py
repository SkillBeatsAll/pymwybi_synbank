"""The analytical contract: schema, scoring, ranking, NULLs and the no-sum rule.

These tests are the interface between this repository and whatever gets built on
top of it. A change that breaks one of them is a change that breaks a dashboard
or a generated narrative, and it should be loud.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.syn_wallet.wallet import assumptions, contract, engine, opportunity

from .wallet_fixtures import BREAKING_SCENARIOS, synthetic_features


@pytest.fixture(scope="module")
def model() -> engine.WalletModel:
    return engine.run(synthetic_features(count=8))


# ---------------------------------------------------------------------------
# Product hierarchy and usability classification
# ---------------------------------------------------------------------------


def test_the_three_wallet_pillars_and_two_signal_pillars_are_declared() -> None:
    assert assumptions.WALLET_PILLARS == (
        assumptions.CASH,
        assumptions.FX,
        assumptions.TRADE,
    )
    assert assumptions.SIGNAL_PILLARS == (assumptions.LENDING, assumptions.IB)
    assert set(assumptions.WALLET_PILLARS) | set(assumptions.SIGNAL_PILLARS) == set(
        assumptions.PRODUCTS
    )
    for product in assumptions.WALLET_PILLARS:
        assert assumptions.PILLAR_ROLE[product] == assumptions.SHARE_OF_WALLET
    for product in assumptions.SIGNAL_PILLARS:
        assert assumptions.PILLAR_ROLE[product] == assumptions.OPPORTUNITY_SIGNAL


def test_no_share_is_ever_computed_for_a_signal_pillar(model: engine.WalletModel) -> None:
    signals = model.estimates[
        model.estimates["product"].isin(assumptions.SIGNAL_PILLARS)
    ]
    assert signals["share"].isna().all()
    assert signals["share_uncapped"].isna().all()


def test_products_classify_to_the_expected_usability_class(model: engine.WalletModel) -> None:
    classes = dict(
        zip(
            model.product_classification["product"],
            model.product_classification["product_class"],
        )
    )
    assert classes == {
        assumptions.CASH: assumptions.CORE,
        assumptions.FX: assumptions.CORE,
        assumptions.TRADE: assumptions.CORE,
        assumptions.LENDING: assumptions.SUPPORTING,
        assumptions.IB: assumptions.SIGNAL_ONLY,
    }


def test_classification_is_measured_rather_than_hardcoded() -> None:
    """Remove the observed numerator and a CORE product must demote itself."""
    features = synthetic_features(count=8)
    for column in (
        "xb_total_volume_zar_fy",
        "xb_inbound_volume_zar_fy",
        "xb_outbound_volume_zar_fy",
    ):
        features[column] = np.nan
    classes = opportunity.classify_products(engine.run(features).estimates).set_index(
        "product"
    )["product_class"]
    assert classes[assumptions.FX] == assumptions.SUPPORTING
    assert classes[assumptions.CASH] == assumptions.CORE


def test_every_classification_row_explains_itself(model: engine.WalletModel) -> None:
    for row in model.product_classification.itertuples():
        assert len(row.classification_reason) > 60
        assert row.product_class in {
            assumptions.CORE,
            assumptions.SUPPORTING,
            assumptions.SIGNAL_ONLY,
        }


# ---------------------------------------------------------------------------
# Cash terminology
# ---------------------------------------------------------------------------


def test_addressable_cash_flow_is_revenue_plus_cost_of_sales() -> None:
    features = synthetic_features(count=8)
    model = engine.run(features)
    cash = model.estimates[model.estimates["product"] == assumptions.CASH].set_index(
        "entity_id"
    )
    expected = (
        pd.to_numeric(features["revenue_total_zar"])
        + pd.to_numeric(features["cost_of_sales_zar"])
    )
    expected.index = features["entity_id"]
    pd.testing.assert_series_equal(
        cash["estimate_modelled_zar"], expected, check_names=False
    )


def test_the_cash_fee_wallet_is_null_for_every_client(model: engine.WalletModel) -> None:
    """The flow figure is the client's turnover; the fee wallet is not estimable."""
    assert model.estimates["cash_management_wallet_zar"].isna().all()
    assert model.opportunity_engine["cash_management_wallet_zar"].isna().all()
    assert model.client_profiles["cash_management_wallet_zar"].isna().all()


def test_addressable_cash_flow_is_the_published_cash_estimate(
    model: engine.WalletModel,
) -> None:
    """The intensity denominator must be the same number the cash pillar published."""
    cash = model.estimates[model.estimates["product"] == assumptions.CASH]
    scale = dict(zip(cash["entity_id"], cash["estimate_zar"]))
    for row in model.estimates.itertuples():
        assert row.addressable_cash_flow_zar == pytest.approx(scale[row.entity_id])


def test_the_cash_estimate_basis_names_the_flow_not_a_market(
    model: engine.WalletModel,
) -> None:
    cash = model.estimates[model.estimates["product"] == assumptions.CASH]
    assert (cash["estimate_basis"] == assumptions.ADDRESSABLE_CASH_FLOW).all()
    assert (cash["estimate_kind"] == assumptions.ADDRESSABLE_CASH_FLOW).all()


# ---------------------------------------------------------------------------
# Reproducibility of both scores
# ---------------------------------------------------------------------------


def test_commercial_score_reproduces_from_its_three_published_factors(
    model: engine.WalletModel,
) -> None:
    weights = assumptions.OPPORTUNITY_WEIGHTS
    expected = (
        weights["gap"] * model.estimates["opportunity_gap_scale"]
        + weights["confidence"] * model.estimates["confidence"]
        + weights["headroom"] * model.estimates["opportunity_headroom"]
    ).clip(0.0, 1.0)
    pd.testing.assert_series_equal(
        model.estimates["commercial_opportunity_score"], expected, check_names=False
    )


def test_the_legacy_opportunity_score_is_exactly_the_commercial_score(
    model: engine.WalletModel,
) -> None:
    pd.testing.assert_series_equal(
        model.estimates["opportunity_score"],
        model.estimates["commercial_opportunity_score"],
        check_names=False,
    )


def test_intensity_reproduces_as_gap_over_addressable_cash_flow(
    model: engine.WalletModel,
) -> None:
    """One ratio, no weights. Reproduced by hand from two published columns."""
    gap = pd.to_numeric(model.estimates["gap_zar"], errors="coerce")
    scale = pd.to_numeric(model.estimates["addressable_cash_flow_zar"], errors="coerce")
    expected = (gap / scale.where(scale > 0)).replace([np.inf, -np.inf], np.nan)
    pd.testing.assert_series_equal(
        model.estimates["opportunity_intensity"], expected, check_names=False
    )


def test_intensity_is_null_wherever_it_cannot_be_computed(
    model: engine.WalletModel,
) -> None:
    ib = model.estimates[model.estimates["product"] == assumptions.IB]
    assert ib["opportunity_intensity"].isna().all()
    assert ib["intensity_rank"].isna().all()


def test_intensity_is_null_rather_than_zero_when_the_client_has_no_scale() -> None:
    """No disclosed revenue means no scale to be under-penetrated relative to."""
    features = synthetic_features(BREAKING_SCENARIOS["null_revenue"], count=8)
    model = engine.run(features)
    broken = model.estimates[model.estimates["entity_id"] == "T01"]
    assert broken["addressable_cash_flow_zar"].isna().all()
    assert broken["opportunity_intensity"].isna().all()
    assert broken["intensity_rank"].isna().all()


def test_a_zero_revenue_client_still_gets_scale_from_its_floored_cash_flow() -> None:
    """Zero disclosed revenue is not zero scale when money is visibly moving.

    The observed floor raises addressable cash flow to the activity already
    flowing through the bank, so the denominator is positive and the intensity is
    computable. Reporting NULL here would hide a client whose disclosure is wrong
    rather than absent.
    """
    features = synthetic_features(BREAKING_SCENARIOS["zero_revenue"], count=8)
    model = engine.run(features)
    broken = model.estimates[model.estimates["entity_id"] == "T01"]
    scale = broken["addressable_cash_flow_zar"].dropna().unique()
    assert len(scale) == 1 and scale[0] > 0
    assert broken.loc[
        broken["product"] == assumptions.CASH, "opportunity_intensity"
    ].notna().all()


def test_intensity_ranks_a_small_client_with_a_proportionally_large_gap_highly() -> None:
    """The whole reason intensity exists alongside the commercial score."""
    features = synthetic_features(count=8)
    model = engine.run(features)
    lending = model.estimates[model.estimates["product"] == assumptions.LENDING]
    by_intensity = lending.sort_values("opportunity_intensity", ascending=False)
    by_gap = lending.sort_values("gap_zar", ascending=False)
    assert set(by_intensity["entity_id"]) == set(by_gap["entity_id"])
    # Intensity is scale-free: uniformly scaled clients tie on it while their
    # rand gaps differ by a factor of nearly four.
    assert lending["opportunity_intensity"].std() == pytest.approx(0.0, abs=1e-9)
    assert lending["gap_zar"].std() > 0


# ---------------------------------------------------------------------------
# Ranking determinism
# ---------------------------------------------------------------------------


RANK_COLUMNS = (
    "commercial_rank",
    "commercial_rank_in_product",
    "intensity_rank",
    "intensity_rank_in_product",
)


def test_every_ranking_is_stable_under_row_reordering() -> None:
    features = synthetic_features(count=8)
    forward = engine.run(features).estimates.set_index(["entity_id", "product"])
    backward = (
        engine.run(features.iloc[::-1].reset_index(drop=True))
        .estimates.set_index(["entity_id", "product"])
    )
    for column in RANK_COLUMNS:
        pd.testing.assert_series_equal(
            forward[column].sort_index(), backward[column].sort_index()
        )


def test_two_runs_produce_byte_identical_rankings() -> None:
    features = synthetic_features(count=8)
    first = engine.run(features)
    second = engine.run(features)
    pd.testing.assert_frame_equal(first.estimates, second.estimates)
    pd.testing.assert_frame_equal(first.opportunity_engine, second.opportunity_engine)
    pd.testing.assert_frame_equal(first.client_profiles, second.client_profiles)


def test_commercial_ranks_are_a_dense_permutation(model: engine.WalletModel) -> None:
    ranks = sorted(int(value) for value in model.estimates["commercial_rank"])
    assert ranks == list(range(1, len(model.estimates) + 1))
    for _, group in model.estimates.groupby("product"):
        in_product = sorted(int(value) for value in group["commercial_rank_in_product"])
        assert in_product == list(range(1, len(group) + 1))


def test_intensity_ranks_are_dense_over_the_rows_that_have_one(
    model: engine.WalletModel,
) -> None:
    scored = model.estimates[model.estimates["opportunity_intensity"].notna()]
    ranks = sorted(int(value) for value in scored["intensity_rank"])
    assert ranks == list(range(1, len(scored) + 1))


def test_an_unrankable_row_gets_null_not_last_place(model: engine.WalletModel) -> None:
    """Investment banking has no intensity. It must not be ranked worst."""
    ib = model.estimates[model.estimates["product"] == assumptions.IB]
    assert ib["intensity_rank"].isna().all()
    assert model.estimates["intensity_rank"].max() < len(model.estimates)


# ---------------------------------------------------------------------------
# The published schema
# ---------------------------------------------------------------------------


def test_opportunity_engine_has_every_contracted_column(model: engine.WalletModel) -> None:
    for column in contract.OPPORTUNITY_ENGINE_COLUMNS:
        assert column in model.opportunity_engine.columns, column


def test_opportunity_engine_is_one_row_per_client_per_product(
    model: engine.WalletModel,
) -> None:
    table = model.opportunity_engine
    assert len(table) == 8 * len(assumptions.PRODUCTS)
    assert not table.duplicated(subset=["entity_id", "product"]).any()


def test_the_contract_preserves_null_for_products_without_a_rand_denominator(
    model: engine.WalletModel,
) -> None:
    for product in contract.NO_RAND_DENOMINATOR:
        rows = model.opportunity_engine[model.opportunity_engine["product"] == product]
        assert rows["addressable_zar"].isna().all()
        assert rows["opportunity_zar"].isna().all()
        assert rows["observed_zar"].isna().all()
        # ...but the signal that *is* supported is still published.
        assert rows["signal_score"].notna().all()


def test_no_rand_column_in_the_contract_is_ever_a_filled_zero() -> None:
    """A NULL denominator must survive as NULL all the way to the contract."""
    features = synthetic_features(
        BREAKING_SCENARIOS["no_external_financials_at_all"], count=8
    )
    table = engine.run(features).opportunity_engine
    broken = table[(table["entity_id"] == "T01") & (table["product"] == assumptions.CASH)]
    assert broken["addressable_zar"].isna().all()
    assert broken["opportunity_zar"].isna().all()
    assert broken["share"].isna().all()


def test_diagnostic_counts_agree_with_the_diagnostics_table(
    model: engine.WalletModel,
) -> None:
    findings = model.diagnostics
    client_scope = findings[findings["entity_id"].notna() & findings["product"].notna()]
    expected = client_scope.groupby(["entity_id", "product"]).size()
    actual = model.opportunity_engine.set_index(["entity_id", "product"])[
        "diagnostic_count"
    ]
    for key, count in expected.items():
        assert actual.loc[key] == count
    assert actual.sum() == len(client_scope)


def test_high_severity_flag_matches_the_findings(model: engine.WalletModel) -> None:
    high = model.diagnostics[
        (model.diagnostics["severity"] == "HIGH")
        & model.diagnostics["entity_id"].notna()
    ]
    expected = set(zip(high["entity_id"], high["product"]))
    flagged = model.opportunity_engine[model.opportunity_engine["high_severity_diagnostic"]]
    assert set(zip(flagged["entity_id"], flagged["product"])) == expected


def test_every_contract_row_carries_an_explanation(model: engine.WalletModel) -> None:
    explanations = model.opportunity_engine["explanation"]
    assert explanations.notna().all()
    assert (explanations.str.len() > 80).all()


# ---------------------------------------------------------------------------
# Client profiles, and the prohibition on summing pillars
# ---------------------------------------------------------------------------


REQUIRED_PROFILE_COLUMNS = (
    "entity_id",
    "entity_name",
    "sector",
    "addressable_cash_flow_zar",
    "cash_share_of_wallet",
    "fx_addressable_zar",
    "fx_share_of_wallet",
    "trade_addressable_zar",
    "trade_share_of_wallet",
    "lending_opportunity_zar",
    "ib_signal_score",
    "commercial_opportunity_score",
    "opportunity_intensity",
    "top_opportunity_confidence",
    "major_diagnostic_count",
    "major_diagnostics",
    "top_opportunity_product",
    "recommended_next_product",
)


def test_client_profile_carries_every_required_field(model: engine.WalletModel) -> None:
    for column in REQUIRED_PROFILE_COLUMNS:
        assert column in model.client_profiles.columns, column
    assert len(model.client_profiles) == 8
    assert model.client_profiles["entity_id"].is_unique


def test_no_profile_column_is_the_sum_of_the_pillars(model: engine.WalletModel) -> None:
    contract.assert_no_pillar_summation(model.client_profiles)


def test_a_deliberate_cross_pillar_total_is_caught(model: engine.WalletModel) -> None:
    """The guard has to actually fire, or it is decoration."""
    sabotaged = model.client_profiles.copy()
    sabotaged["total_wallet_zar"] = sabotaged[
        list(contract.PILLAR_VALUE_COLUMNS)
    ].sum(axis=1, min_count=1)
    with pytest.raises(AssertionError, match="never be totalled"):
        contract.assert_no_pillar_summation(sabotaged)


def test_the_recommended_next_product_is_never_the_top_opportunity(
    model: engine.WalletModel,
) -> None:
    for row in model.client_profiles.itertuples():
        if row.recommended_next_product is not None:
            assert row.recommended_next_product != row.top_opportunity_product
            assert row.recommended_next_product in contract.RECOMMENDABLE
        assert row.recommended_next_reason


def test_profile_pillar_values_match_the_estimate_table(model: engine.WalletModel) -> None:
    estimates = model.estimates.set_index(["entity_id", "product"])
    for row in model.client_profiles.itertuples():
        for product, column in (
            (assumptions.CASH, "addressable_cash_flow_zar"),
            (assumptions.FX, "fx_addressable_zar"),
            (assumptions.TRADE, "trade_addressable_zar"),
            (assumptions.LENDING, "lending_opportunity_zar"),
        ):
            published = estimates.at[(row.entity_id, product), "estimate_zar"]
            assert getattr(row, column) == pytest.approx(published, nan_ok=True)


# ---------------------------------------------------------------------------
# Product confidence summary
# ---------------------------------------------------------------------------


def test_product_confidence_percentages_are_coherent(model: engine.WalletModel) -> None:
    for row in model.product_confidence.itertuples():
        assert row.pct_high + row.pct_medium + row.pct_low == pytest.approx(1.0)
        assert row.clients_high + row.clients_medium + row.clients_low == row.clients
        assert 0.0 <= row.mean_confidence <= 1.0
        assert 0.0 <= row.median_confidence <= 1.0
        assert 0.0 <= row.pct_major_diagnostic <= 1.0
        assert row.min_confidence <= row.mean_confidence <= row.max_confidence


def test_product_confidence_covers_every_product(model: engine.WalletModel) -> None:
    assert list(model.product_confidence["product"]) == list(assumptions.PRODUCTS)
    assert model.product_confidence["clients"].eq(8).all()
