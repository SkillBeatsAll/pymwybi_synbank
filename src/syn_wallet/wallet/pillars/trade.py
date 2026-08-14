"""Pillar 3 -- Trade Finance.

Three sub-models, because a letter of credit, a performance guarantee and an
export collection are not the same product and do not answer to the same driver:

``import_documentary``
    Driven by the procurement base (cost of sales). Import LCs and import
    collections finance goods bought in.
``export_documentary``
    Driven by foreign revenue exposure. Export collections and export LCs
    finance goods sold out.
``guarantees``
    Driven by revenue scale. Performance bonds, customs and rental guarantees
    are not goods trade at all, which is why this component survives in sectors
    where the other two are switched off.

**Sector treatment is real, not cosmetic.** For insurance and real estate the
import and export sub-models are *suppressed entirely* -- an insurer buys no
goods and a property group holds no tradeable inventory, so their cost of sales
and inventory are not trade-finance drivers. Only the guarantee sub-model
applies, and applicability drops to 0.30, which pushes confidence into LOW. This
is what stops an insurer being scored for import letters of credit alongside a
mining house.

**The numerator.** Instruments dated inside the fiscal year, across all four
statuses, because the denominator is annual issuance demand. This is the one
place the four statuses are legitimately summed and it is stated rather than
assumed. The live book -- active plus issued, roughly half the value -- is
reported alongside, never inside.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import assumptions, benchmarks, common, confidence

PRODUCT = assumptions.TRADE

IMPORT_BENCHMARK = "trade_import_per_cost_of_sales"
EXPORT_BENCHMARK = "trade_export_per_foreign_revenue"
GUARANTEE_BENCHMARK = "trade_guarantees_per_revenue"


def _explain(row: pd.Series) -> str:
    pieces = []
    if pd.notna(row["import_component"]):
        pieces.append(
            f"import documentary {common.zar(row['import_component'])} "
            f"(cost of sales {common.zar(row['cogs_basis_zar'])}, "
            f"{row['cogs_source'].replace('_', ' ')}, at peer intensity "
            f"{row['import_intensity']:.4f})"
        )
    if pd.notna(row["export_component"]):
        pieces.append(
            f"export documentary {common.zar(row['export_component'])} "
            f"(foreign revenue {common.zar(row['foreign_revenue_basis_zar'])}, "
            f"{row['foreign_revenue_source'].replace('_', ' ')}, at peer intensity "
            f"{row['export_intensity']:.4f})"
        )
    if pd.notna(row["guarantee_component"]):
        pieces.append(
            f"guarantees {common.zar(row['guarantee_component'])} "
            f"(revenue {common.zar(row['revenue_total_zar'])} at peer intensity "
            f"{row['guarantee_intensity']:.4f})"
        )
    body = " + ".join(pieces) if pieces else "no component applicable"
    sentence = (
        f"Peer-benchmark addressable trade-finance issuance {common.zar(row['estimate_zar'])} "
        f"= {body}."
    )
    if row["suppressed"]:
        sentence += (
            f" Import and export documentary sub-models are suppressed for {row['sector']}: "
            "no goods are bought or sold, so cost of sales and inventory are not trade drivers."
        )
    sentence += (
        f" Syn Bank issued {common.zar(row['observed_zar'])} across "
        f"{row['tf_instrument_count_fy']:,.0f} instruments in {row['fy_label']} "
        f"(live book {common.zar(row['tf_live_value_zar_fy'])}, weighted tenor "
        f"{row['tf_weighted_avg_tenor_days_fy']:.0f} days)"
    )
    if pd.notna(row["share"]):
        sentence += f", a share of {common.pct(row['share'])}."
    else:
        sentence += "."
    if row["floored"]:
        sentence += (
            f" The driver model produced only {common.zar(row['estimate_modelled_zar'])}, below "
            "observed issuance, so the wallet was floored at what Syn Bank already writes. No "
            "headroom can be demonstrated for this client from disclosed drivers."
        )
    return sentence


def build(frame: pd.DataFrame) -> common.PillarOutput:
    """Estimate the trade-finance wallet for every client."""
    index = frame.index
    work = frame.copy()

    rules = work["sector"].map(lambda sector: assumptions.sector_rule(PRODUCT, sector))
    applicability = rules.map(lambda rule: rule.applicability).astype("float64")
    suppress_import = rules.map(lambda rule: "import_documentary" in rule.suppress_components)
    suppress_export = rules.map(lambda rule: "export_documentary" in rule.suppress_components)
    goods_trading = ~(suppress_import | suppress_export)

    # --- Drivers ---------------------------------------------------------
    revenue = pd.to_numeric(work["revenue_total_zar"], errors="coerce")

    cogs_comparable = work["sector"].isin(assumptions.COGS_COMPARABLE_SECTORS)
    sector_cogs, portfolio_cogs, _ = benchmarks.sector_ratio_table(
        work, "cost_of_sales_zar", "revenue_total_zar", eligible=cogs_comparable
    )
    cogs = benchmarks.resolve_driver(
        work,
        "cost_of_sales_zar",
        "revenue_total_zar",
        sector_cogs,
        portfolio_cogs,
        allow_imputation=cogs_comparable,
    )
    sector_foreign, portfolio_foreign, _ = benchmarks.sector_ratio_table(
        work, "revenue_foreign_zar", "revenue_total_zar"
    )
    foreign_revenue = benchmarks.resolve_driver(
        work, "revenue_foreign_zar", "revenue_total_zar", sector_foreign, portfolio_foreign
    )

    # --- Benchmarks, measured on goods-trading clients only ---------------
    benchmark_set = benchmarks.BenchmarkSet()
    import_benchmark = benchmark_set.add(
        benchmarks.measure_benchmark(
            work,
            IMPORT_BENCHMARK,
            PRODUCT,
            "tf_import_value_zar_fy",
            "cost_of_sales_zar",
            "Import instrument issuance per rand of disclosed cost of sales, at the portfolio "
            "upper quartile. Measured only on goods-trading sectors that disclose cost of sales, "
            "so an insurer's cost line cannot set a mining client's benchmark.",
            eligible=cogs_comparable & goods_trading,
        )
    )
    export_benchmark = benchmark_set.add(
        benchmarks.measure_benchmark(
            work,
            EXPORT_BENCHMARK,
            PRODUCT,
            "tf_export_value_zar_fy",
            "revenue_foreign_zar",
            "Export instrument issuance per rand of disclosed foreign revenue, at the portfolio "
            "upper quartile. Measured only on goods-trading clients that disclose foreign "
            "revenue.",
            eligible=goods_trading,
        )
    )
    guarantee_benchmark = benchmark_set.add(
        benchmarks.measure_benchmark(
            work,
            GUARANTEE_BENCHMARK,
            PRODUCT,
            "tf_guarantees_value_zar_fy",
            "revenue_total_zar",
            "Guarantee issuance per rand of revenue, at the portfolio upper quartile. Measured "
            "across every sector because performance bonds, customs guarantees and rental "
            "deposits are not goods trade and apply to financial and property groups too.",
        )
    )

    import_intensity = import_benchmark.value
    export_intensity = export_benchmark.value
    guarantee_intensity = guarantee_benchmark.value
    nan_series = pd.Series(np.nan, index=index, dtype="float64")

    import_component = (
        (cogs.value * import_intensity) if import_intensity is not None else nan_series
    ).where(~suppress_import, np.nan)
    export_component = (
        (foreign_revenue.value * export_intensity) if export_intensity is not None else nan_series
    ).where(~suppress_export, np.nan)
    guarantee_component = (
        (revenue * guarantee_intensity) if guarantee_intensity is not None else nan_series
    )

    modelled_estimate = pd.concat(
        [import_component, export_component, guarantee_component], axis=1
    ).sum(axis=1, min_count=1)

    # --- Observed: annual issuance, all four statuses ---------------------
    observed = pd.to_numeric(work["tf_total_value_zar_fy"], errors="coerce")
    estimate, floored = common.apply_observed_floor(modelled_estimate, observed)
    share_result = common.share_and_gap(observed, estimate)

    # --- Confidence -------------------------------------------------------
    required = pd.Series(1.0, index=index, dtype="float64")  # revenue, always disclosed
    disclosed = work["revenue_total_zar"].notna().astype("float64")
    required = required + (~suppress_import).astype("float64") + (~suppress_export).astype("float64")
    disclosed = (
        disclosed
        + ((cogs.source == "disclosed") & ~suppress_import).astype("float64")
        + ((foreign_revenue.source == "disclosed") & ~suppress_export).astype("float64")
    )
    completeness = disclosed / required

    benchmark_directness = confidence.BASIS_DIRECTNESS[assumptions.PORTFOLIO_BENCHMARK]
    weight_import = import_component.fillna(0.0)
    weight_export = export_component.fillna(0.0)
    weight_guarantee = guarantee_component.fillna(0.0)
    total_weight = (weight_import + weight_export + weight_guarantee).replace(0.0, np.nan)
    # Expected components exclude the ones this sector deliberately suppresses:
    # an insurer is not penalised for lacking an import sub-model it should
    # never have had.
    expected_components = (
        1.0 + (~suppress_import).astype("float64") + (~suppress_export).astype("float64")
    )
    directness = confidence.effective_directness(
        (
            weight_import * benchmark_directness * cogs.quality
            + weight_export * benchmark_directness * foreign_revenue.quality
            + weight_guarantee * benchmark_directness * 1.0
        )
        / total_weight,
        import_component.notna().astype("float64")
        + export_component.notna().astype("float64")
        + guarantee_component.notna().astype("float64"),
        expected_components,
        floored,
    )

    observation = confidence.observation_support(
        pd.to_numeric(work["tf_instrument_count_fy"], errors="coerce")
    )
    consistency = confidence.internal_consistency(
        work, uses_revenue=True, uses_revenue_split=True
    )
    scored = confidence.score(completeness, directness, applicability, observation, consistency)

    # --- Diagnostics ------------------------------------------------------
    flags = common.FlagSet(index)
    flags.add("goods_trade_submodels_suppressed", suppress_import | suppress_export)
    flags.add("cogs_imputed", cogs.source.isin(["sector_benchmark", "portfolio_benchmark"]) & ~suppress_import)
    flags.add(
        "foreign_revenue_imputed",
        foreign_revenue.source.isin(["sector_benchmark", "portfolio_benchmark"]) & ~suppress_export,
    )
    flags.add("wallet_floored_at_observed", floored)
    flags.add("observed_exceeds_estimate", share_result.flags == "observed_exceeds_estimate")
    flags.add("thin_instrument_evidence", pd.to_numeric(work["tf_instrument_count_fy"], errors="coerce") < 50)
    flags.add(
        "estimate_is_guarantees_only",
        guarantee_component.notna() & import_component.isna() & export_component.isna(),
    )

    explain_frame = pd.DataFrame(
        {
            "estimate_zar": estimate,
            "import_component": import_component,
            "export_component": export_component,
            "guarantee_component": guarantee_component,
            "cogs_basis_zar": cogs.value,
            "cogs_source": cogs.source,
            "foreign_revenue_basis_zar": foreign_revenue.value,
            "foreign_revenue_source": foreign_revenue.source,
            "revenue_total_zar": revenue,
            "import_intensity": import_intensity if import_intensity is not None else np.nan,
            "export_intensity": export_intensity if export_intensity is not None else np.nan,
            "guarantee_intensity": (
                guarantee_intensity if guarantee_intensity is not None else np.nan
            ),
            "observed_zar": observed,
            "share": share_result.share,
            "fy_label": work["fy_label"],
            "sector": work["sector"],
            "suppressed": suppress_import | suppress_export,
            "tf_instrument_count_fy": work["tf_instrument_count_fy"],
            "tf_live_value_zar_fy": pd.to_numeric(work["tf_live_value_zar_fy"], errors="coerce"),
            "tf_weighted_avg_tenor_days_fy": work["tf_weighted_avg_tenor_days_fy"],
            "floored": floored,
            "estimate_modelled_zar": modelled_estimate,
        }
    )
    explanation = explain_frame.apply(_explain, axis=1)

    estimates = common.assemble(
        work,
        PRODUCT,
        observed,
        estimate,
        share_result,
        scored.score,
        scored.band,
        explanation,
        flags.series(),
        estimate_kind="addressable_wallet",
        estimate_modelled=modelled_estimate,
    )

    components = common.component_rows(
        work,
        PRODUCT,
        {
            "import_documentary": import_component,
            "export_documentary": export_component,
            "guarantees": guarantee_component,
        },
        {
            "import_documentary": (cogs.value, cogs.source),
            "export_documentary": (foreign_revenue.value, foreign_revenue.source),
            "guarantees": (revenue, pd.Series("disclosed", index=index)),
        },
    )

    detail = scored.detail.copy()
    detail["entity_id"] = work["entity_id"]
    detail["product"] = PRODUCT

    flag_frame = flags.as_frame()
    flag_frame["entity_id"] = work["entity_id"]
    flag_frame["product"] = PRODUCT

    return common.PillarOutput(
        estimates, components, detail, flag_frame, benchmark_set.as_records()
    )
