"""Peer coefficients measured from this portfolio, and the driver-resolution cascade.

Two jobs:

**Peer benchmarks.** Where no accounting identity fixes a coefficient, it is
measured rather than invented: the 75th-percentile observed intensity across
*peers*, which is "what a well-penetrated peer achieves per rand of driver".

Two rules make that defensible rather than merely plausible.

*Leave-one-out.* The client being estimated is removed from the population that
sets its own coefficient. Including it is circular in a way that runs in both
directions: a heavily penetrated client raises the benchmark it is then measured
against, flattening its own gap; a dormant client drags the benchmark down and
makes its own share look healthy. With twenty clients one company is 5% of the
portfolio and up to a third of its own sector, so this is a material correction,
not a technicality. :meth:`PeerBenchmarks.leave_one_out_p75` is the primitive.

*Sector before portfolio, but only with enough peers.* A mining client's trade
intensity is better predicted by other miners than by the portfolio. But a
"sector benchmark" built from one or two companies is a restatement of those
companies, so a sector population must reach
:data:`MIN_SECTOR_SAMPLE_FOR_BENCHMARK` peers *after* the estimated client is
removed. Otherwise the portfolio population is used and the reason is recorded
per client, never inferred.

Every coefficient is published per client x metric with its level, sample size,
median, upper quartile, maximum and fallback reason, so a reader can reconstruct
any single estimate from the coefficient table.

**Driver resolution.** External financial fields are unevenly disclosed. A driver
is resolved through a cascade -- disclosed value, then sector peer ratio, then a
portfolio ratio -- and each step records where the number came from and a quality
weight that feeds confidence. A driver is never silently imputed to zero. This
cascade is leave-one-out *by construction*: only clients that disclose the field
contribute a ratio, and only clients that did not disclose it need one, so a
client can never impute its own driver from itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from . import assumptions

#: Quality weight per driver source, feeding the confidence engine.
SOURCE_QUALITY = {
    "disclosed": 1.0,
    "sector_benchmark": 0.60,
    "portfolio_benchmark": 0.35,
    "unavailable": 0.0,
}

#: A sector *imputation* ratio is only used when at least this many peers
#: disclose the field being imputed.
MIN_SECTOR_SAMPLE = 2

#: A sector *benchmark* is only formed from at least this many peers, counted
#: after the estimated client is excluded. Below it, one company would set its
#: sector's frontier.
MIN_SECTOR_SAMPLE_FOR_BENCHMARK = 3

#: A portfolio benchmark is only published when at least this many clients
#: contribute, again after exclusion. Below it the intensity is not a benchmark,
#: it is an anecdote.
MIN_BENCHMARK_SAMPLE = 4

#: Benchmark levels, in preference order.
SECTOR = "sector"
PORTFOLIO = "portfolio"
UNAVAILABLE = "unavailable"


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce").astype("float64")


# ---------------------------------------------------------------------------
# Peer benchmarks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkMetric:
    """One intensity ratio the portfolio can be measured on."""

    name: str
    product: str
    numerator: str
    denominator: str
    percentile: float
    rationale: str


@dataclass(frozen=True)
class BenchmarkCoefficient:
    """The coefficient used for **one client**, with the population behind it.

    This is the audit record the model report and the dashboard read. It answers
    "what number was used for this client, measured from whom, and why that
    population rather than another".
    """

    entity_id: str
    entity_name: str
    sector: str
    metric: str
    product: str
    numerator: str
    denominator: str
    value: float | None
    percentile: float
    benchmark_level: str
    benchmark_n: int
    benchmark_value: float | None
    benchmark_median: float | None
    benchmark_p75: float | None
    benchmark_max: float | None
    leave_one_out: bool
    self_in_population: bool
    sector_candidate_n: int
    fallback_reason: str
    sample_entities: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "sector": self.sector,
            "metric": self.metric,
            "product": self.product,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
            "percentile": self.percentile,
            "benchmark_level": self.benchmark_level,
            "benchmark_n": self.benchmark_n,
            "benchmark_value": self.benchmark_value,
            "benchmark_median": self.benchmark_median,
            "benchmark_p75": self.benchmark_p75,
            "benchmark_max": self.benchmark_max,
            "leave_one_out": self.leave_one_out,
            "self_in_population": self.self_in_population,
            "sector_candidate_n": self.sector_candidate_n,
            "fallback_reason": self.fallback_reason,
            "sample_entities": ", ".join(self.sample_entities),
            "basis": assumptions.PORTFOLIO_BENCHMARK,
            "rationale": "",
        }


class PeerBenchmarks:
    """Every peer coefficient for one run, resolved per client.

    Construct once per model run, :meth:`register` each metric, and read the
    per-client coefficient series it returns. The populations are computed once;
    resolution per client is a filter over them.
    """

    def __init__(
        self, frame: pd.DataFrame, config: assumptions.ModelConfig | None = None
    ) -> None:
        self._frame = frame
        self._config = config or assumptions.BASE_CONFIG
        self._entity_ids = frame["entity_id"].astype(str)
        self._sectors = frame["sector"].astype(str)
        self._entity_names = frame["entity_name"].astype(str)
        self._metrics: dict[str, BenchmarkMetric] = {}
        #: metric -> ratios indexed by entity_id (the eligible population).
        self._ratios: dict[str, pd.Series] = {}
        #: metric -> sector of each contributor, indexed by entity_id.
        self._population_sectors: dict[str, pd.Series] = {}
        self._coefficients: dict[str, list[BenchmarkCoefficient]] = {}

    # -- registration ------------------------------------------------------

    def register(
        self,
        name: str,
        product: str,
        numerator: str,
        denominator: str,
        rationale: str,
        eligible: pd.Series | None = None,
        percentile: float | None = None,
    ) -> pd.Series:
        """Measure a metric across the portfolio and resolve it for every client.

        Only clients with a positive denominator and a non-null numerator
        contribute. ``eligible`` further restricts the population -- typically to
        the sectors where the driver is economically comparable, so that an
        insurer's cost of sales cannot set a mining client's trade-finance
        benchmark.

        Returns the per-client coefficient as a Series aligned to the feature
        frame's index. Clients whose population is too thin get NaN, never a
        borrowed number.
        """
        percentile = self._config.benchmark_percentile if percentile is None else percentile
        numerator_values = _numeric(self._frame, numerator)
        denominator_values = _numeric(self._frame, denominator)
        mask = (
            numerator_values.notna() & denominator_values.notna() & (denominator_values > 0)
        )
        if eligible is not None:
            mask &= eligible.fillna(False).astype(bool)

        ratios = (numerator_values[mask] / denominator_values[mask]).dropna()
        ratios.index = self._entity_ids[ratios.index]

        self._metrics[name] = BenchmarkMetric(
            name, product, numerator, denominator, percentile, rationale
        )
        self._ratios[name] = ratios
        self._population_sectors[name] = self._sectors[mask].set_axis(
            self._entity_ids[mask].to_numpy()
        )

        coefficients = [
            self.resolve(entity_id, name) for entity_id in self._entity_ids
        ]
        self._coefficients[name] = coefficients
        return pd.Series(
            [
                coefficient.value if coefficient.value is not None else np.nan
                for coefficient in coefficients
            ],
            index=self._frame.index,
            dtype="float64",
        )

    # -- population access -------------------------------------------------

    def population(
        self,
        metric: str,
        entity_id: str | None = None,
        level: str = PORTFOLIO,
        exclude: bool = True,
    ) -> pd.Series:
        """The peer ratios behind a coefficient, indexed by ``entity_id``.

        ``entity_id`` names the client the coefficient is *for*: it selects the
        sector at ``level=SECTOR``, and unless ``exclude`` is False it is dropped
        from the result. The default is to drop it, so the returned population is
        literally the one used to estimate that client -- which is what the
        self-inclusion tests assert against.
        """
        ratios = self._ratios[metric]
        if level == SECTOR:
            if entity_id is None:
                raise ValueError("a sector population needs the client whose sector to use")
            sector = self._sector_of(entity_id)
            ratios = ratios[self._population_sectors[metric].reindex(ratios.index) == sector]
        if entity_id is not None and exclude:
            ratios = ratios.drop(index=entity_id, errors="ignore")
        return ratios

    def _sector_of(self, entity_id: str) -> str:
        matches = self._sectors[self._entity_ids == entity_id]
        if matches.empty:
            raise KeyError(f"unknown entity_id: {entity_id}")
        return str(matches.iloc[0])

    # -- the primitive -----------------------------------------------------

    def leave_one_out_p75(self, entity_id: str, metric: str) -> float | None:
        """The 75th-percentile peer intensity for ``metric``, excluding ``entity_id``.

        The population is the client's sector when that sector has at least
        :data:`MIN_SECTOR_SAMPLE_FOR_BENCHMARK` other members contributing, and
        the whole portfolio otherwise. The client is always excluded, whatever
        the run configuration says -- this method is the leave-one-out primitive
        and is used directly by the tests that prove no self-inclusion.
        """
        return self.leave_one_out_percentile(entity_id, metric, 0.75)

    def leave_one_out_percentile(
        self, entity_id: str, metric: str, percentile: float
    ) -> float | None:
        """:meth:`leave_one_out_p75` at an arbitrary percentile."""
        level, population, _ = self._choose_population(entity_id, metric, leave_one_out=True)
        if level == UNAVAILABLE:
            return None
        return float(population.quantile(percentile))

    # -- resolution --------------------------------------------------------

    def _choose_population(
        self, entity_id: str, metric: str, leave_one_out: bool
    ) -> tuple[str, pd.Series, str]:
        """Pick sector or portfolio, and say why. Returns ``(level, ratios, reason)``."""
        sector_population = self.population(
            metric, entity_id=entity_id, level=SECTOR, exclude=leave_one_out
        )
        portfolio_population = self.population(
            metric, entity_id=entity_id, level=PORTFOLIO, exclude=leave_one_out
        )
        sector_n = len(sector_population)

        if self._config.benchmark_scope == assumptions.PORTFOLIO_ONLY:
            reason = "portfolio_scope_configured"
        elif sector_n >= MIN_SECTOR_SAMPLE_FOR_BENCHMARK:
            return (
                SECTOR,
                sector_population,
                f"sector_sample_{sector_n}_meets_minimum_{MIN_SECTOR_SAMPLE_FOR_BENCHMARK}",
            )
        else:
            reason = (
                f"sector_sample_{sector_n}_below_minimum_{MIN_SECTOR_SAMPLE_FOR_BENCHMARK}"
            )

        if len(portfolio_population) >= MIN_BENCHMARK_SAMPLE:
            return PORTFOLIO, portfolio_population, reason
        return (
            UNAVAILABLE,
            portfolio_population,
            f"{reason}; portfolio_sample_{len(portfolio_population)}_below_minimum_"
            f"{MIN_BENCHMARK_SAMPLE}",
        )

    def resolve(self, entity_id: str, metric: str) -> BenchmarkCoefficient:
        """The full coefficient record for one client x metric."""
        definition = self._metrics[metric]
        leave_one_out = self._config.leave_one_out
        level, population, reason = self._choose_population(entity_id, metric, leave_one_out)
        sector_candidates = self.population(
            metric, entity_id=entity_id, level=SECTOR, exclude=leave_one_out
        )

        if level == UNAVAILABLE:
            value = median = p75 = maximum = None
        else:
            value = float(population.quantile(definition.percentile))
            median = float(population.median())
            p75 = float(population.quantile(0.75))
            maximum = float(population.max())

        matches = self._entity_ids == entity_id
        return BenchmarkCoefficient(
            entity_id=entity_id,
            entity_name=str(self._entity_names[matches].iloc[0]),
            sector=self._sector_of(entity_id),
            metric=metric,
            product=definition.product,
            numerator=definition.numerator,
            denominator=definition.denominator,
            value=value,
            percentile=definition.percentile,
            benchmark_level=level,
            benchmark_n=int(len(population)),
            benchmark_value=value,
            benchmark_median=median,
            benchmark_p75=p75,
            benchmark_max=maximum,
            leave_one_out=leave_one_out,
            self_in_population=bool(
                not leave_one_out and entity_id in set(population.index)
            ),
            sector_candidate_n=int(len(sector_candidates)),
            fallback_reason=reason,
            sample_entities=tuple(str(value) for value in population.index),
        )

    # -- publication -------------------------------------------------------

    def coefficient_records(self) -> list[dict[str, Any]]:
        """Every client x metric coefficient, for ``model_benchmarks.parquet``."""
        records = []
        for metric, coefficients in self._coefficients.items():
            rationale = self._metrics[metric].rationale
            for coefficient in coefficients:
                row = coefficient.as_dict()
                row["rationale"] = rationale
                records.append(row)
        return records

    def metric_summary(self) -> list[dict[str, Any]]:
        """One row per metric describing the whole population, before exclusion.

        Published alongside the per-client coefficients so a reader can see the
        portfolio-wide picture without reconstructing it from twenty rows.
        """
        rows = []
        for metric, definition in self._metrics.items():
            ratios = self._ratios[metric]
            coefficients = self._coefficients[metric]
            levels = pd.Series([c.benchmark_level for c in coefficients])
            values = pd.Series(
                [c.value for c in coefficients if c.value is not None], dtype="float64"
            )
            rows.append(
                {
                    "metric": metric,
                    "product": definition.product,
                    "numerator": definition.numerator,
                    "denominator": definition.denominator,
                    "percentile": definition.percentile,
                    "population_n": int(len(ratios)),
                    "population_median": float(ratios.median()) if len(ratios) else None,
                    "population_p75": float(ratios.quantile(0.75)) if len(ratios) else None,
                    "population_max": float(ratios.max()) if len(ratios) else None,
                    "clients_on_sector_benchmark": int((levels == SECTOR).sum()),
                    "clients_on_portfolio_benchmark": int((levels == PORTFOLIO).sum()),
                    "clients_without_benchmark": int((levels == UNAVAILABLE).sum()),
                    "coefficient_min": float(values.min()) if len(values) else None,
                    "coefficient_max": float(values.max()) if len(values) else None,
                    "coefficient_spread": (
                        float(values.max() - values.min()) if len(values) else None
                    ),
                    "sample_entities": ", ".join(str(value) for value in ratios.index),
                    "basis": assumptions.PORTFOLIO_BENCHMARK,
                    "rationale": definition.rationale,
                }
            )
        return rows

    def levels(self, metric: str) -> pd.Series:
        """Benchmark level per client, aligned to the feature frame."""
        return pd.Series(
            [coefficient.benchmark_level for coefficient in self._coefficients[metric]],
            index=self._frame.index,
            dtype="object",
        )

    def sample_sizes(self, metric: str) -> pd.Series:
        """Benchmark sample size per client, aligned to the feature frame."""
        return pd.Series(
            [coefficient.benchmark_n for coefficient in self._coefficients[metric]],
            index=self._frame.index,
            dtype="int64",
        )

    def fallback_reasons(self, metric: str) -> pd.Series:
        """Why each client landed on the population it did."""
        return pd.Series(
            [coefficient.fallback_reason for coefficient in self._coefficients[metric]],
            index=self._frame.index,
            dtype="object",
        )

    def __contains__(self, metric: str) -> bool:
        return metric in self._metrics

    def __len__(self) -> int:
        return len(self._metrics)


def dominant_level(levels: list[pd.Series]) -> pd.Series:
    """Collapse several metrics' benchmark levels into one per client.

    A pillar built from three coefficients can be on a sector benchmark for one
    and the portfolio for another. The published ``benchmark_level`` is
    ``sector`` only when every contributing coefficient is, ``mixed`` when they
    disagree, and otherwise the level they share. Reporting "sector" when two of
    three legs came from the portfolio would overstate how peer-specific the
    estimate is.
    """
    if not levels:
        raise ValueError("no benchmark levels to collapse")
    frame = pd.concat(levels, axis=1)
    used = frame.where(frame != UNAVAILABLE)

    def _collapse(row: pd.Series) -> str:
        present = set(row.dropna())
        if not present:
            return UNAVAILABLE
        if len(present) == 1:
            return str(next(iter(present)))
        return "mixed"

    return used.apply(_collapse, axis=1)


def total_sample(sizes: list[pd.Series], levels: list[pd.Series]) -> pd.Series:
    """The largest sample behind any coefficient a client's estimate actually used."""
    if not sizes:
        raise ValueError("no benchmark sample sizes to collapse")
    size_frame = pd.concat(sizes, axis=1)
    level_frame = pd.concat(levels, axis=1)
    level_frame.columns = size_frame.columns
    used = size_frame.where(level_frame != UNAVAILABLE)
    return used.max(axis=1).astype("Int64")


# ---------------------------------------------------------------------------
# Driver resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedDriver:
    """A driver value per client, with provenance."""

    value: pd.Series
    source: pd.Series
    quality: pd.Series

    def is_disclosed(self) -> pd.Series:
        return self.source == "disclosed"


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

    Self-inclusion is impossible here: the ratios come only from clients that
    disclosed the field, and only clients that did not disclose it reach steps 2
    and 3.
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
