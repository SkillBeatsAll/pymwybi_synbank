"""The sensitivity sweep: does it test what it claims, and is it reproducible.

The full grid is 36 model runs and takes several seconds, so most of these run a
deliberately reduced grid. The one test that exercises the whole grid is marked
and checks only its shape.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.syn_wallet.wallet import assumptions, engine, sensitivity

from .wallet_fixtures import synthetic_features

#: A four-scenario grid: base, one percentile change, one exclusion change, one
#: scope change. Enough to exercise every comparison path.
REDUCED_GRID = (
    sensitivity.base_config(),
    assumptions.ModelConfig(
        label="median_loo_sector_capex30",
        benchmark_percentile=0.50,
    ),
    assumptions.ModelConfig(
        label="p75_self_included_sector_capex30",
        leave_one_out=False,
    ),
    assumptions.ModelConfig(
        label="p75_loo_portfolio_capex40",
        benchmark_scope=assumptions.PORTFOLIO_ONLY,
        capex_debt_funded_share=0.40,
    ),
)


@pytest.fixture(scope="module")
def features() -> pd.DataFrame:
    return synthetic_features(count=8)


@pytest.fixture(scope="module")
def result(features: pd.DataFrame) -> sensitivity.SensitivityResult:
    return sensitivity.run(features, list(REDUCED_GRID))


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


def test_the_full_grid_covers_every_declared_variant() -> None:
    grid = sensitivity.scenario_grid()
    assert len(grid) == len(sensitivity.PERCENTILES) * 2 * 2 * len(
        sensitivity.CAPEX_SHARES
    )
    assert grid[0].label == sensitivity.base_config().label
    assert len({config.label for config in grid}) == len(grid)

    assert {config.benchmark_percentile for config in grid} == {
        percentile for percentile, _ in sensitivity.PERCENTILES
    }
    assert {config.capex_debt_funded_share for config in grid} == set(
        sensitivity.CAPEX_SHARES
    )
    assert {config.leave_one_out for config in grid} == {True, False}
    assert {config.benchmark_scope for config in grid} == set(
        assumptions.BENCHMARK_SCOPES
    )


def test_the_capex_scenarios_are_the_ones_the_brief_asks_for() -> None:
    assert sensitivity.CAPEX_SHARES == (0.20, 0.30, 0.40)
    assert assumptions.CAPEX_DEBT_FUNDED_SHARE in sensitivity.CAPEX_SHARES


def test_the_base_scenario_matches_the_published_model() -> None:
    base = sensitivity.base_config()
    published = assumptions.BASE_CONFIG
    assert base.benchmark_percentile == published.benchmark_percentile
    assert base.leave_one_out == published.leave_one_out
    assert base.benchmark_scope == published.benchmark_scope
    assert base.capex_debt_funded_share == published.capex_debt_funded_share


def test_the_grid_must_contain_the_base_model(features: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="base model"):
        sensitivity.run(features, [assumptions.ModelConfig(label="not_the_base")])


def test_an_invalid_configuration_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="benchmark_scope"):
        assumptions.ModelConfig(benchmark_scope="whatever")
    with pytest.raises(ValueError, match="benchmark_percentile"):
        assumptions.ModelConfig(benchmark_percentile=1.5)
    with pytest.raises(ValueError, match="capex_debt_funded_share"):
        assumptions.ModelConfig(capex_debt_funded_share=-0.1)


# ---------------------------------------------------------------------------
# What the sweep computes
# ---------------------------------------------------------------------------


def test_the_detail_table_has_one_row_per_scenario_client_product(
    result: sensitivity.SensitivityResult, features: pd.DataFrame
) -> None:
    expected = len(REDUCED_GRID) * len(features) * len(assumptions.PRODUCTS)
    assert len(result.detail) == expected
    assert not result.detail.duplicated(
        subset=["scenario", "entity_id", "product"]
    ).any()
    assert set(result.detail["scenario"]) == {config.label for config in REDUCED_GRID}


def test_the_base_scenario_compares_perfectly_against_itself(
    result: sensitivity.SensitivityResult,
) -> None:
    base = result.summary[result.summary["is_base"]]
    assert len(base) == 1
    row = base.iloc[0]
    assert row["spearman_gap_zar"] == pytest.approx(1.0)
    assert row["spearman_commercial_rank"] == pytest.approx(1.0)
    assert row["total_gap_zar_vs_base"] == pytest.approx(0.0)
    assert row["top10_overlap"] == sensitivity.TOP_N
    assert row["clients_moving_rank_by_5_or_more"] == 0


def test_every_requested_measure_is_present(
    result: sensitivity.SensitivityResult,
) -> None:
    for column in (
        "spearman_gap_zar",
        "spearman_share",
        "spearman_commercial_opportunity_score",
        "spearman_opportunity_intensity",
        "spearman_commercial_rank",
        "spearman_intensity_rank",
        "top10_overlap",
        "top10_intensity_overlap",
        "total_gap_zar",
        "total_gap_zar_vs_base",
    ):
        assert column in result.summary.columns, column
    for column in (
        "total_gap_zar",
        "total_gap_vs_base",
        "spearman_gap",
        "spearman_rank_in_product",
    ):
        assert column in result.product_summary.columns, column


def test_product_ranks_are_compared_for_every_product(
    result: sensitivity.SensitivityResult,
) -> None:
    assert set(result.product_summary["product"]) == set(assumptions.PRODUCTS)
    assert len(result.product_summary) == len(REDUCED_GRID) * len(assumptions.PRODUCTS)


def test_correlations_stay_inside_their_range(
    result: sensitivity.SensitivityResult,
) -> None:
    for column in result.summary.columns:
        if not column.startswith("spearman_"):
            continue
        values = pd.to_numeric(result.summary[column], errors="coerce").dropna()
        assert ((values >= -1.0) & (values <= 1.0)).all(), column


def test_top_ten_overlap_is_bounded(result: sensitivity.SensitivityResult) -> None:
    overlap = result.summary["top10_overlap"]
    assert ((overlap >= 0) & (overlap <= sensitivity.TOP_N)).all()


# ---------------------------------------------------------------------------
# What the sweep is supposed to prove
# ---------------------------------------------------------------------------


def test_the_identity_anchored_pillar_is_immune_to_every_scenario(
    result: sensitivity.SensitivityResult,
) -> None:
    """Cash management uses no peer coefficient, so nothing in the grid can move it."""
    cash = result.product_summary[
        result.product_summary["product"] == assumptions.CASH
    ]
    assert cash["total_gap_vs_base"].abs().max() == pytest.approx(0.0, abs=1e-12)
    assert cash["spearman_rank_in_product"].min() == pytest.approx(1.0)


def test_the_benchmarked_pillars_do_move() -> None:
    """If a percentile change moved nothing, the sweep would not be testing anything.

    Needs a portfolio whose peer *intensities* differ. The default fixture
    scales every client uniformly, so its intensity ratios are identical and
    every percentile of them is the same number -- which is itself a useful
    property for the other tests, and useless for this one.
    """
    varied = synthetic_features(count=8)
    # Vary the internal flows independently of the external financials, which is
    # what makes one client more penetrated than another.
    for position in range(len(varied)):
        multiplier = 1.0 + position
        for column in (
            "xb_inbound_volume_zar_fy",
            "xb_outbound_volume_zar_fy",
            "tf_import_value_zar_fy",
            "tf_export_value_zar_fy",
            "tf_guarantees_value_zar_fy",
        ):
            varied.loc[position, column] = varied.loc[position, column] * multiplier

    result = sensitivity.run(varied, list(REDUCED_GRID))
    benchmarked = result.product_summary[
        result.product_summary["product"].isin([assumptions.FX, assumptions.TRADE])
    ]
    assert benchmarked["total_gap_vs_base"].abs().max() > 0.05


def test_the_capex_share_only_moves_lending(features: pd.DataFrame) -> None:
    base = engine.run(features, sensitivity.base_config())
    doubled = engine.run(
        features,
        assumptions.ModelConfig(
            label=sensitivity.base_config().label, capex_debt_funded_share=0.40
        ),
    )
    key = ["entity_id", "product"]
    left = base.estimates.set_index(key)["estimate_zar"]
    right = doubled.estimates.set_index(key)["estimate_zar"]
    changed = {
        product
        for (_, product) in left[~np.isclose(left, right, equal_nan=True)].index
    }
    assert changed == {assumptions.LENDING}


def test_robustness_classifies_every_product_and_the_portfolio(
    result: sensitivity.SensitivityResult,
) -> None:
    assert set(result.robustness["product"]) == set(assumptions.PRODUCTS) | {"ALL"}
    for row in result.robustness.itertuples():
        assert row.verdict
        assert row.note
        assert row.scenarios_tested == len(REDUCED_GRID) - 1


def test_a_pillar_with_no_rand_magnitude_is_not_called_sensitive(
    result: sensitivity.SensitivityResult,
) -> None:
    ib = result.robustness[result.robustness["product"] == assumptions.IB].iloc[0]
    assert ib["verdict"] == "NO_RAND_MAGNITUDE_ORDERING_ROBUST"
    assert ib["magnitude_robust"]


def test_the_sweep_is_reproducible(features: pd.DataFrame) -> None:
    first = sensitivity.run(features, list(REDUCED_GRID))
    second = sensitivity.run(features, list(REDUCED_GRID))
    pd.testing.assert_frame_equal(first.detail, second.detail)
    pd.testing.assert_frame_equal(first.summary, second.summary)
    pd.testing.assert_frame_equal(first.robustness, second.robustness)


def test_a_constant_ordering_is_not_reported_as_perfect_correlation() -> None:
    """Two constant series correlate with nothing; claiming 1.0 would be a lie."""
    constant = pd.Series([1.0, 1.0, 1.0, 1.0])
    assert np.isnan(sensitivity._spearman(constant, constant))
    assert np.isnan(sensitivity._spearman(pd.Series([1.0, 2.0]), pd.Series([1.0, 2.0])))
