"""Coefficients measured from this portfolio, and the driver-resolution cascade.

Two jobs:

**Portfolio benchmarks.** Where no accounting identity fixes a coefficient, it is
measured rather than invented: the 75th-percentile observed intensity across the
clients that have both the economic driver and the observed activity. The
resulting number is "what a well-penetrated peer in this portfolio achieves per
rand of driver". Every benchmark records its sample size and the clients it was
computed from, so a reader can check it.

**Driver resolution.** External financial fields are unevenly disclosed. A driver
is resolved through a cascade -- disclosed value, then sector peer ratio, then a
portfolio ratio -- and each step records where the number came from and a quality
weight that feeds confidence. A driver is never silently imputed to zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from . import assumptions

#: Quality weight per driver source, feeding the confidence engine.
SOURCE_QUALITY = {
    "disclosed": 1.0,
    "sector_benchmark": 0.60,
    "portfolio_benchmark": 0.35,
    "unavailable": 0.0,
}

#: A sector ratio is only used when at least this many peers disclose it.
MIN_SECTOR_SAMPLE = 2

#: A portfolio benchmark is only published when at least this many clients
#: contribute. Below it the intensity is not a benchmark, it is an anecdote.
MIN_BENCHMARK_SAMPLE = 4


@dataclass(frozen=True)
class Benchmark:
    """One measured intensity coefficient."""

    name: str
    value: float | None
    product: str
    numerator: str
    denominator: str
    percentile: float
    sample_size: int
    sample_entities: tuple[str, ...]
    median: float | None
    maximum: float | None
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "product": self.product,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "percentile": self.percentile,
            "sample_size": self.sample_size,
            "sample_entities": ", ".join(self.sample_entities),
            "sample_median": self.median,
            "sample_maximum": self.maximum,
            "basis": assumptions.PORTFOLIO_BENCHMARK,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class ResolvedDriver:
    """A driver value per client, with provenance."""

    value: pd.Series
    source: pd.Series
    quality: pd.Series

    def is_disclosed(self) -> pd.Series:
        return self.source == "disclosed"


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce").astype("float64")


def measure_benchmark(
    frame: pd.DataFrame,
    name: str,
    product: str,
    numerator: str,
    denominator: str,
    rationale: str,
    eligible: pd.Series | None = None,
    percentile: float = assumptions.BENCHMARK_PERCENTILE,
) -> Benchmark:
    """Measure an intensity coefficient from the clients that can support it.

    Only clients with a positive denominator and a non-null numerator
    contribute. ``eligible`` further restricts the sample -- typically to the
    sectors where the driver is economically comparable, so that an insurer's
    cost of sales cannot set a mining client's trade-finance benchmark.
    """
    numerator_values = _numeric(frame, numerator)
    denominator_values = _numeric(frame, denominator)
    mask = numerator_values.notna() & denominator_values.notna() & (denominator_values > 0)
    if eligible is not None:
        mask &= eligible.fillna(False)

    sample = (numerator_values[mask] / denominator_values[mask]).dropna()
    entities = tuple(frame.loc[sample.index, "entity_id"])
    if len(sample) < MIN_BENCHMARK_SAMPLE:
        return Benchmark(
            name, None, product, numerator, denominator, percentile,
            len(sample), entities, None, None,
            rationale + f" NOT PUBLISHED: only {len(sample)} clients could contribute, below the "
            f"minimum of {MIN_BENCHMARK_SAMPLE}.",
        )
    return Benchmark(
        name,
        float(sample.quantile(percentile)),
        product,
        numerator,
        denominator,
        percentile,
        len(sample),
        entities,
        float(sample.median()),
        float(sample.max()),
        rationale,
    )


def sector_ratio_table(
    frame: pd.DataFrame,
    numerator: str,
    denominator: str,
    eligible: pd.Series | None = None,
) -> tuple[dict[str, float], float | None, dict[str, int]]:
    """Median ``numerator / denominator`` per sector, plus a portfolio fallback.

    Returns ``(sector_medians, portfolio_median, sector_sample_sizes)``. A sector
    only appears when at least :data:`MIN_SECTOR_SAMPLE` of its clients disclose
    the numerator, so a single client never defines its sector's ratio.
    """
    numerator_values = _numeric(frame, numerator)
    denominator_values = _numeric(frame, denominator)
    mask = numerator_values.notna() & denominator_values.notna() & (denominator_values > 0)
    if eligible is not None:
        mask &= eligible.fillna(False)

    ratios = pd.DataFrame(
        {
            "sector": frame.loc[mask, "sector"],
            "ratio": numerator_values[mask] / denominator_values[mask],
        }
    )
    counts = ratios.groupby("sector")["ratio"].size().to_dict()
    medians = {
        sector: float(value)
        for sector, value in ratios.groupby("sector")["ratio"].median().items()
        if counts.get(sector, 0) >= MIN_SECTOR_SAMPLE
    }
    portfolio = float(ratios["ratio"].median()) if not ratios.empty else None
    return medians, portfolio, {sector: int(count) for sector, count in counts.items()}


def resolve_driver(
    frame: pd.DataFrame,
    disclosed_column: str,
    scale_column: str,
    sector_medians: dict[str, float],
    portfolio_median: float | None,
    allow_imputation: pd.Series | None = None,
) -> ResolvedDriver:
    """Resolve one economic driver per client through the disclosure cascade.

    1. the disclosed value;
    2. ``scale_column`` times the client's sector median ratio;
    3. ``scale_column`` times the portfolio median ratio;
    4. unavailable -- NULL, never zero.

    ``allow_imputation`` gates steps 2 and 3. It is False for sectors where the
    peer ratio would be economically meaningless, so an insurer never receives an
    imputed cost of sales built from manufacturers.
    """
    disclosed = _numeric(frame, disclosed_column)
    scale = _numeric(frame, scale_column)
    sectors = frame["sector"]

    value = disclosed.copy()
    source = pd.Series("unavailable", index=frame.index, dtype="object")
    source[disclosed.notna()] = "disclosed"

    gate = pd.Series(True, index=frame.index) if allow_imputation is None else allow_imputation.fillna(False)

    sector_ratio = sectors.map(sector_medians)
    use_sector = value.isna() & gate & sector_ratio.notna() & scale.notna()
    value[use_sector] = scale[use_sector] * sector_ratio[use_sector]
    source[use_sector] = "sector_benchmark"

    if portfolio_median is not None:
        use_portfolio = value.isna() & gate & scale.notna()
        value[use_portfolio] = scale[use_portfolio] * portfolio_median
        source[use_portfolio] = "portfolio_benchmark"

    quality = source.map(SOURCE_QUALITY).astype("float64")
    return ResolvedDriver(value=value, source=source, quality=quality)


class BenchmarkSet:
    """Every measured benchmark for one run, keyed by name."""

    def __init__(self) -> None:
        self._benchmarks: dict[str, Benchmark] = {}

    def add(self, benchmark: Benchmark) -> Benchmark:
        self._benchmarks[benchmark.name] = benchmark
        return benchmark

    def __getitem__(self, name: str) -> Benchmark:
        return self._benchmarks[name]

    def value(self, name: str) -> float | None:
        return self._benchmarks[name].value

    def as_records(self) -> list[dict[str, Any]]:
        return [benchmark.as_dict() for benchmark in self._benchmarks.values()]

    def __contains__(self, name: str) -> bool:
        return name in self._benchmarks

    def __len__(self) -> int:
        return len(self._benchmarks)
