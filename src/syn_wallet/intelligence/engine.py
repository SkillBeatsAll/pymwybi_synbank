"""Stage 4: turn the analytical contract into banker-oriented intelligence.

The whole layer is a pure function of two published tables plus the sensitivity
sweep. It adds no data of its own -- every number it emits is read from
``opportunity_engine.parquet``, and every sentence it writes is a template filled
from those fields -- so a rerun on unchanged inputs reproduces it exactly.

It reads ``client_opportunity_profile.parquet`` only to carry through the two
fields the per-product grain cannot hold: the investment-banking category, and
the model's own recommended next product. Everything else comes from the
per-product table, so there is one source for each number rather than two that
could drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..wallet import assumptions
from . import config, explanations, portfolio, profiles, questions, selection
from . import sensitivity_view


@dataclass
class Intelligence:
    """Everything one run of the intelligence layer produces."""

    client_intelligence: pd.DataFrame
    portfolio_intelligence: pd.DataFrame
    banker_questions: pd.DataFrame
    opportunity_explanations: pd.DataFrame
    client_cards: pd.DataFrame
    opportunity_detail: pd.DataFrame
    sensitivity: pd.DataFrame
    report: dict[str, Any] = field(default_factory=dict)


def _check_contract(engine_table: pd.DataFrame, client_profile: pd.DataFrame) -> None:
    """Fail early and loudly when the inputs are not the contract we expect."""
    versions = set(engine_table["methodology_version"].unique())
    if versions != {config.REQUIRED_METHODOLOGY}:
        raise ValueError(
            f"opportunity_engine.parquet carries methodology {sorted(versions)}, but this "
            f"intelligence layer is built against {config.REQUIRED_METHODOLOGY}. Rebuild the "
            "wallet engine or re-validate this layer."
        )
    missing = set(engine_table["entity_id"]) ^ set(client_profile["entity_id"])
    if missing:
        raise ValueError(
            f"the two contract tables disagree on which clients exist: {sorted(missing)}"
        )
    expected = len(client_profile) * len(assumptions.PRODUCTS)
    if len(engine_table) != expected:
        raise ValueError(
            f"opportunity_engine.parquet has {len(engine_table)} rows, expected {expected} "
            "(one per client x product)"
        )


def run(
    engine_table: pd.DataFrame,
    client_profile: pd.DataFrame,
    sensitivity: pd.DataFrame | None = None,
) -> Intelligence:
    """Build the whole intelligence layer from the analytical contract."""
    engine_table = engine_table.reset_index(drop=True)
    client_profile = client_profile.reset_index(drop=True)
    _check_contract(engine_table, client_profile)

    sensitivity_summary = (
        sensitivity_view.build(sensitivity)
        if sensitivity is not None and not sensitivity.empty
        else sensitivity_view.empty(engine_table)
    )

    # The investment-banking category lives only on the client profile, because
    # it is a per-client fact. Carry it onto the IB rows so the explanation
    # renderer has one place to read from.
    enriched = engine_table.merge(
        client_profile[["entity_id", "ib_opportunity_type", "recommended_next_product"]],
        on="entity_id",
        how="left",
        validate="many_to_one",
    )

    scored = selection.score(enriched, sensitivity_summary)
    scored = selection.assign_slots(scored)
    selections = selection.client_selection_summary(scored)

    client_intelligence = profiles.build(scored, sensitivity_summary, selections)
    opportunity_explanations = explanations.build(scored, sensitivity_summary)
    banker_questions = questions.build(scored)
    portfolio_intelligence = portfolio.build(scored, client_intelligence, sensitivity_summary)
    client_cards = portfolio.client_level_metrics(client_intelligence)

    detail = scored[
        [
            "entity_id",
            "entity_name",
            "sector",
            "product",
            "product_label",
            "product_class",
            "pillar_role",
            "observed_zar",
            "addressable_zar",
            "opportunity_zar",
            "share",
            "confidence",
            "confidence_band",
            "commercial_opportunity_score",
            "selection_score",
            "selection_role_weight",
            "selection_confidence_weight",
            "selection_diagnostic_factor",
            "selection_sensitivity_factor",
            "selection_slot",
            "selection_rank_for_client",
            "headroom_fraction",
            "opportunity_status",
            "status_reason",
            "status_action",
            "sensitivity_flag",
            "rank_stability",
            "diagnostic_count",
            "high_severity_diagnostic",
            "methodology_version",
        ]
    ].copy()
    detail["intelligence_version"] = config.INTELLIGENCE_VERSION

    status_counts = scored["opportunity_status"].value_counts().to_dict()
    report = {
        "intelligence_version": config.INTELLIGENCE_VERSION,
        "methodology_version": config.REQUIRED_METHODOLOGY,
        "clients": int(engine_table["entity_id"].nunique()),
        "products": list(assumptions.PRODUCTS),
        "product_classes": {
            product: str(
                engine_table.loc[engine_table["product"] == product, "product_class"].iloc[0]
            )
            for product in assumptions.PRODUCTS
        },
        "selection_rules": config.SELECTION_WEIGHTS.as_records(),
        "configuration": config.registry(),
        "status_counts": {str(key): int(value) for key, value in status_counts.items()},
        "clients_with_primary_opportunity": int(selections["has_primary_opportunity"].sum()),
        "clients_without_primary_opportunity": int(
            (~selections["has_primary_opportunity"]).sum()
        ),
        "questions_generated": int(len(banker_questions)),
        "explanations_generated": int(len(opportunity_explanations)),
        "scenarios_tested": int(sensitivity_summary["scenarios_tested"].max())
        if len(sensitivity_summary)
        else 0,
        "terminology": {
            product: config.DENOMINATOR_LABEL[product] for product in assumptions.PRODUCTS
        },
        "forbidden_phrases": list(config.FORBIDDEN_PHRASES),
    }

    return Intelligence(
        client_intelligence=client_intelligence,
        portfolio_intelligence=portfolio_intelligence,
        banker_questions=banker_questions,
        opportunity_explanations=opportunity_explanations,
        client_cards=client_cards,
        opportunity_detail=detail,
        sensitivity=sensitivity_summary,
        report=report,
    )
