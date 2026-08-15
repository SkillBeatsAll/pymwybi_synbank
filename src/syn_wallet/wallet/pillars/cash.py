"""Pillar 1 -- Transactional / Cash Management.

**What the denominator is: Addressable Cash Flow.** The annual gross operating
payment-and-collection turnover the client must push through a bank account:
money in from customers, money out to suppliers. Not revenue -- revenue is one of
two legs, and the model says so in its component breakdown.

**Addressable Cash Flow is not a wallet, and the two have different columns.**

``addressable_cash_flow_zar``
    Revenue + cost of sales. A **client** flow magnitude: turnover that must be
    banked somewhere. Share of wallet is the fraction of it Syn Bank handles, and
    that share is the defensible number this pillar exists to produce.
``cash_management_wallet_zar``
    The fee income a bank earns on that flow. **NULL for every client, and
    deliberately so.** Syn Bank is fictional and discloses no pricing, so any
    rand figure here would rest on an invented basis-point assumption. R8.7tn of
    Glencore addressable cash flow is not R8.7tn of anything a bank could earn,
    and the schema is built so nobody can read it that way.

The distinction costs nothing analytically -- share, gap and rank are unchanged --
and removes the single most likely misreading of the deck.

**Why only two legs.** Payroll, tax and intercompany sweeps are excluded from
*both* sides of the ratio, not just one:

* *Payroll* -- no employee-cost field exists anywhere in the external data, and
  observed payroll is a token R61m across the whole portfolio for a year at an
  average of R11,048 an instruction. Sizing it would need an invented cost per
  head, and putting an invented denominator opposite a token numerator would
  manufacture an enormous fake gap. Payroll is carried as a *mandate signal*
  instead: instructions per employee, which is the sharpest engagement fact in
  the dataset.
* *Tax* -- no tax charge is disclosed.
* *Intercompany sweeps* -- the largest observed leg (R64.8bn), but treasury
  sweep volume has no external anchor at all. Including it in the numerator
  against a denominator that cannot cover it would inflate share.

All three are reported as ``out_of_scope_observed_zar`` so the excluded activity
stays visible rather than disappearing.

**The SWIFT overlap.** The numerator uses the non-SWIFT-channel volume of each
in-scope leg, measured per leg rather than apportioned. Those SWIFT rows are not
added to the FX pillar either, so no rand is double counted in either direction.
The excluded amount is published per client as ``overlap_excluded_zar``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import assumptions, benchmarks, common, confidence

PRODUCT = assumptions.CASH


def _explain(row: pd.Series) -> str:
    parts = [
        f"Addressable cash flow {common.zar(row['estimate_zar'])} = "
        f"collections {common.zar(row['collections_component'])} "
        f"(revenue {common.zar(row['revenue_total_zar'])}, accounting identity: revenue is "
        f"collected into a bank account)"
    ]
    if pd.notna(row["supplier_component"]):
        source = row["cogs_source"].replace("_", " ")
        parts.append(
            f"supplier payments {common.zar(row['supplier_component'])} "
            f"(cost of sales {common.zar(row['cogs_basis_zar'])}, {source})"
        )
    else:
        parts.append("supplier payments not estimable (no cost of sales disclosed or imputable)")
    sentence = " + ".join(parts) + "."

    sentence += (
        f" Syn Bank handled {common.zar(row['observed_zar'])} of in-scope domestic collections "
        f"and supplier payments in {row['fy_label']}"
    )
    if pd.notna(row["share"]):
        sentence += f", a share of {common.pct(row['share'])}."
    else:
        sentence += "."

    if row["overlap_excluded_zar"] > 0:
        sentence += (
            f" {common.zar(row['overlap_excluded_zar'])} of SWIFT-channel volume on those legs was "
            "excluded from both this pillar and FX to avoid double counting."
        )
    sentence += (
        f" A further {common.zar(row['out_of_scope_observed_zar'])} of intercompany, payroll and "
        "tax activity sits outside this denominator by design, carried as engagement signals "
        "rather than as rand."
    )
    sentence += (
        " This is the client's own turnover, not bank revenue: no fee wallet is estimated, "
        "because Syn Bank discloses no pricing."
    )
    if row["payroll_instructions_per_1k_employees"] is not None and pd.notna(
        row["payroll_instructions_per_1k_employees"]
    ):
        sentence += (
            f" Payroll mandate signal: {row['txn_payroll_count_fy']:,.0f} instructions for "
            f"{row['employees']:,.0f} employees "
            f"({row['payroll_instructions_per_1k_employees']:.1f} per 1,000 staff)."
        )
    return sentence


def build(
    frame: pd.DataFrame, config: assumptions.ModelConfig | None = None
) -> common.PillarOutput:
    """Estimate addressable cash flow, and Syn Bank's share of it, per client.

    ``config`` is accepted for symmetry and is unused: both coefficients here are
    accounting identities, so no benchmark scenario can move this pillar. That
    immunity is itself a finding, and docs/MODEL_SENSITIVITY.md reports it.
    """
    index = frame.index
    work = frame.copy()

    # --- Drivers ---------------------------------------------------------
    revenue = pd.to_numeric(work["revenue_total_zar"], errors="coerce")

    cogs_comparable = work["sector"].isin(assumptions.COGS_COMPARABLE_SECTORS)
    sector_cogs, portfolio_cogs, cogs_counts = benchmarks.sector_ratio_table(
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

    # --- Components (both coefficients are accounting identities) ---------
    collections_component = revenue * 1.0
    supplier_component = cogs.value * 1.0
    modelled_estimate = pd.concat([collections_component, supplier_component], axis=1).sum(
        axis=1, min_count=1
    )

    # --- Observed, scoped to match the denominator ------------------------
    observed = pd.to_numeric(
        work["txn_collections_domestic_volume_zar_fy"], errors="coerce"
    ) + pd.to_numeric(work["txn_supplier_payments_domestic_volume_zar_fy"], errors="coerce")
    in_scope_all_channels = pd.to_numeric(
        work["txn_collections_volume_zar_fy"], errors="coerce"
    ) + pd.to_numeric(work["txn_supplier_payments_volume_zar_fy"], errors="coerce")
    overlap_excluded = (in_scope_all_channels - observed).clip(lower=0.0)
    out_of_scope = (
        pd.to_numeric(work["txn_intercompany_sweeps_volume_zar_fy"], errors="coerce")
        + pd.to_numeric(work["txn_payroll_volume_zar_fy"], errors="coerce")
        + pd.to_numeric(work["txn_tax_volume_zar_fy"], errors="coerce")
    )

    estimate, floored = common.apply_observed_floor(modelled_estimate, observed)
    share_result = common.share_and_gap(observed, estimate)

    # --- Confidence -------------------------------------------------------
    rules = work["sector"].map(lambda sector: assumptions.sector_rule(PRODUCT, sector))
    applicability = rules.map(lambda rule: rule.applicability).astype("float64")

    completeness = (1.0 + (cogs.source == "disclosed").astype("float64")) / 2.0
    identity = confidence.BASIS_DIRECTNESS[assumptions.ACCOUNTING_IDENTITY]
    weight_collections = collections_component.fillna(0.0)
    weight_supplier = supplier_component.fillna(0.0)
    total_weight = (weight_collections + weight_supplier).replace(0.0, np.nan)
    directness = confidence.effective_directness(
        (weight_collections * identity * 1.0 + weight_supplier * identity * cogs.quality)
        / total_weight,
        collections_component.notna().astype("float64")
        + supplier_component.notna().astype("float64"),
        pd.Series(2.0, index=index),
        floored,
    )
    observation = confidence.observation_support(
        pd.to_numeric(work["txn_collections_count_fy"], errors="coerce")
        + pd.to_numeric(work["txn_supplier_payments_count_fy"], errors="coerce")
    )
    consistency = confidence.internal_consistency(work, uses_revenue=True)
    scored = confidence.score(completeness, directness, applicability, observation, consistency)

    # --- Diagnostics ------------------------------------------------------
    employees = pd.to_numeric(work["employees"], errors="coerce")
    payroll_instructions = pd.to_numeric(work["txn_payroll_count_fy"], errors="coerce")
    payroll_per_1k = (payroll_instructions / employees * 1000.0).replace(
        [np.inf, -np.inf], np.nan
    )

    flags = common.FlagSet(index)
    flags.add("cogs_imputed", cogs.source.isin(["sector_benchmark", "portfolio_benchmark"]))
    flags.add("cogs_unavailable", cogs.source == "unavailable")
    flags.add("revenue_not_a_cash_measure", work["sector"] == "insurance")
    flags.add("swift_overlap_material", overlap_excluded > 0.20 * observed.replace(0.0, np.nan))
    flags.add("wallet_floored_at_observed", floored)
    flags.add("observed_exceeds_estimate", share_result.flags == "observed_exceeds_estimate")
    flags.add("payroll_mandate_absent", payroll_instructions.fillna(0) < 25)
    flags.add("revenue_denominator_soft_basis", work["revenue_total_is_soft_basis"].fillna(False))

    explain_frame = pd.DataFrame(
        {
            "estimate_zar": estimate,
            "collections_component": collections_component,
            "supplier_component": supplier_component,
            "revenue_total_zar": revenue,
            "cogs_basis_zar": cogs.value,
            "cogs_source": cogs.source,
            "observed_zar": observed,
            "share": share_result.share,
            "fy_label": work["fy_label"],
            "overlap_excluded_zar": overlap_excluded,
            "out_of_scope_observed_zar": out_of_scope,
            "txn_payroll_count_fy": payroll_instructions,
            "employees": employees,
            "payroll_instructions_per_1k_employees": payroll_per_1k,
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
        estimate_kind=assumptions.ADDRESSABLE_CASH_FLOW,
        estimate_modelled=modelled_estimate,
        out_of_scope_observed=out_of_scope,
        overlap_excluded=overlap_excluded,
        benchmark_fallback_reason=pd.Series(
            "accounting_identity_no_peer_benchmark_needed", index=index, dtype="object"
        ),
    )

    components = common.component_rows(
        work,
        PRODUCT,
        {"collections": collections_component, "supplier_payments": supplier_component},
        {
            "collections": (revenue, pd.Series("disclosed", index=index)),
            "supplier_payments": (cogs.value, cogs.source),
        },
    )

    detail = scored.detail.copy()
    detail["entity_id"] = work["entity_id"]
    detail["product"] = PRODUCT

    flag_frame = flags.as_frame()
    flag_frame["entity_id"] = work["entity_id"]
    flag_frame["product"] = PRODUCT

    # The one measured ratio this pillar uses is not a penetration benchmark at
    # all: it imputes a *missing driver*, and it is leave-one-out by
    # construction, since only clients that disclosed cost of sales contribute
    # and only clients that did not need the imputation.
    imputed_clients = int(cogs.source.isin(["sector_benchmark", "portfolio_benchmark"]).sum())
    metric_records = [
        {
            "metric": "cash_cogs_to_revenue_imputation",
            "product": PRODUCT,
            "numerator": "cost_of_sales_zar",
            "denominator": "revenue_total_zar",
            "percentile": 0.5,
            "population_n": int(sum(cogs_counts.values())),
            "population_median": portfolio_cogs,
            "population_p75": None,
            "population_max": None,
            "clients_on_sector_benchmark": int((cogs.source == "sector_benchmark").sum()),
            "clients_on_portfolio_benchmark": int(
                (cogs.source == "portfolio_benchmark").sum()
            ),
            "clients_without_benchmark": int((cogs.source == "unavailable").sum()),
            "coefficient_min": None,
            "coefficient_max": None,
            "coefficient_spread": None,
            "sample_entities": ", ".join(
                f"{sector}:{count}" for sector, count in sorted(cogs_counts.items())
            ),
            "basis": assumptions.PORTFOLIO_BENCHMARK,
            "rationale": (
                "Sector median cost-of-sales intensity used to impute a supplier-payment "
                "denominator where cost of sales is undisclosed, for "
                f"{imputed_clients} of {len(work)} clients. Restricted to sectors where cost of "
                "sales is a procurement proxy, so insurance and real estate are never imputed. "
                "Self-inclusion is structurally impossible: a client that disclosed the field "
                "does not need it imputed. Sector medians: "
                + ", ".join(
                    f"{sector} {value:.3f}" for sector, value in sorted(sector_cogs.items())
                )
                + (
                    f"; portfolio fallback {portfolio_cogs:.3f}."
                    if portfolio_cogs is not None
                    else "; no portfolio fallback available."
                )
            ),
        }
    ]

    return common.PillarOutput(
        estimates, components, detail, flag_frame, [], metric_records
    )
