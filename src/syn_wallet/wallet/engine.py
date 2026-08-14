"""Runs the five pillar models and assembles the wallet outputs.

Stage 3 of the pipeline. It reads ``client_features.parquet`` and writes the
wallet, opportunity, component, diagnostic and summary tables. It adds no data
of its own: every number it produces is a function of the feature table plus the
declared assumptions, so a rerun on unchanged inputs reproduces it exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import assumptions, common, confidence, diagnostics, opportunity
from .pillars import cash, fx, investment_banking, lending, trade

#: Pillar builders in output order.
PILLARS = {
    assumptions.CASH: cash.build,
    assumptions.FX: fx.build,
    assumptions.TRADE: trade.build,
    assumptions.LENDING: lending.build,
    assumptions.IB: investment_banking.build,
}


@dataclass
class WalletModel:
    """Everything one run of the engine produces."""

    estimates: pd.DataFrame
    opportunities: pd.DataFrame
    components: pd.DataFrame
    confidence_detail: pd.DataFrame
    flags: pd.DataFrame
    diagnostics: pd.DataFrame
    portfolio_summary: pd.DataFrame
    benchmarks: pd.DataFrame
    assumption_registry: pd.DataFrame
    sector_rules: pd.DataFrame
    report: dict[str, Any] = field(default_factory=dict)


def run(features: pd.DataFrame) -> WalletModel:
    """Estimate every pillar for every client and rank the resulting opportunities."""
    features = features.reset_index(drop=True)

    estimate_frames: list[pd.DataFrame] = []
    component_frames: list[pd.DataFrame] = []
    confidence_frames: list[pd.DataFrame] = []
    flag_frames: list[pd.DataFrame] = []
    benchmark_records: list[dict[str, Any]] = []

    for product, builder in PILLARS.items():
        output = builder(features)
        if len(output.estimates) != len(features):
            raise AssertionError(
                f"{product} produced {len(output.estimates)} rows for {len(features)} clients"
            )
        estimate_frames.append(output.estimates)
        component_frames.append(output.components)
        confidence_frames.append(output.confidence_detail)
        flag_frames.append(output.flags)
        benchmark_records.extend(output.benchmarks)

    estimates = pd.concat(estimate_frames, ignore_index=True)
    estimates = opportunity.score(estimates)

    components = pd.concat(component_frames, ignore_index=True)
    confidence_detail = pd.concat(confidence_frames, ignore_index=True)
    flags = pd.concat(flag_frames, ignore_index=True)

    findings = diagnostics.build(estimates, features)
    summary = opportunity.portfolio_summary(estimates)
    ranked = opportunity.ranked_view(estimates)

    benchmark_frame = pd.DataFrame(benchmark_records)
    assumption_frame = pd.DataFrame(assumptions.registry())
    sector_frame = pd.DataFrame(assumptions.sector_rule_registry())

    report = {
        "methodology_version": assumptions.METHODOLOGY_VERSION,
        "clients": int(features["entity_id"].nunique()),
        "products": list(assumptions.PRODUCTS),
        "estimate_rows": int(len(estimates)),
        "estimate_basis_by_product": dict(assumptions.ESTIMATE_BASIS),
        "estimate_basis_notes": dict(assumptions.ESTIMATE_BASIS_NOTES),
        "confidence_weights": confidence.WEIGHTS,
        "opportunity_weights": assumptions.OPPORTUNITY_WEIGHTS,
        "benchmarks": benchmark_records,
        "portfolio_summary": summary.to_dict(orient="records"),
        "diagnostics": diagnostics.summarise(findings, estimates),
    }

    return WalletModel(
        estimates=estimates,
        opportunities=ranked,
        components=components,
        confidence_detail=confidence_detail,
        flags=flags,
        diagnostics=findings,
        portfolio_summary=summary,
        benchmarks=benchmark_frame,
        assumption_registry=assumption_frame,
        sector_rules=sector_frame,
        report=report,
    )


#: Worked-example inputs that are counts, not rand. Rendering an instruction
#: count as "R2" is the kind of small wrongness that makes a reader distrust
#: every other number on the page.
NON_MONETARY_INPUTS = frozenset(
    {"employees", "txn_payroll_count_fy", "txn_memo_count_fy"}
)


def worked_example(model: WalletModel, features: pd.DataFrame, entity_id: str) -> dict[str, Any]:
    """A full audit trail for one client: inputs, components, outputs, per product.

    Everything here is read back from the generated tables, so a worked example
    cannot drift from what the engine actually produced.
    """
    client = features[features["entity_id"] == entity_id]
    if client.empty:
        raise KeyError(f"unknown entity_id: {entity_id}")
    row = client.iloc[0]

    inputs = {
        "revenue_total_zar": row["revenue_total_zar"],
        "cost_of_sales_zar": row["cost_of_sales_zar"],
        "revenue_foreign_zar": row["revenue_foreign_zar"],
        "inventory_zar": row["inventory_zar"],
        "trade_receivables_zar": row["trade_receivables_zar"],
        "trade_payables_zar": row["trade_payables_zar"],
        "working_capital_zar": row["working_capital_zar"],
        "gross_debt_zar": row["gross_debt_zar"],
        "debt_current_zar": row["debt_current_zar"],
        "undrawn_facilities_zar": row["undrawn_facilities_zar"],
        "capex_zar": row["capex_zar"],
        "fx_forward_notional_zar": row["fx_forward_notional_zar"],
        "employees": row["employees"],
        "txn_collections_domestic_volume_zar_fy": float(
            row["txn_collections_domestic_volume_zar_fy"]
        ),
        "txn_supplier_payments_domestic_volume_zar_fy": float(
            row["txn_supplier_payments_domestic_volume_zar_fy"]
        ),
        "txn_swift_channel_volume_zar_fy": float(row["txn_swift_channel_volume_zar_fy"]),
        "xb_total_volume_zar_fy": float(row["xb_total_volume_zar_fy"]),
        "tf_total_value_zar_fy": float(row["tf_total_value_zar_fy"]),
        "txn_payroll_count_fy": int(row["txn_payroll_count_fy"]),
        "txn_memo_count_fy": int(row["txn_memo_count_fy"]),
    }

    products = []
    for product in assumptions.PRODUCTS:
        estimate_row = model.estimates[
            (model.estimates["entity_id"] == entity_id)
            & (model.estimates["product"] == product)
        ].iloc[0]
        component_rows = model.components[
            (model.components["entity_id"] == entity_id)
            & (model.components["product"] == product)
        ]
        confidence_row = model.confidence_detail[
            (model.confidence_detail["entity_id"] == entity_id)
            & (model.confidence_detail["product"] == product)
        ].iloc[0]
        products.append(
            {
                "product": product,
                "estimate_basis": estimate_row["estimate_basis"],
                "components": component_rows[
                    ["component", "component_zar", "driver_value_zar", "driver_source"]
                ].to_dict(orient="records"),
                "observed_zar": estimate_row["observed_zar"],
                "estimate_zar": estimate_row["estimate_zar"],
                "share": estimate_row["share"],
                "share_basis": estimate_row["share_basis"],
                "gap_zar": estimate_row["gap_zar"],
                "confidence": estimate_row["confidence"],
                "confidence_band": estimate_row["confidence_band"],
                "confidence_factors": {
                    name: float(confidence_row[name])
                    for name in (*confidence.WEIGHTS, "evidence_directness")
                },
                "opportunity_score": estimate_row["opportunity_score"],
                "rank_overall": int(estimate_row["rank_overall"]),
                "rank_in_product": int(estimate_row["rank_in_product"]),
                "diagnostic_flags": estimate_row["diagnostic_flags"],
                "explanation": estimate_row["explanation"],
            }
        )

    return {
        "entity_id": entity_id,
        "entity_name": row["entity_name"],
        "sector": row["sector"],
        "fy_label": row["fy_label"],
        "fiscal_year_end": str(row["fiscal_year_end"]),
        "reporting_currency": row["reporting_currency"],
        "inputs": {
            key: (float(value) if pd.notna(value) else None) for key, value in inputs.items()
        },
        "products": products,
    }


def format_worked_example(example: dict[str, Any]) -> str:
    """Render a worked example as readable text for the model report."""
    lines = [
        f"### {example['entity_id']} {example['entity_name']} "
        f"({example['sector']}, {example['fy_label']}, reports in "
        f"{example['reporting_currency']})",
        "",
        "**Inputs (ZAR, fiscal year):**",
        "",
    ]
    for key, value in example["inputs"].items():
        if value is None:
            rendered = "not disclosed"
        elif key in NON_MONETARY_INPUTS:
            rendered = common.count(value)
        else:
            rendered = common.zar(value)
        lines.append(f"- `{key}` = {rendered}")
    lines.append("")
    for product in example["products"]:
        lines.append(f"**{assumptions.PRODUCT_LABELS[product['product']]}**")
        lines.append("")
        lines.append(f"- Basis: `{product['estimate_basis']}`")
        for component in product["components"]:
            driver = component["driver_value_zar"]
            driver_text = (
                f" from driver {common.zar(driver)} ({component['driver_source']})"
                if pd.notna(driver)
                else ""
            )
            lines.append(
                f"- Component `{component['component']}` = "
                f"{common.zar(component['component_zar'])}{driver_text}"
            )
        lines.append(f"- Estimate: {common.zar(product['estimate_zar'])}")
        lines.append(f"- Observed: {common.zar(product['observed_zar'])}")
        share = product["share"]
        lines.append(
            f"- Share: {common.pct(share) if share is not None and pd.notna(share) else 'not computed'}"
            f" (`{product['share_basis']}`)"
        )
        lines.append(f"- Gap: {common.zar(product['gap_zar'])}")
        factors = ", ".join(
            f"{name} {value:.2f}" for name, value in product["confidence_factors"].items()
        )
        lines.append(
            f"- Confidence: {product['confidence']:.2f} ({product['confidence_band']}) "
            f"[{factors}]"
        )
        lines.append(
            f"- Opportunity score {product['opportunity_score']:.3f}, "
            f"rank {product['rank_in_product']} in product, "
            f"{product['rank_overall']} overall"
        )
        if product["diagnostic_flags"]:
            lines.append(f"- Flags: `{product['diagnostic_flags']}`")
        lines.append("")
        lines.append(f"> {product['explanation']}")
        lines.append("")
    return "\n".join(lines)
