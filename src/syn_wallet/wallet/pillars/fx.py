"""Pillar 2 -- FX / Global Markets.

**Foreign revenue is not FX volume.** MTN's Nigerian revenue is earned and
collected in naira inside Nigeria; it never crosses a border as a payment. So
the model never sets cross-border demand equal to foreign revenue. Foreign
revenue is used as an *exposure* variable, and the conversion from exposure to
settlement volume is measured from the portfolio -- the cross-border intensity a
well-penetrated peer achieves per rand of foreign revenue.

Three components, split by direction so nothing is counted twice:

``export_settlement``
    Foreign revenue exposure x the upper-quartile inbound cross-border intensity
    of the client's **peers**.
``import_settlement``
    Cost-of-sales exposure x the upper-quartile outbound cross-border intensity
    of the client's peers.
``hedging_execution``
    Disclosed FX forward notional, executed once. Corporates roll shorter-dated
    hedges several times a year, but no forward tenor is disclosed, so a single
    roll is a deliberate floor.

**Peers, not the portfolio including the client.** Both intensities are resolved
per client: the client is excluded from the population that sets its own
coefficient, and a sector population is used where the sector has at least three
other contributors. Vodacom's cross-border intensity no longer helps define the
benchmark Vodacom is measured against, which is what made the v1.0.0 FX gap
partly self-referential.

**The SWIFT overlap.** ``txn_swift_channel_volume_zar`` is never added to the FX
numerator. Those rows sit in the transactional ledger and conceptually overlap
cross-border payments, but the supplied fields carry no product-lineage key, so
the overlap cannot be resolved. Adding them would double count; the cash pillar
already excludes them from its own numerator. The result is conservative in both
directions: the ambiguous volume is claimed by neither pillar, and it is
published per client so the size of the ambiguity is visible.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import assumptions, benchmarks, common, confidence

PRODUCT = assumptions.FX

EXPORT_BENCHMARK = "fx_inbound_per_foreign_revenue"
IMPORT_BENCHMARK = "fx_outbound_per_cost_of_sales"


def _explain(row: pd.Series) -> str:
    pieces = []
    if pd.notna(row["export_component"]):
        pieces.append(
            f"export settlement {common.zar(row['export_component'])} "
            f"(foreign revenue exposure {common.zar(row['foreign_revenue_basis_zar'])}, "
            f"{row['foreign_revenue_source'].replace('_', ' ')}, at the upper-quartile inbound "
            f"intensity of {row['export_intensity']:.3f} measured across "
            f"{row['export_benchmark_n']:,.0f} {row['export_benchmark_level']} peers, this client "
            f"excluded)"
        )
    if pd.notna(row["import_component"]):
        pieces.append(
            f"import settlement {common.zar(row['import_component'])} "
            f"(cost of sales {common.zar(row['cogs_basis_zar'])}, "
            f"{row['cogs_source'].replace('_', ' ')}, at outbound intensity "
            f"{row['import_intensity']:.3f} across {row['import_benchmark_n']:,.0f} "
            f"{row['import_benchmark_level']} peers, this client excluded)"
        )
    if pd.notna(row["hedging_component"]) and row["hedging_component"] > 0:
        pieces.append(
            f"hedging execution {common.zar(row['hedging_component'])} "
            "(disclosed forward notional, one roll)"
        )
    body = " + ".join(pieces) if pieces else "no component estimable from disclosed exposure"
    sentence = f"Peer-benchmark addressable FX volume {common.zar(row['estimate_zar'])} = {body}."
    sentence += (
        f" Syn Bank routed {common.zar(row['observed_zar'])} of cross-border payments in "
        f"{row['fy_label']} across {row['xb_active_countries_fy']:,.0f} countries and "
        f"{row['xb_active_currency_pairs_fy']:,.0f} currency pairs"
    )
    if pd.notna(row["share"]):
        sentence += f", a share of {common.pct(row['share'])}."
    else:
        sentence += "."
    sentence += (
        f" {common.zar(row['overlap_excluded_zar'])} of SWIFT-channel transactional volume was "
        "deliberately not added here: it overlaps this pillar by an unresolvable amount."
    )
    if row["floored"]:
        sentence += (
            f" The exposure model produced only {common.zar(row['estimate_modelled_zar'])}, below "
            "observed activity, so the wallet was floored at what already flows. The disclosed "
            "exposure drivers do not describe this client's cross-border business; no headroom "
            "can be demonstrated and none is claimed."
        )
    return sentence


def build(
    frame: pd.DataFrame, config: assumptions.ModelConfig | None = None
) -> common.PillarOutput:
    """Estimate the FX / global-markets wallet for every client."""
    index = frame.index
    work = frame.copy()
    config = config or assumptions.BASE_CONFIG

    # --- Drivers ---------------------------------------------------------
    revenue = pd.to_numeric(work["revenue_total_zar"], errors="coerce")
    sector_foreign, portfolio_foreign, _ = benchmarks.sector_ratio_table(
        work, "revenue_foreign_zar", "revenue_total_zar"
    )
    foreign_revenue = benchmarks.resolve_driver(
        work, "revenue_foreign_zar", "revenue_total_zar", sector_foreign, portfolio_foreign
    )

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

    # --- Benchmarks, per client, measured only where the exposure is disclosed
    peers = benchmarks.PeerBenchmarks(work, config)
    export_intensity = peers.register(
        EXPORT_BENCHMARK,
        PRODUCT,
        "xb_inbound_volume_zar_fy",
        "revenue_foreign_zar",
        "Inbound cross-border volume per rand of disclosed foreign revenue. Converts an "
        "exposure measure into a settlement-volume expectation without asserting that "
        "foreign revenue itself crosses a border. Measured only on clients that disclose "
        "foreign revenue, so an imputed exposure never sets the benchmark, and never on the "
        "client being estimated.",
    )
    import_intensity = peers.register(
        IMPORT_BENCHMARK,
        PRODUCT,
        "xb_outbound_volume_zar_fy",
        "cost_of_sales_zar",
        "Outbound cross-border volume per rand of disclosed cost of sales. Sized on the "
        "procurement base because outbound cross-border payment is import settlement. "
        "Measured only on clients disclosing cost of sales in a sector where cost of sales "
        "is a procurement proxy.",
        eligible=cogs_comparable,
    )

    export_component = foreign_revenue.value * export_intensity
    import_component = cogs.value * import_intensity
    hedging_component = (
        pd.to_numeric(work["fx_forward_notional_zar"], errors="coerce")
        * assumptions.FX_FORWARD_ROLLS_PER_YEAR
    )

    nan_series = pd.Series(np.nan, index=index, dtype="float64")

    modelled_estimate = pd.concat(
        [export_component, import_component, hedging_component], axis=1
    ).sum(axis=1, min_count=1)

    # --- Observed ---------------------------------------------------------
    observed = pd.to_numeric(work["xb_total_volume_zar_fy"], errors="coerce")
    overlap_excluded = pd.to_numeric(work["txn_swift_channel_volume_zar_fy"], errors="coerce")

    estimate, floored = common.apply_observed_floor(modelled_estimate, observed)
    share_result = common.share_and_gap(observed, estimate)

    # --- Confidence -------------------------------------------------------
    rules = work["sector"].map(lambda sector: assumptions.sector_rule(PRODUCT, sector))
    applicability = rules.map(lambda rule: rule.applicability).astype("float64")

    disclosed_inputs = (
        (foreign_revenue.source == "disclosed").astype("float64")
        + (cogs.source == "disclosed").astype("float64")
        + work["fx_forward_notional_zar"].notna().astype("float64")
    )
    completeness = disclosed_inputs / 3.0

    benchmark_directness = confidence.BASIS_DIRECTNESS[assumptions.PORTFOLIO_BENCHMARK]
    structural_directness = confidence.BASIS_DIRECTNESS[assumptions.STRUCTURAL]
    weight_export = export_component.fillna(0.0)
    weight_import = import_component.fillna(0.0)
    weight_hedge = hedging_component.fillna(0.0)
    total_weight = (weight_export + weight_import + weight_hedge).replace(0.0, np.nan)
    directness = confidence.effective_directness(
        (
            weight_export * benchmark_directness * foreign_revenue.quality
            + weight_import * benchmark_directness * cogs.quality
            + weight_hedge * structural_directness * 1.0
        )
        / total_weight,
        # Hedging is an additive bonus, not a required component: 14 of 20
        # clients disclose no forward notional at all, and its absence means the
        # client published nothing, not that the settlement model failed. Only
        # the two settlement components count towards method completeness, and
        # the missing disclosure is already carried by input_completeness.
        export_component.notna().astype("float64")
        + import_component.notna().astype("float64"),
        pd.Series(2.0, index=index),
        floored,
    )

    observation = confidence.observation_support(
        pd.to_numeric(work["xb_transaction_count_fy"], errors="coerce")
    )
    consistency = confidence.internal_consistency(
        work, uses_revenue=True, uses_revenue_split=True
    )
    scored = confidence.score(completeness, directness, applicability, observation, consistency)

    # --- Diagnostics ------------------------------------------------------
    flags = common.FlagSet(index)
    flags.add(
        "foreign_revenue_imputed",
        foreign_revenue.source.isin(["sector_benchmark", "portfolio_benchmark"]),
    )
    flags.add("cogs_imputed", cogs.source.isin(["sector_benchmark", "portfolio_benchmark"]))
    flags.add("no_disclosed_hedging", work["fx_forward_notional_zar"].isna())
    flags.add("wallet_floored_at_observed", floored)
    flags.add("observed_exceeds_estimate", share_result.flags == "observed_exceeds_estimate")
    flags.add(
        "estimate_dominated_by_imputed_driver",
        (weight_export + weight_import > 0.8 * estimate.fillna(0.0))
        & (foreign_revenue.source != "disclosed")
        & (cogs.source != "disclosed"),
    )
    flags.add("revenue_split_identity_failed", work["revenue_split_identity_ok"].eq(False))

    explain_frame = pd.DataFrame(
        {
            "estimate_zar": estimate,
            "export_component": export_component,
            "import_component": import_component,
            "hedging_component": hedging_component,
            "foreign_revenue_basis_zar": foreign_revenue.value,
            "foreign_revenue_source": foreign_revenue.source,
            "cogs_basis_zar": cogs.value,
            "cogs_source": cogs.source,
            "export_intensity": export_intensity,
            "import_intensity": import_intensity,
            "export_benchmark_level": peers.levels(EXPORT_BENCHMARK),
            "export_benchmark_n": peers.sample_sizes(EXPORT_BENCHMARK),
            "import_benchmark_level": peers.levels(IMPORT_BENCHMARK),
            "import_benchmark_n": peers.sample_sizes(IMPORT_BENCHMARK),
            "observed_zar": observed,
            "share": share_result.share,
            "fy_label": work["fy_label"],
            "xb_active_countries_fy": work["xb_active_countries_fy"],
            "xb_active_currency_pairs_fy": work["xb_active_currency_pairs_fy"],
            "overlap_excluded_zar": overlap_excluded,
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
        overlap_excluded=overlap_excluded,
        benchmark_level=benchmarks.dominant_level(
            [peers.levels(EXPORT_BENCHMARK), peers.levels(IMPORT_BENCHMARK)]
        ),
        benchmark_n=benchmarks.total_sample(
            [peers.sample_sizes(EXPORT_BENCHMARK), peers.sample_sizes(IMPORT_BENCHMARK)],
            [peers.levels(EXPORT_BENCHMARK), peers.levels(IMPORT_BENCHMARK)],
        ),
        benchmark_fallback_reason=(
            "export: "
            + peers.fallback_reasons(EXPORT_BENCHMARK)
            + "; import: "
            + peers.fallback_reasons(IMPORT_BENCHMARK)
        ),
    )

    components = common.component_rows(
        work,
        PRODUCT,
        {
            "export_settlement": export_component,
            "import_settlement": import_component,
            "hedging_execution": hedging_component,
        },
        {
            "export_settlement": (foreign_revenue.value, foreign_revenue.source),
            "import_settlement": (cogs.value, cogs.source),
            "hedging_execution": (
                pd.to_numeric(work["fx_forward_notional_zar"], errors="coerce"),
                pd.Series("disclosed", index=index).where(
                    work["fx_forward_notional_zar"].notna(), "unavailable"
                ),
            ),
        },
    )

    detail = scored.detail.copy()
    detail["entity_id"] = work["entity_id"]
    detail["product"] = PRODUCT

    flag_frame = flags.as_frame()
    flag_frame["entity_id"] = work["entity_id"]
    flag_frame["product"] = PRODUCT

    return common.PillarOutput(
        estimates,
        components,
        detail,
        flag_frame,
        peers.coefficient_records(),
        peers.metric_summary(),
    )
