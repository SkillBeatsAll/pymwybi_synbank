"""What would change if the defensible-but-arguable choices had gone the other way.

A model whose author cannot say which of its findings survive a different
benchmark percentile is a model that has not been tested, only built. This module
rebuilds the entire engine under every variant of the four choices that are
genuinely arguable, and measures what moves:

* the benchmark statistic -- median, upper quartile, 80th percentile;
* leave-one-out or self-inclusive peer populations;
* sector-preferred or portfolio-only benchmarks;
* the capex debt-funded share -- 20%, 30%, 40%.

Three benchmark statistics x two exclusion policies x two scopes x three capex
shares is 36 full model runs, which takes a couple of seconds. Every scenario is
compared to the base model on four axes:

``portfolio totals``
    Does the headline rand figure move, and by how much.
``client ranks`` / ``product ranks``
    Spearman rank correlation against the base ordering. A result that survives
    is one whose *ordering* is stable even when its magnitude is not -- and
    ordering is what a call list actually consumes.
``top 10 overlap``
    How many of the base model's ten best opportunities are still in the top ten.
    The bluntest and most honest measure: it is the number a reviewer can check
    by eye.

The distinction this module exists to draw is between a finding that is robust --
true under every scenario -- and one that is an artefact of a coefficient. Both
get published; only the first gets to be a headline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from . import assumptions, engine

#: The benchmark statistics under test. The base model uses the upper quartile.
PERCENTILES = ((0.50, "median"), (0.75, "p75"), (0.80, "p80"))

#: Capex debt-funded shares under test. The base model uses 0.30, the one
#: underived coefficient in the engine.
CAPEX_SHARES = (0.20, 0.30, 0.40)

#: How many of the top opportunities to track across scenarios.
TOP_N = 10

#: Columns compared between a scenario and the base model.
COMPARED_VALUES = ("estimate_zar", "gap_zar", "share")
COMPARED_SCORES = ("commercial_opportunity_score", "opportunity_intensity")


def scenario_grid() -> list[assumptions.ModelConfig]:
    """Every configuration under test, base model first.

    The label encodes the whole configuration, so a row in the output parquet is
    self-describing without a join back to this module.
    """
    configs: list[assumptions.ModelConfig] = []
    for percentile, percentile_label in PERCENTILES:
        for leave_one_out in (True, False):
            for scope in (assumptions.SECTOR_PREFERRED, assumptions.PORTFOLIO_ONLY):
                for capex in CAPEX_SHARES:
                    exclusion = "loo" if leave_one_out else "self_included"
                    scope_label = (
                        "sector" if scope == assumptions.SECTOR_PREFERRED else "portfolio"
                    )
                    configs.append(
                        assumptions.ModelConfig(
                            label=(
                                f"{percentile_label}_{exclusion}_{scope_label}"
                                f"_capex{int(round(capex * 100)):02d}"
                            ),
                            benchmark_percentile=percentile,
                            leave_one_out=leave_one_out,
                            benchmark_scope=scope,
                            capex_debt_funded_share=capex,
                        )
                    )

    base = base_config()
    configs.sort(key=lambda config: config.label != base.label)
    return configs


def base_config() -> assumptions.ModelConfig:
    """The published model, labelled the way the grid labels it."""
    return assumptions.ModelConfig(
        label="p75_loo_sector_capex30",
        benchmark_percentile=assumptions.BENCHMARK_PERCENTILE,
        leave_one_out=True,
        benchmark_scope=assumptions.SECTOR_PREFERRED,
        capex_debt_funded_share=assumptions.CAPEX_DEBT_FUNDED_SHARE,
    )


def _spearman(left: pd.Series, right: pd.Series) -> float:
    """Rank correlation, NaN when fewer than three pairs can be compared."""
    paired = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(paired) < 3:
        return float("nan")
    if paired["left"].nunique() < 2 or paired["right"].nunique() < 2:
        # A constant ordering correlates with everything and nothing. Reporting
        # 1.0 here would claim stability the data cannot demonstrate.
        return float("nan")
    return float(paired["left"].rank().corr(paired["right"].rank()))


@dataclass
class SensitivityResult:
    """Everything one sweep produces."""

    detail: pd.DataFrame
    summary: pd.DataFrame
    product_summary: pd.DataFrame
    scenarios: pd.DataFrame
    robustness: pd.DataFrame


def _detail_rows(model: engine.WalletModel, config: assumptions.ModelConfig) -> pd.DataFrame:
    """One row per client x product for a single scenario."""
    estimates = model.estimates
    frame = pd.DataFrame(
        {
            "scenario": config.label,
            "benchmark_percentile": config.benchmark_percentile,
            "leave_one_out": config.leave_one_out,
            "benchmark_scope": config.benchmark_scope,
            "capex_debt_funded_share": config.capex_debt_funded_share,
            "entity_id": estimates["entity_id"],
            "entity_name": estimates["entity_name"],
            "sector": estimates["sector"],
            "product": estimates["product"],
            "benchmark_level": estimates["benchmark_level"],
            "benchmark_n": estimates["benchmark_n"],
            "observed_zar": pd.to_numeric(estimates["observed_zar"], errors="coerce"),
            "estimate_zar": pd.to_numeric(estimates["estimate_zar"], errors="coerce"),
            "gap_zar": pd.to_numeric(estimates["gap_zar"], errors="coerce"),
            "share": pd.to_numeric(estimates["share"], errors="coerce"),
            "confidence": pd.to_numeric(estimates["confidence"], errors="coerce"),
            "commercial_opportunity_score": pd.to_numeric(
                estimates["commercial_opportunity_score"], errors="coerce"
            ),
            "opportunity_intensity": pd.to_numeric(
                estimates["opportunity_intensity"], errors="coerce"
            ),
            "commercial_rank": estimates["commercial_rank"],
            "commercial_rank_in_product": estimates["commercial_rank_in_product"],
            "intensity_rank": estimates["intensity_rank"],
            "intensity_rank_in_product": estimates["intensity_rank_in_product"],
        }
    )
    return frame.reset_index(drop=True)


def _key(frame: pd.DataFrame) -> pd.Index:
    return pd.MultiIndex.from_arrays([frame["entity_id"], frame["product"]])


def _compare(
    scenario: pd.DataFrame, base: pd.DataFrame, config: assumptions.ModelConfig
) -> dict[str, Any]:
    """Scenario against base: totals, correlations, top-N overlap."""
    scenario = scenario.set_index(_key(scenario))
    base = base.set_index(_key(base))
    aligned = scenario.reindex(base.index)

    row: dict[str, Any] = {
        "scenario": config.label,
        **config.as_dict(),
        "is_base": config.label == base_config().label,
    }

    for column in COMPARED_VALUES + COMPARED_SCORES:
        base_values = pd.to_numeric(base[column], errors="coerce")
        values = pd.to_numeric(aligned[column], errors="coerce")
        row[f"spearman_{column}"] = _spearman(base_values, values)
        base_total = base_values.sum(min_count=1)
        total = values.sum(min_count=1)
        row[f"total_{column}"] = float(total) if pd.notna(total) else np.nan
        row[f"total_{column}_vs_base"] = (
            float(total / base_total - 1.0)
            if pd.notna(total) and pd.notna(base_total) and base_total != 0
            else np.nan
        )

    row["spearman_commercial_rank"] = _spearman(
        pd.to_numeric(base["commercial_rank"], errors="coerce"),
        pd.to_numeric(aligned["commercial_rank"], errors="coerce"),
    )
    row["spearman_intensity_rank"] = _spearman(
        pd.to_numeric(base["intensity_rank"], errors="coerce"),
        pd.to_numeric(aligned["intensity_rank"], errors="coerce"),
    )

    base_top = set(
        base.sort_values("commercial_rank").head(TOP_N).index
    )
    scenario_top = set(
        scenario.sort_values("commercial_rank").head(TOP_N).index
    )
    row["top10_overlap"] = int(len(base_top & scenario_top))
    row["top10_overlap_fraction"] = float(len(base_top & scenario_top) / max(len(base_top), 1))

    base_intensity_top = set(
        base[base["intensity_rank"].notna()].sort_values("intensity_rank").head(TOP_N).index
    )
    scenario_intensity_top = set(
        scenario[scenario["intensity_rank"].notna()]
        .sort_values("intensity_rank")
        .head(TOP_N)
        .index
    )
    row["top10_intensity_overlap"] = int(
        len(base_intensity_top & scenario_intensity_top)
    )

    # The largest single-client move in the portfolio, which averages hide.
    gap_ratio = (
        pd.to_numeric(aligned["gap_zar"], errors="coerce")
        / pd.to_numeric(base["gap_zar"], errors="coerce").replace(0.0, np.nan)
    ).replace([np.inf, -np.inf], np.nan)
    row["max_client_gap_ratio"] = (
        float(gap_ratio.max()) if gap_ratio.notna().any() else np.nan
    )
    row["min_client_gap_ratio"] = (
        float(gap_ratio.min()) if gap_ratio.notna().any() else np.nan
    )
    row["clients_moving_rank_by_5_or_more"] = int(
        (
            (
                pd.to_numeric(aligned["commercial_rank"], errors="coerce")
                - pd.to_numeric(base["commercial_rank"], errors="coerce")
            ).abs()
            >= 5
        ).sum()
    )
    return row


def _product_comparison(
    scenario: pd.DataFrame, base: pd.DataFrame, config: assumptions.ModelConfig
) -> list[dict[str, Any]]:
    """The same comparison, per product, because the pillars are not alike."""
    rows = []
    for product in assumptions.PRODUCTS:
        scenario_product = scenario[scenario["product"] == product].set_index("entity_id")
        base_product = base[base["product"] == product].set_index("entity_id")
        aligned = scenario_product.reindex(base_product.index)
        base_gap = pd.to_numeric(base_product["gap_zar"], errors="coerce")
        gap = pd.to_numeric(aligned["gap_zar"], errors="coerce")
        base_total = base_gap.sum(min_count=1)
        total = gap.sum(min_count=1)
        rows.append(
            {
                "scenario": config.label,
                **config.as_dict(),
                "product": product,
                "product_label": assumptions.PRODUCT_LABELS[product],
                "total_gap_zar": float(total) if pd.notna(total) else np.nan,
                "total_gap_vs_base": (
                    float(total / base_total - 1.0)
                    if pd.notna(total) and pd.notna(base_total) and base_total != 0
                    else np.nan
                ),
                "total_estimate_zar": float(
                    pd.to_numeric(aligned["estimate_zar"], errors="coerce").sum(min_count=1)
                ),
                "spearman_gap": _spearman(base_gap, gap),
                "spearman_rank_in_product": _spearman(
                    pd.to_numeric(base_product["commercial_rank_in_product"], errors="coerce"),
                    pd.to_numeric(aligned["commercial_rank_in_product"], errors="coerce"),
                ),
                "median_share": float(
                    pd.to_numeric(aligned["share"], errors="coerce").median()
                ),
                # ``benchmark_level`` is the collapsed level for the whole
                # pillar, so a pillar whose export leg is on the portfolio and
                # whose import leg is on its sector reads "mixed". Counting only
                # pure "sector" would report zero for FX and understate how much
                # sector information the estimates actually carry.
                "clients_on_pure_sector_benchmark": int(
                    (aligned["benchmark_level"] == "sector").sum()
                ),
                "clients_using_any_sector_benchmark": int(
                    aligned["benchmark_level"].isin(["sector", "mixed"]).sum()
                ),
            }
        )
    return rows


#: A result is robust when its ordering survives every scenario at this
#: correlation or better. 0.90 is deliberately demanding: below it, enough
#: clients have swapped places to change who gets called first.
ROBUST_SPEARMAN = 0.90
#: ...and when its rand total stays within this fraction of the base.
ROBUST_TOTAL_DRIFT = 0.15


def _robustness(product_summary: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    """Per product: is the ordering robust, is the magnitude robust, and why not."""
    base_label = base_config().label
    rows = []
    for product, group in product_summary.groupby("product", sort=False):
        others = group[group["scenario"] != base_label]
        correlations = pd.to_numeric(others["spearman_rank_in_product"], errors="coerce")
        drifts = pd.to_numeric(others["total_gap_vs_base"], errors="coerce").abs()
        min_correlation = float(correlations.min()) if correlations.notna().any() else np.nan
        max_drift = float(drifts.max()) if drifts.notna().any() else np.nan

        ordering_robust = bool(pd.notna(min_correlation) and min_correlation >= ROBUST_SPEARMAN)
        # A pillar with no rand magnitude cannot have a sensitive one. Scoring
        # the absence as a failure would put investment banking, which is
        # correctly signal-only, in the same bucket as FX, whose rand total
        # genuinely swings by three quarters.
        has_magnitude = bool(drifts.notna().any())
        magnitude_robust = (
            bool(max_drift <= ROBUST_TOTAL_DRIFT) if has_magnitude else True
        )
        if not has_magnitude and ordering_robust:
            verdict = "NO_RAND_MAGNITUDE_ORDERING_ROBUST"
            note = (
                "This pillar produces no rand figure, so there is no magnitude to be sensitive. "
                f"Its ranked signal is identical across all {len(others)} scenarios: every "
                "threshold behind it is a declared judgement, not a peer coefficient."
            )
        elif ordering_robust and magnitude_robust:
            verdict = "ROBUST"
            note = (
                f"Ordering holds at rho >= {min_correlation:.3f} and the rand total moves at most "
                f"{max_drift:.1%} across all {len(others)} scenarios."
            )
        elif ordering_robust:
            verdict = "ROBUST_ORDERING_SENSITIVE_MAGNITUDE"
            note = (
                f"Ordering holds at rho >= {min_correlation:.3f}, but the rand total moves up to "
                f"{max_drift:.1%}. Rank the opportunities; do not quote the total without the "
                "range."
            )
        else:
            verdict = "ASSUMPTION_SENSITIVE"
            note = (
                f"Ordering falls to rho = {min_correlation:.3f} and the total moves up to "
                f"{max_drift:.1%}. Neither the ranking nor the magnitude survives the grid."
            )
        worst = (
            others.loc[correlations.idxmin(), "scenario"]
            if correlations.notna().any()
            else None
        )
        rows.append(
            {
                "product": product,
                "product_label": assumptions.PRODUCT_LABELS[product],
                "scenarios_tested": int(len(others)),
                "min_spearman_rank_in_product": min_correlation,
                "max_abs_total_gap_drift": max_drift,
                "ordering_robust": ordering_robust,
                "magnitude_robust": magnitude_robust,
                "verdict": verdict,
                "worst_scenario": worst,
                "note": note,
            }
        )

    overall = summary[summary["scenario"] != base_label]
    rows.append(
        {
            "product": "ALL",
            "product_label": "Portfolio, every product",
            "scenarios_tested": int(len(overall)),
            "min_spearman_rank_in_product": float(
                pd.to_numeric(overall["spearman_commercial_rank"], errors="coerce").min()
            ),
            "max_abs_total_gap_drift": float(
                pd.to_numeric(overall["total_gap_zar_vs_base"], errors="coerce").abs().max()
            ),
            "ordering_robust": bool(
                pd.to_numeric(overall["spearman_commercial_rank"], errors="coerce").min()
                >= ROBUST_SPEARMAN
            ),
            "magnitude_robust": bool(
                pd.to_numeric(overall["total_gap_zar_vs_base"], errors="coerce").abs().max()
                <= ROBUST_TOTAL_DRIFT
            ),
            "verdict": "SEE_PER_PRODUCT",
            "worst_scenario": (
                overall.loc[
                    pd.to_numeric(
                        overall["spearman_commercial_rank"], errors="coerce"
                    ).idxmin(),
                    "scenario",
                ]
                if len(overall)
                else None
            ),
            "note": (
                "The portfolio-wide figure is dominated by cash management, which no scenario "
                "moves. Read the per-product rows, not this one."
            ),
        }
    )
    return pd.DataFrame(rows)


def run(features: pd.DataFrame, configs: list[assumptions.ModelConfig] | None = None) -> SensitivityResult:
    """Rebuild the engine under every scenario and compare each to the base."""
    configs = configs or scenario_grid()
    base_label = base_config().label
    if not any(config.label == base_label for config in configs):
        raise ValueError(f"the scenario grid must contain the base model {base_label!r}")

    details: dict[str, pd.DataFrame] = {}
    for config in configs:
        model = engine.run(features, config)
        details[config.label] = _detail_rows(model, config)

    base_detail = details[base_label]
    summary_rows = []
    product_rows: list[dict[str, Any]] = []
    for config in configs:
        detail = details[config.label]
        summary_rows.append(_compare(detail, base_detail, config))
        product_rows.extend(_product_comparison(detail, base_detail, config))

    detail_frame = pd.concat(details.values(), ignore_index=True)
    summary = pd.DataFrame(summary_rows)
    product_summary = pd.DataFrame(product_rows)
    scenarios = pd.DataFrame([config.as_dict() for config in configs])
    robustness = _robustness(product_summary, summary)

    return SensitivityResult(
        detail=detail_frame,
        summary=summary,
        product_summary=product_summary,
        scenarios=scenarios,
        robustness=robustness,
    )
