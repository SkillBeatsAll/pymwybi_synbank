"""The wallet engine's declarations, arithmetic, and its behaviour under abuse.

Split from :mod:`tests.test_wallet_outputs` deliberately: everything here runs
without the full dataset, on synthetic clients built to break things.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.syn_wallet.wallet import assumptions, common, confidence, engine, opportunity

from .wallet_fixtures import BREAKING_SCENARIOS, synthetic_features

# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


def test_every_assumption_declares_a_basis_and_a_rationale() -> None:
    valid = {
        assumptions.ACCOUNTING_IDENTITY,
        assumptions.STRUCTURAL,
        assumptions.PORTFOLIO_BENCHMARK,
        assumptions.JUDGEMENT,
    }
    for assumption in assumptions.STATIC_ASSUMPTIONS:
        assert assumption.basis in valid, assumption.name
        assert len(assumption.rationale) > 40, assumption.name
        assert assumption.product in {*assumptions.PRODUCTS, "all"}, assumption.name


def test_assumption_names_are_unique() -> None:
    names = [assumption.name for assumption in assumptions.STATIC_ASSUMPTIONS]
    assert len(names) == len(set(names))


def test_judgement_coefficients_are_few_and_named() -> None:
    """The engine's credibility rests on how little of it is invented. If this
    count grows, the model report has to explain why."""
    judgement = [
        assumption.name
        for assumption in assumptions.STATIC_ASSUMPTIONS
        if assumption.basis == assumptions.JUDGEMENT and assumption.value is not None
    ]
    assert set(judgement) == {
        "capex_debt_funded_share",
        "ib_near_term_maturity_threshold",
        "ib_capex_intensity_threshold",
        "ib_leverage_threshold",
        "ib_cost_of_debt_threshold",
        "share_cap",
        "benchmark_percentile",
        "opportunity_weight_gap",
        "opportunity_weight_confidence",
        "opportunity_weight_headroom",
        "confidence_band_high",
        "confidence_band_medium",
    }


def test_opportunity_and_confidence_weights_sum_to_one() -> None:
    assert sum(assumptions.OPPORTUNITY_WEIGHTS.values()) == pytest.approx(1.0)
    assert sum(confidence.WEIGHTS.values()) == pytest.approx(1.0)


def test_every_product_sector_pair_has_an_applicability_rule() -> None:
    rows = assumptions.sector_rule_registry()
    assert len(rows) == len(assumptions.PRODUCTS) * len(assumptions.SECTORS)
    for row in rows:
        assert 0.0 < row["applicability"] <= 1.0
        assert row["note"]


def test_trade_finance_suppresses_goods_submodels_for_non_goods_sectors() -> None:
    """The sector rule that stops an insurer being scored for import LCs."""
    for sector in ("insurance", "real_estate"):
        rule = assumptions.sector_rule(assumptions.TRADE, sector)
        assert rule.suppress_components == ("import_documentary", "export_documentary")
        assert rule.applicability < 0.5
    for sector in ("mining", "consumer", "industrials_pharma"):
        rule = assumptions.sector_rule(assumptions.TRADE, sector)
        assert rule.suppress_components == ()


def test_cost_of_sales_is_never_imputed_for_non_comparable_sectors() -> None:
    assert "insurance" not in assumptions.COGS_COMPARABLE_SECTORS
    assert "real_estate" not in assumptions.COGS_COMPARABLE_SECTORS


# ---------------------------------------------------------------------------
# Share and gap guards
# ---------------------------------------------------------------------------


def _series(*values: float) -> pd.Series:
    return pd.Series(list(values), dtype="float64")


def test_share_is_null_not_infinite_on_a_zero_denominator() -> None:
    result = common.share_and_gap(_series(10.0, 10.0, 10.0), _series(0.0, np.nan, -5.0))
    assert result.share.isna().all()
    assert result.gap.isna().all()
    assert list(result.basis) == [
        common.SHARE_NON_POSITIVE_DENOMINATOR,
        common.SHARE_NO_DENOMINATOR,
        common.SHARE_NON_POSITIVE_DENOMINATOR,
    ]


def test_share_is_capped_and_the_uncapped_value_is_retained() -> None:
    result = common.share_and_gap(_series(300.0), _series(100.0))
    assert result.share.iloc[0] == 1.0
    assert result.share_uncapped.iloc[0] == 3.0
    assert result.basis.iloc[0] == common.SHARE_CAPPED
    assert result.gap.iloc[0] == 0.0


def test_gap_is_never_negative() -> None:
    result = common.share_and_gap(_series(500.0, 10.0), _series(100.0, 100.0))
    assert (result.gap.dropna() >= 0).all()


def test_pillar_without_observed_data_reports_no_share() -> None:
    result = common.share_and_gap(
        _series(np.nan), _series(250.0), observed_available=False
    )
    assert result.share.isna().all()
    assert result.basis.iloc[0] == common.SHARE_NO_OBSERVED
    assert result.gap.iloc[0] == 250.0
    assert result.gap_basis.iloc[0] == "full_estimate_no_observed_activity_in_dataset"


def test_observed_floor_lifts_the_wallet_and_reports_the_modelled_value() -> None:
    published, floored = common.apply_observed_floor(_series(100.0, 400.0), _series(300.0, 200.0))
    assert list(published) == [300.0, 400.0]
    assert list(floored) == [True, False]


def test_observed_floor_leaves_null_estimates_alone() -> None:
    published, floored = common.apply_observed_floor(_series(np.nan), _series(300.0))
    assert published.isna().all()
    assert not floored.any()


# ---------------------------------------------------------------------------
# Confidence engine
# ---------------------------------------------------------------------------


def test_confidence_is_bounded_and_banded() -> None:
    ones = pd.Series([1.0, 0.5, 0.0])
    result = confidence.score(ones, ones, ones, ones, ones)
    assert ((result.score >= 0) & (result.score <= 1)).all()
    assert list(result.band) == ["HIGH", "LOW", "LOW"]


def test_directness_caps_confidence_rather_than_being_outvoted() -> None:
    """Perfect inputs with a judgement-only method must not read as confident.

    This is the regression test for the calibration bug that scored all twenty
    investment-banking estimates HIGH.
    """
    perfect = pd.Series([1.0])
    judgement = pd.Series([confidence.BASIS_DIRECTNESS[assumptions.JUDGEMENT]])
    result = confidence.score(perfect, judgement, perfect, perfect, perfect)
    assert result.score.iloc[0] == pytest.approx(0.35)
    assert result.band.iloc[0] == "LOW"


def test_a_missing_component_reduces_directness() -> None:
    full = confidence.effective_directness(
        pd.Series([1.0]), pd.Series([2.0]), pd.Series([2.0])
    )
    half = confidence.effective_directness(
        pd.Series([1.0]), pd.Series([1.0]), pd.Series([2.0])
    )
    assert full.iloc[0] == pytest.approx(1.0)
    assert half.iloc[0] == pytest.approx(0.5)


def test_flooring_the_wallet_halves_directness() -> None:
    floored = confidence.effective_directness(
        pd.Series([1.0]), pd.Series([2.0]), pd.Series([2.0]), pd.Series([True])
    )
    assert floored.iloc[0] == pytest.approx(1.0 - confidence.FLOOR_PENALTY)


def test_observation_support_is_log_scaled_and_bounded() -> None:
    support = confidence.observation_support(pd.Series([0.0, 100.0, 10_000.0]))
    assert support.iloc[0] == 0.0
    assert support.iloc[2] == pytest.approx(1.0)
    assert 0.4 < support.iloc[1] < 0.6  # log scale, not linear


def test_observation_support_handles_an_all_zero_portfolio() -> None:
    assert (confidence.observation_support(pd.Series([0.0, 0.0])) == 0.0).all()


# ---------------------------------------------------------------------------
# Breaking the model on purpose
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", sorted(BREAKING_SCENARIOS))
def test_engine_survives_every_breaking_scenario(scenario: str) -> None:
    """The engine must degrade to NULL, never to a crash or a fabricated number."""
    features = synthetic_features(BREAKING_SCENARIOS[scenario])
    model = engine.run(features)

    assert len(model.estimates) == len(features) * len(assumptions.PRODUCTS)
    numeric = model.estimates[["estimate_zar", "observed_zar", "gap_zar", "share"]].apply(
        pd.to_numeric, errors="coerce"
    )
    finite = numeric.to_numpy(dtype="float64")
    assert not np.isinf(finite[~np.isnan(finite)]).any(), "infinity leaked into an output"
    assert (numeric["estimate_zar"].dropna() >= 0).all(), "negative wallet estimate"
    assert (numeric["gap_zar"].dropna() >= 0).all(), "negative gap"
    shares = numeric["share"].dropna()
    assert ((shares >= 0) & (shares <= 1)).all(), "share outside 0-1"
    assert model.estimates["confidence"].between(0, 1).all()
    assert model.estimates["opportunity_score"].between(0, 1).all()


def _cash_row(scenario: str) -> pd.Series:
    features = synthetic_features(BREAKING_SCENARIOS[scenario])
    model = engine.run(features)
    return model.estimates[
        (model.estimates["entity_id"] == "T01")
        & (model.estimates["product"] == assumptions.CASH)
    ].iloc[0]


def test_a_null_denominator_produces_a_null_share_not_a_zero() -> None:
    for scenario in ("null_revenue", "no_external_financials_at_all"):
        row = _cash_row(scenario)
        assert pd.isna(row["share"]), scenario
        assert pd.isna(row["gap_zar"]), scenario
        assert row["share_basis"] == common.SHARE_NO_DENOMINATOR, scenario


def test_a_zero_denominator_with_real_activity_floors_rather_than_divides() -> None:
    """Revenue of zero against R3bn of observed collections means the disclosure
    is wrong, not that the client has no wallet. The floor takes over, the share
    resolves to 100%, and the flag says the modelled driver failed -- but no
    division by zero happens and no infinity is published."""
    row = _cash_row("zero_revenue")
    assert row["estimate_modelled_zar"] == 0.0
    assert row["estimate_zar"] == pytest.approx(row["observed_zar"])
    assert row["share"] == pytest.approx(1.0)
    assert "wallet_floored_at_observed" in row["diagnostic_flags"]


def test_a_negative_denominator_is_never_divided_by() -> None:
    features = synthetic_features(BREAKING_SCENARIOS["negative_revenue"])
    model = engine.run(features)
    row = model.estimates[
        (model.estimates["entity_id"] == "T01")
        & (model.estimates["product"] == assumptions.CASH)
    ].iloc[0]
    assert row["estimate_modelled_zar"] < 0
    # The floor lifts it to observed activity, so no negative wallet is published.
    assert row["estimate_zar"] >= 0
    assert pd.isna(row["share"]) or 0.0 <= row["share"] <= 1.0


def test_a_negative_driver_with_no_observed_activity_publishes_zero_not_a_negative() -> None:
    """The one combination the other fixtures miss: nothing observed to floor
    against, and a driver that has gone negative."""
    scenario = {
        **BREAKING_SCENARIOS["negative_revenue"],
        **BREAKING_SCENARIOS["no_observed_activity"],
    }
    model = engine.run(synthetic_features(scenario))
    row = model.estimates[
        (model.estimates["entity_id"] == "T01")
        & (model.estimates["product"] == assumptions.CASH)
    ].iloc[0]
    assert row["estimate_modelled_zar"] < 0
    assert row["estimate_zar"] == 0.0
    assert pd.isna(row["share"])
    assert row["share_basis"] == common.SHARE_NON_POSITIVE_DENOMINATOR


def test_observed_dwarfing_the_drivers_floors_the_wallet_instead_of_absurd_share() -> None:
    features = synthetic_features(BREAKING_SCENARIOS["observed_dwarfs_every_driver"])
    model = engine.run(features)
    broken = model.estimates[model.estimates["entity_id"] == "T01"]
    for product in (assumptions.FX, assumptions.TRADE, assumptions.CASH):
        row = broken[broken["product"] == product].iloc[0]
        assert row["share"] == pytest.approx(1.0), product
        assert row["gap_zar"] == pytest.approx(0.0), product
        assert "wallet_floored_at_observed" in row["diagnostic_flags"], product
        assert row["estimate_modelled_zar"] < row["estimate_zar"], product


def test_negative_working_capital_never_becomes_a_negative_component() -> None:
    features = synthetic_features(BREAKING_SCENARIOS["negative_working_capital"])
    model = engine.run(features)
    components = model.components[
        (model.components["entity_id"] == "T01")
        & (model.components["component"] == "working_capital")
    ]
    assert (components["component_zar"].dropna() >= 0).all()
    row = model.estimates[
        (model.estimates["entity_id"] == "T01")
        & (model.estimates["product"] == assumptions.LENDING)
    ].iloc[0]
    assert "negative_working_capital" in row["diagnostic_flags"]


def test_an_insurer_is_never_given_import_or_export_trade_finance() -> None:
    """Even handed a manufacturing-sized cost base and inventory."""
    features = synthetic_features(BREAKING_SCENARIOS["insurer_with_manufacturing_cost_base"])
    model = engine.run(features)
    components = model.components[
        (model.components["entity_id"] == "T01")
        & (model.components["product"] == assumptions.TRADE)
    ].set_index("component")["component_zar"]
    assert pd.isna(components["import_documentary"])
    assert pd.isna(components["export_documentary"])
    assert components["guarantees"] > 0
    row = model.estimates[
        (model.estimates["entity_id"] == "T01")
        & (model.estimates["product"] == assumptions.TRADE)
    ].iloc[0]
    assert "goods_trade_submodels_suppressed" in row["diagnostic_flags"]


def test_zero_observed_activity_gives_zero_share_not_null() -> None:
    features = synthetic_features(BREAKING_SCENARIOS["no_observed_activity"])
    model = engine.run(features)
    row = model.estimates[
        (model.estimates["entity_id"] == "T01")
        & (model.estimates["product"] == assumptions.CASH)
    ].iloc[0]
    assert row["share"] == 0.0
    assert row["gap_zar"] == pytest.approx(row["estimate_zar"])


def test_benchmarks_are_withheld_when_too_few_clients_can_support_them() -> None:
    """Three clients cannot define an upper-quartile peer intensity."""
    features = synthetic_features(count=3)
    model = engine.run(features)
    published = model.benchmarks[model.benchmarks["value"].notna()]
    trade_benchmarks = published[published["product"] == assumptions.TRADE]
    assert trade_benchmarks.empty
    trade_rows = model.estimates[model.estimates["product"] == assumptions.TRADE]
    # No benchmark means no modelled wallet -- but observed activity still
    # floors it, so the engine reports what it can and claims nothing more.
    assert trade_rows["estimate_modelled_zar"].isna().all()


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def test_two_runs_on_identical_input_produce_identical_output() -> None:
    features = synthetic_features()
    first = engine.run(features)
    second = engine.run(features)
    pd.testing.assert_frame_equal(first.estimates, second.estimates)
    pd.testing.assert_frame_equal(first.opportunities, second.opportunities)


def test_ranking_is_deterministic_under_row_reordering() -> None:
    """Ranks must come from the numbers, not from the order rows arrived in."""
    features = synthetic_features()
    forward = engine.run(features)
    reversed_frame = features.iloc[::-1].reset_index(drop=True)
    backward = engine.run(reversed_frame)

    key = ["entity_id", "product"]
    left = forward.estimates.set_index(key)["rank_overall"].sort_index()
    right = backward.estimates.set_index(key)["rank_overall"].sort_index()
    pd.testing.assert_series_equal(left, right)


def test_ranks_are_a_dense_permutation() -> None:
    model = engine.run(synthetic_features())
    ranks = sorted(model.estimates["rank_overall"])
    assert ranks == list(range(1, len(model.estimates) + 1))
    for _, group in model.estimates.groupby("product"):
        assert sorted(group["rank_in_product"]) == list(range(1, len(group) + 1))


def test_opportunity_score_reproduces_from_its_three_declared_factors() -> None:
    model = engine.run(synthetic_features())
    weights = assumptions.OPPORTUNITY_WEIGHTS
    expected = (
        weights["gap"] * model.estimates["opportunity_gap_scale"]
        + weights["confidence"] * model.estimates["confidence"]
        + weights["headroom"] * model.estimates["opportunity_headroom"]
    ).clip(0.0, 1.0)
    pd.testing.assert_series_equal(
        model.estimates["opportunity_score"], expected, check_names=False
    )


def test_headroom_is_neutral_where_no_share_exists() -> None:
    model = engine.run(synthetic_features())
    no_share = model.estimates[model.estimates["share"].isna()]
    assert (no_share["opportunity_headroom"] == opportunity.NEUTRAL_HEADROOM).all()
