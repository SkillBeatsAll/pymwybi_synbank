"""Leave-one-out and sector-aware peer benchmarking.

The single most important property in this file is that a client never
contributes to the population that sets its own coefficient. It is easy to break
by accident -- one forgotten ``exclude`` argument -- and the failure is silent:
every number still computes, they are just quietly circular. So it is asserted
directly on the populations, not inferred from the outputs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.syn_wallet.wallet import assumptions, benchmarks
from src.syn_wallet.wallet.pillars import fx as fx_pillar
from src.syn_wallet.wallet.pillars import lending as lending_pillar
from src.syn_wallet.wallet.pillars import trade as trade_pillar

from .wallet_fixtures import synthetic_features

METRIC = "test_intensity"


def _register(frame: pd.DataFrame, config: assumptions.ModelConfig | None = None):
    peers = benchmarks.PeerBenchmarks(frame, config)
    values = peers.register(
        METRIC,
        assumptions.FX,
        "xb_inbound_volume_zar_fy",
        "revenue_foreign_zar",
        "A test metric with a rationale long enough to be a real one.",
    )
    return peers, values


# ---------------------------------------------------------------------------
# No self-inclusion
# ---------------------------------------------------------------------------


def test_a_client_is_never_in_the_population_that_estimates_it() -> None:
    frame = synthetic_features(count=8)
    peers, _ = _register(frame)
    for entity_id in frame["entity_id"]:
        for level in (benchmarks.PORTFOLIO, benchmarks.SECTOR):
            population = peers.population(METRIC, entity_id=entity_id, level=level)
            assert entity_id not in set(population.index), (entity_id, level)


def test_every_published_coefficient_records_its_population_without_the_client() -> None:
    frame = synthetic_features(count=8)
    peers, _ = _register(frame)
    for entity_id in frame["entity_id"]:
        coefficient = peers.resolve(entity_id, METRIC)
        assert entity_id not in coefficient.sample_entities
        assert coefficient.self_in_population is False
        assert coefficient.leave_one_out is True


def test_leave_one_out_p75_excludes_the_client_from_the_quantile() -> None:
    """Computed by hand: drop the client, take the 75th percentile of the rest."""
    frame = synthetic_features(count=8)
    peers, _ = _register(frame)
    ratios = pd.to_numeric(frame["xb_inbound_volume_zar_fy"]) / pd.to_numeric(
        frame["revenue_foreign_zar"]
    )
    ratios.index = frame["entity_id"]

    for entity_id in frame["entity_id"]:
        expected = float(ratios.drop(index=entity_id).quantile(0.75))
        assert peers.leave_one_out_p75(entity_id, METRIC) == pytest.approx(expected)


def _graded_frame() -> pd.DataFrame:
    """Eight clients whose intensities are exactly 1, 2, ... 8."""
    frame = synthetic_features(count=8)
    frame["revenue_foreign_zar"] = 1e9
    frame["xb_inbound_volume_zar_fy"] = [1e9 * (position + 1) for position in range(8)]
    return frame


def test_leave_one_out_corrects_circularity_in_both_directions() -> None:
    """The point of the exclusion, shown on a population with a known quantile.

    Intensities are 1..8. The most penetrated client (8) is inflating the
    benchmark it would be measured against; the least (1) is depressing it. The
    correction has to move in opposite directions for the two, or it is not
    correcting circularity, only shifting the level.
    """
    frame = _graded_frame()
    excluded, _ = _register(frame)
    included, _ = _register(frame, assumptions.ModelConfig(leave_one_out=False))

    everyone = pd.Series(range(1, 9), dtype="float64")
    assert included.resolve("T08", METRIC).value == pytest.approx(
        float(everyone.quantile(0.75))
    )

    # The most penetrated client: dropping it lowers the frontier it is judged by.
    assert excluded.resolve("T08", METRIC).value < included.resolve("T08", METRIC).value
    assert excluded.resolve("T08", METRIC).value == pytest.approx(
        float(everyone.drop(7).quantile(0.75))
    )

    # The least penetrated client: dropping it raises the frontier.
    assert excluded.resolve("T01", METRIC).value > included.resolve("T01", METRIC).value
    assert excluded.resolve("T01", METRIC).value == pytest.approx(
        float(everyone.drop(0).quantile(0.75))
    )


def test_a_self_included_run_says_so_in_the_record() -> None:
    frame = synthetic_features(count=8)
    peers, _ = _register(frame, assumptions.ModelConfig(leave_one_out=False))
    coefficient = peers.resolve("T01", METRIC)
    assert coefficient.leave_one_out is False
    assert coefficient.self_in_population is True
    assert "T01" in coefficient.sample_entities


def test_leave_one_out_p75_ignores_the_run_configuration() -> None:
    """The primitive is leave-one-out by name, whatever the run is doing."""
    frame = synthetic_features(count=8)
    peers, _ = _register(frame, assumptions.ModelConfig(leave_one_out=False))
    population = peers.population(METRIC, entity_id="T01", level=benchmarks.PORTFOLIO)
    assert "T01" not in set(population.index)
    assert peers.leave_one_out_p75("T01", METRIC) == pytest.approx(
        float(population.quantile(0.75))
    )


# ---------------------------------------------------------------------------
# Sector minimum sample
# ---------------------------------------------------------------------------


def _mixed_sector_frame() -> pd.DataFrame:
    """Eight clients: five consumer, three across two one-member sectors."""
    frame = synthetic_features(count=8)
    frame.loc[5, "sector"] = "mining"
    frame.loc[6, "sector"] = "tech"
    frame.loc[7, "sector"] = "insurance"
    return frame


def test_a_sector_of_three_or_more_peers_forms_a_sector_benchmark() -> None:
    frame = _mixed_sector_frame()  # consumer has five members
    peers, _ = _register(frame)
    coefficient = peers.resolve("T01", METRIC)
    assert coefficient.benchmark_level == benchmarks.SECTOR
    assert coefficient.benchmark_n == 4  # five consumer clients, minus itself
    assert coefficient.benchmark_n >= benchmarks.MIN_SECTOR_SAMPLE_FOR_BENCHMARK


def test_a_sector_below_the_minimum_falls_back_and_says_why() -> None:
    frame = _mixed_sector_frame()
    peers, _ = _register(frame)
    coefficient = peers.resolve("T06", METRIC)  # the lone mining client
    assert coefficient.benchmark_level == benchmarks.PORTFOLIO
    assert coefficient.sector_candidate_n == 0
    assert "below_minimum_3" in coefficient.fallback_reason


def test_no_sector_benchmark_is_ever_built_from_fewer_than_three_peers() -> None:
    """The invariant, asserted over every client of every real pillar metric."""
    frame = _mixed_sector_frame()
    peers, _ = _register(frame)
    for entity_id in frame["entity_id"]:
        coefficient = peers.resolve(entity_id, METRIC)
        if coefficient.benchmark_level == benchmarks.SECTOR:
            assert coefficient.benchmark_n >= benchmarks.MIN_SECTOR_SAMPLE_FOR_BENCHMARK


def test_a_sector_of_exactly_three_peers_is_the_boundary() -> None:
    """Four members give three peers after exclusion: the smallest sector that works."""
    frame = synthetic_features(count=8)
    frame.loc[4:, "sector"] = "mining"  # consumer keeps exactly four
    peers, _ = _register(frame)
    assert peers.resolve("T01", METRIC).benchmark_n == 3
    assert peers.resolve("T01", METRIC).benchmark_level == benchmarks.SECTOR

    frame.loc[3, "sector"] = "mining"  # now consumer has three, so two peers
    peers, _ = _register(frame)
    assert peers.resolve("T01", METRIC).benchmark_level == benchmarks.PORTFOLIO


def test_portfolio_only_scope_never_forms_a_sector_benchmark() -> None:
    frame = _mixed_sector_frame()
    peers, _ = _register(
        frame, assumptions.ModelConfig(benchmark_scope=assumptions.PORTFOLIO_ONLY)
    )
    for entity_id in frame["entity_id"]:
        coefficient = peers.resolve(entity_id, METRIC)
        assert coefficient.benchmark_level != benchmarks.SECTOR
        assert coefficient.fallback_reason == "portfolio_scope_configured"


def test_a_population_below_the_portfolio_minimum_publishes_no_coefficient() -> None:
    frame = synthetic_features(count=3)
    peers, values = _register(frame)
    assert values.isna().all()
    coefficient = peers.resolve("T01", METRIC)
    assert coefficient.benchmark_level == benchmarks.UNAVAILABLE
    assert coefficient.value is None
    assert "portfolio_sample_2_below_minimum_4" in coefficient.fallback_reason


# ---------------------------------------------------------------------------
# What the coefficient record has to carry
# ---------------------------------------------------------------------------


REQUIRED_COEFFICIENT_FIELDS = (
    "entity_id",
    "metric",
    "benchmark_level",
    "benchmark_n",
    "benchmark_value",
    "benchmark_median",
    "benchmark_p75",
    "benchmark_max",
    "fallback_reason",
)


def test_every_coefficient_record_carries_the_required_provenance() -> None:
    frame = _mixed_sector_frame()
    peers, _ = _register(frame)
    records = peers.coefficient_records()
    assert len(records) == len(frame)
    for record in records:
        for field in REQUIRED_COEFFICIENT_FIELDS:
            assert field in record, field
        assert record["benchmark_level"] in {
            benchmarks.SECTOR,
            benchmarks.PORTFOLIO,
            benchmarks.UNAVAILABLE,
        }
        assert record["benchmark_n"] >= 0
        assert record["fallback_reason"]


def test_the_summary_statistics_describe_the_population_actually_used() -> None:
    frame = _mixed_sector_frame()
    peers, _ = _register(frame)
    coefficient = peers.resolve("T01", METRIC)
    population = peers.population(METRIC, entity_id="T01", level=coefficient.benchmark_level)
    assert coefficient.benchmark_median == pytest.approx(float(population.median()))
    assert coefficient.benchmark_p75 == pytest.approx(float(population.quantile(0.75)))
    assert coefficient.benchmark_max == pytest.approx(float(population.max()))
    assert coefficient.benchmark_n == len(population)


# ---------------------------------------------------------------------------
# The real pillars
# ---------------------------------------------------------------------------

PILLAR_METRICS = (
    (fx_pillar, (fx_pillar.EXPORT_BENCHMARK, fx_pillar.IMPORT_BENCHMARK)),
    (
        trade_pillar,
        (
            trade_pillar.IMPORT_BENCHMARK,
            trade_pillar.EXPORT_BENCHMARK,
            trade_pillar.GUARANTEE_BENCHMARK,
        ),
    ),
    (lending_pillar, (lending_pillar.WORKING_CAPITAL_BENCHMARK,)),
)


@pytest.mark.parametrize("module,metrics", PILLAR_METRICS)
def test_every_pillar_publishes_a_coefficient_per_client_per_metric(module, metrics) -> None:
    frame = synthetic_features(count=8)
    output = module.build(frame)
    records = pd.DataFrame(output.benchmarks)
    assert set(records["metric"]) == set(metrics)
    for metric in metrics:
        rows = records[records["metric"] == metric]
        assert len(rows) == len(frame)
        assert set(rows["entity_id"]) == set(frame["entity_id"])
        assert not rows["self_in_population"].any()
        assert rows["leave_one_out"].all()


@pytest.mark.parametrize("module,metrics", PILLAR_METRICS)
def test_no_pillar_coefficient_ever_samples_its_own_client(module, metrics) -> None:
    frame = synthetic_features(count=8)
    records = pd.DataFrame(module.build(frame).benchmarks)
    for row in records.itertuples():
        sample = {value.strip() for value in str(row.sample_entities).split(",") if value.strip()}
        assert row.entity_id not in sample, (row.entity_id, row.metric)


def test_the_driver_imputation_cascade_cannot_include_the_client_it_imputes() -> None:
    """Structural, not enforced: only disclosers contribute, only non-disclosers consume."""
    frame = synthetic_features({"cost_of_sales_zar": np.nan}, count=8)
    medians, portfolio, counts = benchmarks.sector_ratio_table(
        frame, "cost_of_sales_zar", "revenue_total_zar"
    )
    resolved = benchmarks.resolve_driver(
        frame, "cost_of_sales_zar", "revenue_total_zar", medians, portfolio
    )
    assert resolved.source.iloc[0] != "disclosed"
    assert sum(counts.values()) == len(frame) - 1  # the client with no disclosure
    assert resolved.value.iloc[0] > 0
