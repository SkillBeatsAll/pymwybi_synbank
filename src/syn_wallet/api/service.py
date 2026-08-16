"""Read the published tables once and project them into dashboard payloads.

This layer does **no financial arithmetic**. Every figure it returns is read
from a column that stages 3 and 4 already computed, and the only operations
performed here are filtering, sorting and shaping into JSON. If a number the
dashboard shows cannot be traced to a column in one of these tables, it is a
bug in this file.

Two consequences worth stating, because they are what keep the dashboard honest:

* **No pillar is ever summed with another.** There is no total-wallet field in
  any payload, and :func:`_assert_no_cross_pillar_total` runs over every
  portfolio payload at build time.
* **Descriptive features are separated from model estimates.** Currency pairs,
  counterparty countries and the external financial signals come from the
  stage-2 feature table. They describe *observed* activity and disclosed
  financials; no estimate is ever derived from them here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import config as paths
from ..wallet import assumptions
from ..wallet.common import pct, zar

#: Stage 3 and 4 outputs -- the analytical contract and the intelligence layer.
MODEL_TABLES = (
    "opportunity_engine",
    "client_opportunity_profile",
    "client_opportunity_intelligence",
    "opportunity_explanations",
    "banker_questions",
    "portfolio_opportunity_intelligence",
    "opportunity_selection_detail",
    "opportunity_sensitivity_summary",
    "client_opportunity_cards",
    "model_diagnostics",
    "product_confidence",
    "product_classification",
    "portfolio_summary",
)

#: Optional: written only by `build_wallet --sensitivity`.
SENSITIVITY_TABLES = ("model_sensitivity_robustness", "model_sensitivity_by_product")

#: Stage 2. Descriptive only -- observed breakdowns and disclosed financials.
FEATURE_TABLES = ("client_features", "client_corridor_breakdown")

PRODUCT_ORDER = {product: position for position, product in enumerate(assumptions.PRODUCTS)}


# ---------------------------------------------------------------------------
# JSON safety
# ---------------------------------------------------------------------------


def clean(value: Any) -> Any:
    """One cell as JSON-legal Python. NaN becomes null, never the token NaN."""
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (np.floating,)):
        return None if math.isnan(float(value)) else float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def records(frame: pd.DataFrame, columns: list[str] | None = None) -> list[dict[str, Any]]:
    """A DataFrame as a list of JSON-safe dicts."""
    if frame.empty:
        return []
    selected = frame[columns] if columns else frame
    return [
        {column: clean(value) for column, value in zip(selected.columns, row)}
        for row in selected.itertuples(index=False, name=None)
    ]


def with_money(rows: list[dict[str, Any]], *columns: str) -> list[dict[str, Any]]:
    """Add a ``<column>_display`` beside each named rand column.

    A raw number sent without its rendered form is an invitation for the browser
    to render it, and a second renderer is a second rounding rule: the front end
    had one that wrote R279bn where this one writes R278.56bn. Both are "right";
    they simply disagree, and the figure under audit is the server's. Shipping
    the string with the number removes the invitation.
    """
    for row in rows:
        for column in columns:
            row[f"{column}_display"] = money(row.get(column))
    return rows


def row_dict(row: pd.Series) -> dict[str, Any]:
    return {key: clean(value) for key, value in row.items()}


# ---------------------------------------------------------------------------
# Display helpers -- formatting only, never arithmetic
# ---------------------------------------------------------------------------


def money(value: Any) -> str | None:
    cleaned = clean(value)
    return zar(cleaned) if cleaned is not None else None


def share(value: Any) -> str | None:
    cleaned = clean(value)
    return pct(cleaned) if cleaned is not None else None


def figure(value: Any, kind: str = "zar") -> dict[str, Any]:
    """A number plus its rendered form, so the client never formats currency."""
    cleaned = clean(value)
    return {
        "value": cleaned,
        "display": (
            money(cleaned)
            if kind == "zar"
            else share(cleaned)
            if kind == "pct"
            else (f"{cleaned:.2f}" if cleaned is not None else None)
        ),
    }


@dataclass(frozen=True)
class Tables:
    """Every table the dashboard reads, loaded once."""

    data: dict[str, pd.DataFrame]

    def __getitem__(self, name: str) -> pd.DataFrame:
        if name not in self.data:
            raise KeyError(
                f"table {name!r} was not loaded. Available: {sorted(self.data)}"
            )
        return self.data[name]

    def has(self, name: str) -> bool:
        return name in self.data


def load(processed_dir: Path | None = None) -> Tables:
    """Read every published table into memory. Called once at startup."""
    import duckdb

    processed_dir = processed_dir or paths.PROCESSED_DIR
    connection = duckdb.connect(":memory:")
    loaded: dict[str, pd.DataFrame] = {}
    try:
        for name in MODEL_TABLES + FEATURE_TABLES:
            path = processed_dir / f"{name}.parquet"
            if not path.is_file():
                raise FileNotFoundError(
                    f"Missing {path}. Build the pipeline first — see README "
                    "'Running everything'."
                )
            loaded[name] = connection.execute(
                f"SELECT * FROM read_parquet('{path}')"
            ).df()
        for name in SENSITIVITY_TABLES:
            path = processed_dir / f"{name}.parquet"
            if path.is_file():
                loaded[name] = connection.execute(
                    f"SELECT * FROM read_parquet('{path}')"
                ).df()
    finally:
        connection.close()
    return Tables(loaded)


@lru_cache(maxsize=1)
def tables() -> Tables:
    return load()


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------

#: Pillar rand keys that must never be added together in a payload.
PILLAR_KEYS = ("cash", "fx", "trade", "lending")


def _assert_no_cross_pillar_total(payload: dict[str, Any]) -> None:
    """Fail the build if a payload contains the sum of the pillar figures.

    The display rule this dashboard is most likely to break by accident is the
    one against a single total-opportunity number. Checking the payload rather
    than trusting the template means a future edit that adds the sum fails here
    instead of shipping.
    """
    pillars = payload.get("pillars") or []
    values = [
        pillar.get("opportunity", {}).get("value")
        for pillar in pillars
        if pillar.get("key") in PILLAR_KEYS
    ]
    present = [value for value in values if value is not None]
    if len(present) < 2:
        return
    total = sum(present)

    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for position, value in enumerate(node):
                walk(value, f"{path}[{position}]")
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            if total and abs(float(node) - total) < 1.0:
                raise AssertionError(
                    f"payload field {path} equals the cross-pillar opportunity total; the "
                    "five pillars are measured on incomparable bases and must never be added"
                )

    walk(payload)


# ---------------------------------------------------------------------------
# Page 1 -- portfolio overview
# ---------------------------------------------------------------------------

PILLAR_META = {
    assumptions.CASH: {
        "key": "cash",
        "label": "Cash Management",
        "sublabel": "Transactional",
        "denominator": "Addressable Cash Flow",
        "basis": "Accounting identity — revenue + cost of sales",
        "role": "core",
    },
    assumptions.FX: {
        "key": "fx",
        "label": "FX",
        "sublabel": "Global Markets",
        "denominator": "Peer-benchmark addressable",
        "basis": "Peer benchmark — client excluded from its own population",
        "role": "core",
    },
    assumptions.TRADE: {
        "key": "trade",
        "label": "Trade Finance",
        "sublabel": "Documentary & guarantees",
        "denominator": "Peer-benchmark addressable",
        "basis": "Peer benchmark — client excluded from its own population",
        "role": "core",
    },
    assumptions.LENDING: {
        "key": "lending",
        "label": "Lending",
        "sublabel": "Financing opportunity",
        "denominator": "Financing opportunity",
        "basis": "Disclosed debt structure — no loan book, so no share exists",
        "role": "supporting",
    },
    assumptions.IB: {
        "key": "ib",
        "label": "Investment Banking",
        "sublabel": "Capital markets",
        "denominator": "Opportunity signal",
        "basis": "Ranked signal — no rand amount is estimated",
        "role": "signal",
    },
}


def _portfolio_metric(portfolio: pd.DataFrame, product: str, metric: str) -> Any:
    rows = portfolio[
        (portfolio["section"] == "portfolio_position")
        & (portfolio["product"] == product)
        & (portfolio["metric"] == metric)
    ]
    return rows.iloc[0] if not rows.empty else None


def portfolio_payload(store: Tables | None = None) -> dict[str, Any]:
    """Page 1. Three core pillar cards, two supporting, and portfolio insight."""
    store = store or tables()
    portfolio = store["portfolio_opportunity_intelligence"]
    summary = store["portfolio_summary"].set_index("product")
    confidence = store["product_confidence"].set_index("product")
    detail = store["opportunity_selection_detail"]
    profiles = store["client_opportunity_intelligence"]

    pillars = []
    for product, meta in PILLAR_META.items():
        group = detail[detail["product"] == product]
        summary_row = summary.loc[product]
        confidence_row = confidence.loc[product]
        range_row = _portfolio_metric(portfolio, product, "opportunity_range_text")
        low = _portfolio_metric(portfolio, product, "opportunity_range_low_zar")
        high = _portfolio_metric(portfolio, product, "opportunity_range_high_zar")

        top = (
            group[group["opportunity_status"] != "NO_HEADROOM_DEMONSTRATED"]
            .sort_values("selection_score", ascending=False)
            .head(3)
        )
        pillars.append(
            {
                **meta,
                "product": product,
                "product_class": clean(summary_row["product_class"]),
                "observed": figure(summary_row["total_observed_zar"]),
                "addressable": figure(summary_row["total_estimate_zar"]),
                "opportunity": figure(summary_row["total_gap_zar"]),
                "share": figure(summary_row["portfolio_share"], "pct"),
                "median_client_share": figure(summary_row["median_client_share"], "pct"),
                "range": {
                    "low": figure(low["value_numeric"] if low is not None else None),
                    "high": figure(high["value_numeric"] if high is not None else None),
                    "text": clean(range_row["value_text"]) if range_row is not None else None,
                },
                "confidence": {
                    "mean": clean(confidence_row["mean_confidence"]),
                    "high_pct": clean(confidence_row["pct_high"]),
                    "medium_pct": clean(confidence_row["pct_medium"]),
                    "low_pct": clean(confidence_row["pct_low"]),
                },
                "top_clients": [
                    {
                        "entity_id": clean(row["entity_id"]),
                        "entity_name": clean(row["entity_name"]),
                        "sector": clean(row["sector"]),
                        "opportunity": figure(row["opportunity_zar"]),
                        "signal": clean(row["commercial_opportunity_score"]),
                        "confidence_band": clean(row["confidence_band"]),
                        "status": clean(row["opportunity_status"]),
                    }
                    for _, row in top.iterrows()
                ],
            }
        )

    ib_categories = records(
        portfolio[portfolio["metric"] == "ib_signal_category_clients"][
            ["note", "value_numeric", "value_text"]
        ]
    )
    for row in ib_categories:
        note = row.pop("note") or ""
        row["category"] = note.split("`")[1] if "`" in note else note

    focus = (
        profiles[profiles["has_primary_opportunity"]]
        .sort_values("primary_selection_score", ascending=False)
        .head(6)
    )

    payload = {
        "pillars": pillars,
        "ib_categories": ib_categories,
        "focus": [
            {
                "entity_id": clean(row["entity_id"]),
                "entity_name": clean(row["entity_name"]),
                "sector": clean(row["sector"]),
                "product": clean(row["primary_product"]),
                "product_label": clean(row["primary_label"]),
                "status": clean(row["primary_status"]),
                "action": clean(row["primary_action"]),
                "score": clean(row["primary_selection_score"]),
                "confidence_band": clean(row["primary_confidence_band"]),
                "sensitivity": clean(row["primary_sensitivity_flag"]),
                "opportunity": figure(row["primary_opportunity_zar"]),
                "summary": clean(row["opportunity_summary"]),
            }
            for _, row in focus.iterrows()
        ],
        "sectors": records(
            portfolio[portfolio["section"] == "sector_concentration"][
                ["product", "sector", "value_numeric", "value_text", "note", "rank"]
            ]
        ),
        "concentration": records(
            portfolio[portfolio["section"] == "primary_concentration"][
                ["product", "product_label", "metric", "value_numeric", "value_text", "note"]
            ]
        ),
        "multiple": records(
            portfolio[portfolio["section"] == "multiple_opportunities"][
                ["entity_id", "entity_name", "sector", "value_numeric", "value_text", "note"]
            ]
        ),
        "low_confidence_high_value": records(
            portfolio[portfolio["section"] == "low_confidence_high_value"][
                ["entity_id", "entity_name", "product", "value_text", "note", "rank"]
            ]
        ),
        "clients": int(len(profiles)),
    }
    _assert_no_cross_pillar_total(payload)
    return payload


# ---------------------------------------------------------------------------
# Page 2 -- heatmap
# ---------------------------------------------------------------------------


def heatmap_payload(store: Tables | None = None) -> dict[str, Any]:
    """Page 2. Client x product grid with score, confidence, headroom, sensitivity."""
    store = store or tables()
    detail = store["opportunity_selection_detail"].copy()
    detail["_order"] = detail["product"].map(PRODUCT_ORDER)

    clients = (
        detail[["entity_id", "entity_name", "sector"]]
        .drop_duplicates()
        .sort_values("entity_id")
    )
    # Order rows by the client's best commercial position so the eye lands on
    # the top-left cell, which is where the answer to "focus next" lives.
    best = (
        detail.groupby("entity_id")["selection_score"].max().rename("best").reset_index()
    )
    clients = clients.merge(best, on="entity_id").sort_values(
        "best", ascending=False, kind="stable"
    )

    return {
        "products": [
            {
                "product": product,
                "key": meta["key"],
                "label": meta["label"],
                "role": meta["role"],
                "denominator": meta["denominator"],
            }
            for product, meta in PILLAR_META.items()
        ],
        "clients": records(clients[["entity_id", "entity_name", "sector"]]),
        "cells": with_money(
            records(
                detail.sort_values(["entity_id", "_order"])[
                [
                    "entity_id",
                    "entity_name",
                    "sector",
                    "product",
                    "product_label",
                    "commercial_opportunity_score",
                    "selection_score",
                    "confidence",
                    "confidence_band",
                    "headroom_fraction",
                    "sensitivity_flag",
                    "rank_stability",
                    "opportunity_status",
                    "status_action",
                    "status_reason",
                    "opportunity_zar",
                    "observed_zar",
                    "addressable_zar",
                    "share",
                    "selection_slot",
                    "high_severity_diagnostic",
                    "diagnostic_count",
                ]
                ]
            ),
            "opportunity_zar",
            "observed_zar",
            "addressable_zar",
        ),
        "sectors": sorted(detail["sector"].unique()),
        "statuses": ["PRIORITY", "INVESTIGATE", "MONITOR", "NO_HEADROOM_DEMONSTRATED"],
        "bands": ["HIGH", "MEDIUM", "LOW"],
    }


# ---------------------------------------------------------------------------
# Page 3 -- client drill-down
# ---------------------------------------------------------------------------

#: External financial signals worth showing per pillar. Nineteen fields at once
#: is a data dump; these are the ones that actually drive each estimate.
SIGNALS_BY_PRODUCT = {
    assumptions.CASH: (
        ("revenue_total_zar", "Revenue", "Collections leg of addressable cash flow"),
        ("cost_of_sales_zar", "Cost of sales", "Supplier-payment leg"),
        ("employees", "Employees", "Payroll mandate signal", "count"),
    ),
    assumptions.FX: (
        ("revenue_foreign_zar", "Foreign revenue", "Export settlement exposure"),
        ("cost_of_sales_zar", "Cost of sales", "Import settlement exposure"),
        ("fx_forward_notional_zar", "FX forward notional", "Disclosed hedging book"),
    ),
    assumptions.TRADE: (
        ("cost_of_sales_zar", "Cost of sales", "Import documentary driver"),
        ("revenue_foreign_zar", "Foreign revenue", "Export documentary driver"),
        ("inventory_zar", "Inventory", "Working stock behind trade demand"),
        ("revenue_total_zar", "Revenue", "Guarantee driver"),
    ),
    assumptions.LENDING: (
        ("debt_current_zar", "Debt falling due", "Refinancing inside 12 months"),
        ("undrawn_facilities_zar", "Undrawn facilities", "Committed headroom in use elsewhere"),
        ("working_capital_zar", "Working capital", "Cycle funded by short-term debt"),
        ("capex_zar", "Capex", "Investment programme"),
        ("gross_debt_zar", "Gross debt", "Total disclosed debt"),
    ),
    assumptions.IB: (
        ("gross_debt_zar", "Gross debt", "Leverage input"),
        ("debt_current_zar", "Debt falling due", "Near-term maturity input"),
        ("capex_zar", "Capex", "Capex intensity input"),
        ("named_lender_count", "Named lenders", "Syndicate breadth input", "count"),
    ),
}


def client_payload(entity_id: str, store: Tables | None = None) -> dict[str, Any]:
    """Page 3. Everything one client page needs, in one request."""
    store = store or tables()
    profiles = store["client_opportunity_intelligence"]
    rows = profiles[profiles["entity_id"] == entity_id]
    if rows.empty:
        raise KeyError(entity_id)
    profile = rows.iloc[0]

    detail = store["opportunity_selection_detail"]
    client_rows = detail[detail["entity_id"] == entity_id].copy()
    client_rows["_order"] = client_rows["product"].map(PRODUCT_ORDER)
    client_rows = client_rows.sort_values("_order")

    sensitivity = store["opportunity_sensitivity_summary"]
    sensitivity = sensitivity[sensitivity["entity_id"] == entity_id].set_index("product")

    explanations = store["opportunity_explanations"]
    explanations = explanations[explanations["entity_id"] == entity_id].set_index("product")

    features = store["client_features"]
    feature_row = features[features["entity_id"] == entity_id]
    feature_row = feature_row.iloc[0] if not feature_row.empty else None

    pillars = []
    for _, row in client_rows.iterrows():
        product = row["product"]
        meta = PILLAR_META[product]
        spread = sensitivity.loc[product] if product in sensitivity.index else None
        explanation = (
            explanations.loc[product] if product in explanations.index else None
        )
        signals = []
        for entry in SIGNALS_BY_PRODUCT[product]:
            column, label, why = entry[0], entry[1], entry[2]
            kind = entry[3] if len(entry) > 3 else "zar"
            if feature_row is None or column not in feature_row.index:
                continue
            value = clean(feature_row[column])
            signals.append(
                {
                    "field": column,
                    "label": label,
                    "why": why,
                    "value": value,
                    "display": (
                        money(value)
                        if kind == "zar"
                        else (f"{value:,.0f}" if value is not None else None)
                    ),
                }
            )
        pillars.append(
            {
                **meta,
                "product": product,
                "product_label": clean(row["product_label"]),
                "product_class": clean(row["product_class"]),
                "observed": figure(row["observed_zar"]),
                "addressable": figure(row["addressable_zar"]),
                "opportunity": figure(row["opportunity_zar"]),
                "share": figure(row["share"], "pct"),
                "confidence": clean(row["confidence"]),
                "confidence_band": clean(row["confidence_band"]),
                "headroom": clean(row["headroom_fraction"]),
                "status": clean(row["opportunity_status"]),
                "status_action": clean(row["status_action"]),
                "status_reason": clean(row["status_reason"]),
                "commercial_score": clean(row["commercial_opportunity_score"]),
                "selection_score": clean(row["selection_score"]),
                "selection_slot": clean(row["selection_slot"]),
                "commercial_rank": clean(row["selection_rank_for_client"]),
                "sensitivity_flag": clean(row["sensitivity_flag"]),
                "rank_stability": clean(row["rank_stability"]),
                "diagnostic_count": clean(row["diagnostic_count"]),
                "high_severity": clean(row["high_severity_diagnostic"]),
                "range": {
                    "estimate_low": figure(spread["estimate_low"]) if spread is not None else figure(None),
                    "estimate_base": figure(spread["estimate_base"]) if spread is not None else figure(None),
                    "estimate_high": figure(spread["estimate_high"]) if spread is not None else figure(None),
                    "opportunity_low": figure(spread["opportunity_low"]) if spread is not None else figure(None),
                    "opportunity_base": figure(spread["opportunity_base"]) if spread is not None else figure(None),
                    "opportunity_high": figure(spread["opportunity_high"]) if spread is not None else figure(None),
                    "scenarios": clean(spread["scenarios_tested"]) if spread is not None else None,
                }
                if spread is not None
                else None,
                "explanation": {
                    "what": clean(explanation["what"]),
                    "why": clean(explanation["why"]),
                    "evidence": clean(explanation["evidence"]),
                    "limitation": clean(explanation["limitation"]),
                    "next_action": clean(explanation["next_action"]),
                }
                if explanation is not None
                else None,
                "signals": signals,
            }
        )

    questions = store["banker_questions"]
    questions = questions[questions["entity_id"] == entity_id]

    diagnostics = store["model_diagnostics"]
    diagnostics = diagnostics[diagnostics["entity_id"] == entity_id]
    order = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}
    diagnostics = diagnostics.assign(
        _order=diagnostics["severity"].map(order)
    ).sort_values("_order")

    payload = {
        "entity_id": clean(profile["entity_id"]),
        "entity_name": clean(profile["entity_name"]),
        "sector": clean(profile["sector"]),
        "fy_label": clean(profile["fy_label"]),
        "fiscal_year_end": clean(profile["fiscal_year_end"]),
        "summary": clean(profile["opportunity_summary"]),
        "has_primary": bool(profile["has_primary_opportunity"]),
        "no_opportunity_reason": clean(profile["no_opportunity_reason"]),
        "primary": {
            "product": clean(profile["primary_product"]),
            "label": clean(profile["primary_label"]),
            "status": clean(profile["primary_status"]),
            "action": clean(profile["primary_action"]),
            "confidence_band": clean(profile["primary_confidence_band"]),
        },
        "secondary_product": clean(profile["secondary_product"]),
        "supporting_product": clean(profile["supporting_signal_product"]),
        "commercial_rank": clean(profile["commercial_rank"]),
        "mean_core_confidence": clean(profile["mean_core_confidence"]),
        "high_severity_pillars": clean(profile["high_severity_pillars"]),
        "pillars": pillars,
        "questions": records(
            questions[
                ["question_index", "product", "product_label", "question", "rationale", "selection_slot"]
            ]
        ),
        "diagnostics": records(
            diagnostics[["severity", "product", "diagnostic", "detail"]].head(8)
        ),
    }
    _assert_no_cross_pillar_total(payload)
    return payload


def client_index(store: Tables | None = None) -> list[dict[str, Any]]:
    """The roster, ordered by where a banker should look first."""
    store = store or tables()
    cards = store["client_opportunity_cards"]
    return with_money(
        records(
            cards[
                [
                    "entity_id",
                    "entity_name",
                    "sector",
                    "primary_opportunity",
                    "primary_opportunity_product",
                    "primary_opportunity_score",
                    "primary_opportunity_zar",
                    "confidence_band",
                    "status",
                    "next_action",
                    "sensitivity",
                    "high_severity_flag",
                ]
            ]
        ),
        "primary_opportunity_zar",
    )


# ---------------------------------------------------------------------------
# Page 4 -- sensitivity and model trust
# ---------------------------------------------------------------------------

TRUST_STATEMENTS = [
    {
        "product": assumptions.CASH,
        "verdict": "ROBUST",
        "headline": "Identity-anchored. Nothing moves it.",
        "detail": (
            "Addressable Cash Flow is revenue plus cost of sales — two accounting identities, not "
            "coefficients. No scenario in the 36-run sweep changes it by a rand. This is the only "
            "pillar whose rand figure can be quoted as a single number."
        ),
    },
    {
        "product": assumptions.FX,
        "verdict": "ASSUMPTION_SENSITIVE",
        "headline": "Peer-benchmark. Quote the range, never the point.",
        "detail": (
            "No disclosure states any client's true cross-border activity, so the denominator IS "
            "the coefficient. Across the sweep the portfolio FX opportunity spans 7.4x from "
            "lowest to highest, and the within-pillar ordering falls to rho 0.51 under a median "
            "benchmark."
        ),
    },
    {
        "product": assumptions.TRADE,
        "verdict": "ASSUMPTION_SENSITIVE",
        "headline": "Peer-benchmark. Quote the range, never the point.",
        "detail": (
            "Same construction as FX and the same caveat. The portfolio trade opportunity spans "
            "4.0x across the sweep. Ordering is steadier than FX at rho 0.85, so the ranking is "
            "more usable than the total."
        ),
    },
    {
        "product": assumptions.LENDING,
        "verdict": "ROBUST",
        "headline": "A financing opportunity, not a share of wallet.",
        "detail": (
            "Built from disclosed debt structure: debt falling due, undrawn committed facilities, "
            "the working-capital cycle and capex. Syn Bank's data holds no loan book, so there is "
            "no observed activity to divide and no share exists. Ordering holds at rho 0.997 and "
            "the total moves under 5% even when the capex judgement coefficient moves by a third."
        ),
    },
    {
        "product": assumptions.IB,
        "verdict": "SIGNAL_ONLY",
        "headline": "A ranked signal. No rand figure exists.",
        "detail": (
            "Five percentile-ranked balance-sheet facts produce a mandate-likelihood signal. "
            "Nothing in the data indicates a planned transaction, so no amount is estimated. Its "
            "ordering is identical in all 36 runs because every threshold behind it is a declared "
            "judgement rather than a measured coefficient."
        ),
    },
]


def sensitivity_payload(store: Tables | None = None) -> dict[str, Any]:
    """Page 4. What the model is sure of, and what it is not."""
    store = store or tables()
    spread = store["opportunity_sensitivity_summary"]
    confidence = store["product_confidence"]

    by_product = []
    for product, meta in PILLAR_META.items():
        rows = spread[spread["product"] == product]
        flags = rows["sensitivity_flag"].value_counts().to_dict()
        confidence_row = confidence[confidence["product"] == product].iloc[0]
        statement = next(item for item in TRUST_STATEMENTS if item["product"] == product)
        by_product.append(
            {
                **meta,
                "product": product,
                "verdict": statement["verdict"],
                "headline": statement["headline"],
                "detail": statement["detail"],
                "flags": {str(key): int(value) for key, value in flags.items()},
                "mean_confidence": clean(confidence_row["mean_confidence"]),
                "pct_high": clean(confidence_row["pct_high"]),
                "pct_medium": clean(confidence_row["pct_medium"]),
                "pct_low": clean(confidence_row["pct_low"]),
                "pct_major_diagnostic": clean(confidence_row["pct_major_diagnostic"]),
                "scenarios": clean(rows["scenarios_tested"].max()) if not rows.empty else None,
            }
        )

    widest = (
        spread[spread["estimate_range_pct"].notna()]
        .sort_values("estimate_range_pct", ascending=False)
        .head(10)
        .merge(
            store["opportunity_selection_detail"][
                ["entity_id", "product", "entity_name"]
            ],
            on=["entity_id", "product"],
            how="left",
        )
    )

    robustness = (
        records(store["model_sensitivity_robustness"])
        if store.has("model_sensitivity_robustness")
        else []
    )

    diagnostics = store["model_diagnostics"]
    order = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}
    diagnostics = diagnostics.assign(_order=diagnostics["severity"].map(order)).sort_values(
        "_order"
    )

    return {
        "by_product": by_product,
        "widest": [
            {
                "entity_id": clean(row["entity_id"]),
                "entity_name": clean(row["entity_name"]),
                "product": clean(row["product"]),
                "low": figure(row["estimate_low"]),
                "base": figure(row["estimate_base"]),
                "high": figure(row["estimate_high"]),
                "range_pct": clean(row["estimate_range_pct"]),
                "rank_stability": clean(row["rank_stability"]),
            }
            for _, row in widest.iterrows()
        ],
        "robustness": robustness,
        "diagnostics": records(
            diagnostics[
                ["severity", "scope", "diagnostic", "entity_id", "entity_name", "product", "detail"]
            ]
        ),
        "diagnostic_counts": {
            str(key): int(value)
            for key, value in store["model_diagnostics"]["severity"].value_counts().items()
        },
        "methodology": {
            "benchmark": (
                "Where no accounting identity fixes a coefficient it is measured from the "
                "client's peers at the 75th percentile — with that client removed from the "
                "population. Including it is circular in both directions: a heavily penetrated "
                "client raises the benchmark it is then judged against; a dormant one drags it "
                "down and makes its own share look healthy."
            ),
            "sector": (
                "A sector benchmark is used only where at least three peers remain after that "
                "exclusion, otherwise the portfolio population is used and the reason is recorded "
                "per client. A sector frontier built from one or two companies would be a "
                "restatement of those companies."
            ),
            "sweep": (
                "Every rand estimate is rebuilt under 36 configurations varying the benchmark "
                "percentile (median / P75 / P80), leave-one-out versus self-inclusive peers, "
                "sector versus portfolio scope, and the capex debt-funded share (0.20 / 0.30 / "
                "0.40)."
            ),
            "no_totals": (
                "The five pillars are never added. Two overlap on the SWIFT channel by an amount "
                "the supplied data cannot resolve, and the five rand figures are measured on "
                "incomparable bases."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Page 5 -- product analysis
# ---------------------------------------------------------------------------


def _corridor(store: Tables, scope: str, dimension: str, limit: int = 8) -> list[dict[str, Any]]:
    """Observed counterparty breakdowns. Descriptive: never an estimate."""
    breakdown = store["client_corridor_breakdown"]
    columns = set(breakdown.columns)
    if not {"dimension", "scope"} <= columns:
        return []
    rows = breakdown[
        (breakdown["scope"] == scope) & (breakdown["dimension"] == dimension)
    ]
    if rows.empty:
        return []
    value_column = "value_zar" if "value_zar" in columns else "volume_zar"
    if value_column not in columns:
        return []
    grouped = (
        rows.groupby("bucket")[value_column].sum().sort_values(ascending=False).head(limit)
    )
    return [
        {"label": str(index), "value": clean(value), "display": money(value)}
        for index, value in grouped.items()
    ]


def product_payload(product: str, store: Tables | None = None) -> dict[str, Any]:
    """Page 5. The analytics that suit the selected pillar."""
    store = store or tables()
    if product not in PILLAR_META:
        raise KeyError(product)
    meta = PILLAR_META[product]
    detail = store["opportunity_selection_detail"]
    rows = detail[detail["product"] == product].copy()
    spread = store["opportunity_sensitivity_summary"]
    spread = spread[spread["product"] == product].set_index("entity_id")
    features = store["client_features"].set_index("entity_id")

    ranked = rows.sort_values("selection_score", ascending=False)
    clients = []
    for _, row in ranked.iterrows():
        entity_id = row["entity_id"]
        client_spread = spread.loc[entity_id] if entity_id in spread.index else None
        clients.append(
            {
                "entity_id": clean(entity_id),
                "entity_name": clean(row["entity_name"]),
                "sector": clean(row["sector"]),
                "observed": figure(row["observed_zar"]),
                "addressable": figure(row["addressable_zar"]),
                "opportunity": figure(row["opportunity_zar"]),
                "share": figure(row["share"], "pct"),
                "confidence": clean(row["confidence"]),
                "confidence_band": clean(row["confidence_band"]),
                "status": clean(row["opportunity_status"]),
                "action": clean(row["status_action"]),
                "score": clean(row["selection_score"]),
                "sensitivity_flag": clean(row["sensitivity_flag"]),
                "low": figure(client_spread["estimate_low"]) if client_spread is not None else figure(None),
                "high": figure(client_spread["estimate_high"]) if client_spread is not None else figure(None),
            }
        )

    #: Descriptive observed detail, per pillar. Read from the feature layer and
    #: clearly separated in the payload from anything the model estimated.
    descriptive: dict[str, Any] = {}
    if product == assumptions.FX:
        pairs = [
            ("xb_pair_usd_volume_zar_fy", "USD"),
            ("xb_pair_eur_volume_zar_fy", "EUR"),
            ("xb_pair_gbp_volume_zar_fy", "GBP"),
            ("xb_pair_aed_volume_zar_fy", "AED"),
            ("xb_pair_cny_volume_zar_fy", "CNY"),
        ]
        descriptive["currency_pairs"] = [
            {
                "label": label,
                "value": clean(features[column].sum()),
                "display": money(features[column].sum()),
            }
            for column, label in pairs
            if column in features.columns
        ]
        descriptive["countries"] = _corridor(store, "fy", "country")
        descriptive["direction"] = [
            {
                "label": "Inbound",
                "value": clean(features["xb_inbound_volume_zar_fy"].sum()),
                "display": money(features["xb_inbound_volume_zar_fy"].sum()),
            },
            {
                "label": "Outbound",
                "value": clean(features["xb_outbound_volume_zar_fy"].sum()),
                "display": money(features["xb_outbound_volume_zar_fy"].sum()),
            },
        ]
    elif product == assumptions.TRADE:
        instruments = [
            ("tf_letters_of_credit_value_zar_fy", "Letters of credit"),
            ("tf_guarantees_value_zar_fy", "Guarantees"),
            ("tf_export_collections_value_zar_fy", "Export collections"),
        ]
        descriptive["instruments"] = [
            {
                "label": label,
                "value": clean(features[column].sum()),
                "display": money(features[column].sum()),
            }
            for column, label in instruments
            if column in features.columns
        ]
        descriptive["direction"] = [
            {
                "label": "Import",
                "value": clean(features["tf_import_value_zar_fy"].sum()),
                "display": money(features["tf_import_value_zar_fy"].sum()),
            },
            {
                "label": "Export",
                "value": clean(features["tf_export_value_zar_fy"].sum()),
                "display": money(features["tf_export_value_zar_fy"].sum()),
            },
        ]
        descriptive["countries"] = _corridor(store, "fy", "country")
    elif product == assumptions.CASH:
        legs = [
            ("txn_collections_domestic_volume_zar_fy", "Collections"),
            ("txn_supplier_payments_domestic_volume_zar_fy", "Supplier payments"),
        ]
        descriptive["legs"] = [
            {
                "label": label,
                "value": clean(features[column].sum()),
                "display": money(features[column].sum()),
            }
            for column, label in legs
            if column in features.columns
        ]
        descriptive["excluded"] = [
            {
                "label": label,
                "value": clean(features[column].sum()),
                "display": money(features[column].sum()),
                "why": why,
            }
            for column, label, why in (
                (
                    "txn_intercompany_sweeps_volume_zar_fy",
                    "Intercompany sweeps",
                    "No external anchor exists, so it sits outside the denominator",
                ),
                (
                    "txn_payroll_volume_zar_fy",
                    "Payroll",
                    "No employee-cost field exists; carried as a mandate signal",
                ),
                ("txn_tax_volume_zar_fy", "Tax", "No tax charge is disclosed"),
                (
                    "txn_swift_channel_volume_zar_fy",
                    "SWIFT channel",
                    "Overlaps FX by an unresolvable amount; counted in neither pillar",
                ),
            )
            if column in features.columns
        ]
    elif product == assumptions.LENDING:
        components = [
            ("debt_current_zar", "Refinancing", "Debt classified current"),
            ("undrawn_facilities_zar", "Undrawn facilities", "Committed headroom in use elsewhere"),
            ("working_capital_zar", "Working capital", "Cycle funded by short-term debt"),
            ("capex_zar", "Capex", "Investment programme"),
        ]
        descriptive["components"] = [
            {
                "label": label,
                "why": why,
                "value": clean(features[column].sum()),
                "display": money(features[column].sum()),
            }
            for column, label, why in components
            if column in features.columns
        ]
    elif product == assumptions.IB:
        profiles = store["client_opportunity_intelligence"]
        counts = profiles["ib_opportunity_type"].value_counts()
        descriptive["categories"] = [
            {"label": str(index), "value": int(value)} for index, value in counts.items()
        ]
        descriptive["signals"] = [
            {
                "entity_id": clean(row["entity_id"]),
                "entity_name": clean(row["entity_name"]),
                "signal": clean(row["ib_signal_score"]),
                "category": clean(row["ib_opportunity_type"]),
                "confidence_band": clean(row["ib_confidence_band"]),
            }
            for _, row in profiles.sort_values(
                "ib_signal_score", ascending=False
            ).head(10).iterrows()
        ]

    summary = store["portfolio_summary"].set_index("product").loc[product]
    return {
        **meta,
        "product": product,
        "product_class": clean(summary["product_class"]),
        "basis_note": clean(summary["estimate_basis"]),
        "observed": figure(summary["total_observed_zar"]),
        "addressable": figure(summary["total_estimate_zar"]),
        "opportunity": figure(summary["total_gap_zar"]),
        "share": figure(summary["portfolio_share"], "pct"),
        "clients": clients,
        "descriptive": descriptive,
    }
