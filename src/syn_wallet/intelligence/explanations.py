"""Six-part explanations, generated deterministically from published fields only.

Every explanation has the same shape -- WHAT, WHY, EVIDENCE, CONFIDENCE,
LIMITATION, NEXT ACTION -- because a relationship manager reading twenty of them
should not have to work out where the caveat is each time.

Three rules govern what may appear:

**No value is invented.** Every number in every sentence is read from
``opportunity_engine.parquet`` or from the sensitivity view built from
``model_sensitivity.parquet``. Each explanation publishes the list of source
fields it read, so a reader can check any figure against the contract.

**No claim of ownership.** A gap is addressable activity not observed in Syn
Bank's data. It is never described as held by a competitor, lost, or winnable
back -- the supplied data cannot support any of those, and :data:`FORBIDDEN_PHRASES`
is checked against the generated text by a test.

**No pillar borrows another's language.** Cash management gets "Addressable Cash
Flow", FX and trade get "peer-benchmark addressable", lending gets "financing
opportunity" and never a share, investment banking gets "opportunity signal" and
never a rand. The per-pillar renderers below are separate functions for exactly
that reason.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..wallet import assumptions
from ..wallet.common import count, pct, zar
from . import config

#: Columns published in ``opportunity_explanations.parquet``.
EXPLANATION_COLUMNS = (
    "entity_id",
    "entity_name",
    "sector",
    "fy_label",
    "product",
    "product_label",
    "product_class",
    "pillar_role",
    "selection_slot",
    "opportunity_status",
    "status_action",
    "what",
    "why",
    "evidence",
    "confidence_band",
    "confidence_statement",
    "limitation",
    "next_action",
    "narrative",
    "source_fields",
    "intelligence_version",
    "methodology_version",
)


# ---------------------------------------------------------------------------
# WHAT -- product and magnitude
# ---------------------------------------------------------------------------


def _what(row: pd.Series) -> str:
    label = config.DENOMINATOR_LABEL[row["product"]]
    if row["product"] == assumptions.IB:
        return (
            f"{row['product_label']}: an {label} of "
            f"{row['signal_score']:.2f} on a 0-1 scale, ranked against the other nineteen "
            "clients in this portfolio. No rand amount is estimated."
        )
    if row["product"] == assumptions.LENDING:
        return (
            f"{row['product_label']}: a {label} of "
            f"{zar(row['opportunity_zar'])} inside a twelve-month horizon."
        )
    return (
        f"{row['product_label']}: {zar(row['opportunity_zar'])} of unserved activity against "
        f"{zar(row['addressable_zar'])} of {label}."
    )


# ---------------------------------------------------------------------------
# WHY -- internal activity, external signals, share where valid
# ---------------------------------------------------------------------------


def _why_cash(row: pd.Series) -> str:
    return (
        f"The client's Addressable Cash Flow of {zar(row['addressable_zar'])} is its own annual "
        "operating turnover: revenue collected in plus cost of sales paid out, both of which must "
        f"move through a bank account. Syn Bank currently handles {pct(row['share'])} of the "
        f"client's observable addressable cash flow — {zar(row['observed_zar'])} in "
        f"{row['fy_label']} — leaving {zar(row['opportunity_zar'])} of operating flow that Syn "
        "Bank's data does not show. The denominator is an accounting identity, not an estimate, "
        "which is why this is the best-evidenced pillar in the model."
    )


def _why_fx(row: pd.Series) -> str:
    return (
        f"Syn Bank routed {zar(row['observed_zar'])} of cross-border payments for this client in "
        f"{row['fy_label']}. Scaling the client's own disclosed foreign-revenue and procurement "
        "exposure by the settlement intensity of its peers -- measured at the upper quartile of "
        f"{count(row['benchmark_n'])} {row['benchmark_level']} peers, with this client excluded "
        f"from that population -- puts peer-benchmark addressable FX activity at "
        f"{zar(row['addressable_zar'])}. That implies a share of {pct(row['share'])} and "
        f"{zar(row['opportunity_zar'])} of potential headroom."
    )


def _why_trade(row: pd.Series) -> str:
    return (
        f"Syn Bank issued {zar(row['observed_zar'])} of trade instruments for this client in "
        f"{row['fy_label']}. Scaling the client's procurement base, export exposure and revenue "
        "by peer issuance intensity -- measured across "
        f"{count(row['benchmark_n'])} {row['benchmark_level']} peers, with this client excluded "
        "-- puts peer-benchmark addressable trade-finance activity at "
        f"{zar(row['addressable_zar'])}, a share of {pct(row['share'])} and "
        f"{zar(row['opportunity_zar'])} of potential headroom."
    )


def _why_lending(row: pd.Series) -> str:
    return (
        f"The client's disclosed debt structure indicates {zar(row['opportunity_zar'])} of "
        "financing decisions falling inside a twelve-month horizon: debt classified as current, "
        "undrawn committed facilities, the working-capital cycle and capex. Syn Bank's supplied "
        "datasets contain no loan book, so there is no observed lending activity to divide and "
        "no share of wallet is computed. This is a financing-need indicator, not a wallet."
    )


def _why_ib(row: pd.Series) -> str:
    category = row.get("ib_opportunity_type") or "none_supported"
    if category == "none_supported":
        detail = (
            "No mandate category is assigned: the disclosed balance sheet does not meet any of "
            "the declared thresholds."
        )
    else:
        detail = f"The disclosed balance sheet supports a {category.replace('_', ' ')} category."
    return (
        f"An investment-banking opportunity signal of {row['signal_score']:.2f}, built from five "
        "percentile-ranked balance-sheet facts: scale, leverage, near-term maturity, capex "
        f"intensity and syndicate breadth. {detail} Nothing in the supplied data indicates a "
        "planned issue, disposal or acquisition, so no rand amount is estimated and none should "
        "be quoted."
    )


WHY_BY_PRODUCT = {
    assumptions.CASH: _why_cash,
    assumptions.FX: _why_fx,
    assumptions.TRADE: _why_trade,
    assumptions.LENDING: _why_lending,
    assumptions.IB: _why_ib,
}


# ---------------------------------------------------------------------------
# EVIDENCE -- the actual fields, named
# ---------------------------------------------------------------------------

#: Fields cited per pillar. Published alongside the text so a reader can check
#: every figure against ``opportunity_engine.parquet``.
EVIDENCE_FIELDS = {
    assumptions.CASH: (
        "observed_zar",
        "addressable_zar",
        "share",
        "opportunity_zar",
        "confidence",
        "diagnostic_count",
    ),
    assumptions.FX: (
        "observed_zar",
        "addressable_zar",
        "share",
        "opportunity_zar",
        "benchmark_level",
        "benchmark_n",
        "confidence",
        "diagnostic_count",
    ),
    assumptions.TRADE: (
        "observed_zar",
        "addressable_zar",
        "share",
        "opportunity_zar",
        "benchmark_level",
        "benchmark_n",
        "confidence",
        "diagnostic_count",
    ),
    assumptions.LENDING: (
        "opportunity_zar",
        "addressable_zar",
        "confidence",
        "diagnostic_count",
        "share_basis",
    ),
    assumptions.IB: ("signal_score", "confidence", "diagnostic_count", "share_basis"),
}


def _format_field(row: pd.Series, field: str) -> str:
    value = row[field]
    if pd.isna(value):
        return f"`{field}` = not available"
    if field in ("share",):
        return f"`{field}` = {pct(value)}"
    if field in ("confidence", "signal_score"):
        return f"`{field}` = {float(value):.2f}"
    if field in ("benchmark_n", "diagnostic_count"):
        return f"`{field}` = {count(value)}"
    if field.endswith("_zar"):
        return f"`{field}` = {zar(value)}"
    return f"`{field}` = {value}"


def _evidence(row: pd.Series, sensitivity: pd.Series | None) -> str:
    fields = EVIDENCE_FIELDS[row["product"]]
    parts = [_format_field(row, field) for field in fields]
    text = (
        f"From `opportunity_engine.parquet` for {row['entity_id']} x {row['product']}: "
        + "; ".join(parts)
        + "."
    )
    if row["diagnostic_count"] and row["diagnostic_count"] > 0:
        flags = row["diagnostic_flags"] or ""
        severity = "including at least one HIGH severity" if row["high_severity_diagnostic"] else "none HIGH severity"
        text += (
            f" {count(row['diagnostic_count'])} model diagnostic"
            f"{'s' if row['diagnostic_count'] != 1 else ''} recorded ({severity})"
        )
        text += f"; flags `{flags}`." if flags else "."
    if sensitivity is not None and pd.notna(sensitivity.get("estimate_base")):
        text += (
            f" Across {count(sensitivity['scenarios_tested'])} tested scenarios in "
            f"`model_sensitivity.parquet`, the addressable figure ranges "
            f"{zar(sensitivity['estimate_low'])} to {zar(sensitivity['estimate_high'])} "
            f"around a base of {zar(sensitivity['estimate_base'])}."
        )
    return text


# ---------------------------------------------------------------------------
# LIMITATION
# ---------------------------------------------------------------------------


def _limitation(row: pd.Series, sensitivity: pd.Series | None) -> str:
    text = config.DENOMINATOR_CAVEAT[row["product"]]

    if row["product"] in (assumptions.FX, assumptions.TRADE):
        flag = (
            sensitivity["sensitivity_flag"]
            if sensitivity is not None
            else config.NOT_APPLICABLE
        )
        if flag == config.SENSITIVE:
            text += (
                " The model indicates potential headroom, but the estimate is "
                "benchmark-sensitive: across the tested assumptions the addressable figure spans "
                f"{zar(sensitivity['estimate_low'])} to {zar(sensitivity['estimate_high'])} "
                f"({pct(sensitivity['estimate_range_pct'], 0)} of the base case), and the "
                f"client's position within this pillar "
                f"{config.RANK_STABILITY_PHRASE[sensitivity['rank_stability']]}. Present it as a "
                "range, not as a single figure."
            )
        elif flag in (config.STABLE, config.MODERATE):
            text += (
                f" Across the tested assumptions this estimate is "
                f"{config.SENSITIVITY_PHRASE[flag]}, spanning "
                f"{zar(sensitivity['estimate_low'])} to {zar(sensitivity['estimate_high'])}."
            )

    if row["confidence_band"] == "LOW":
        text += (
            " Confidence is LOW, so this figure should be validated against the client's own "
            "disclosure before it is used in a commercial conversation."
        )
    if row["high_severity_diagnostic"]:
        text += (
            " A HIGH-severity model diagnostic is open on this estimate; review it before "
            "quoting the rand amount."
        )
    return text


# ---------------------------------------------------------------------------
# NEXT ACTION
# ---------------------------------------------------------------------------

NEXT_ACTION = {
    assumptions.CASH: (
        "Establish where the rest of the client's operating collections and supplier payments "
        "are settled, and whether Syn Bank is positioned on the primary operating account or "
        "only on secondary flows."
    ),
    assumptions.FX: (
        "Establish the client's actual cross-border settlement volume and how it is split across "
        "its banking panel, then test the peer-benchmark estimate against what the client says."
    ),
    assumptions.TRADE: (
        "Establish the client's actual annual trade-instrument issuance and the size of its "
        "existing facilities, then test the peer-benchmark estimate against what the client says."
    ),
    assumptions.LENDING: (
        "Establish the maturity profile of the debt falling due inside twelve months, how much "
        "is already committed, and whether the undrawn facilities are being renewed."
    ),
    assumptions.IB: (
        "Treat as background for a relationship conversation. Confirm whether any capital-markets "
        "or corporate-finance activity is planned before allocating coverage effort."
    ),
}


def _next_action(row: pd.Series) -> str:
    return f"{row['status_action']}. {NEXT_ACTION[row['product']]}"


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _narrative(parts: dict[str, str]) -> str:
    return (
        f"WHAT: {parts['what']}\n\n"
        f"WHY: {parts['why']}\n\n"
        f"EVIDENCE: {parts['evidence']}\n\n"
        f"CONFIDENCE: {parts['confidence_statement']}\n\n"
        f"LIMITATION: {parts['limitation']}\n\n"
        f"NEXT ACTION: {parts['next_action']}"
    )


def build(scored: pd.DataFrame, sensitivity: pd.DataFrame) -> pd.DataFrame:
    """One six-part explanation per client x product."""
    sensitivity_index = sensitivity.set_index(["entity_id", "product"])
    rows: list[dict[str, Any]] = []

    for _, row in scored.iterrows():
        key = (row["entity_id"], row["product"])
        sensitivity_row = (
            sensitivity_index.loc[key] if key in sensitivity_index.index else None
        )
        parts = {
            "what": _what(row),
            "why": WHY_BY_PRODUCT[row["product"]](row),
            "evidence": _evidence(row, sensitivity_row),
            "confidence_statement": (
                f"{config.CONFIDENCE_PHRASE[row['confidence_band']]} "
                f"(score {float(row['confidence']):.2f})."
            ),
            "limitation": _limitation(row, sensitivity_row),
            "next_action": _next_action(row),
        }
        rows.append(
            {
                "entity_id": row["entity_id"],
                "entity_name": row["entity_name"],
                "sector": row["sector"],
                "fy_label": row["fy_label"],
                "product": row["product"],
                "product_label": row["product_label"],
                "product_class": row["product_class"],
                "pillar_role": row["pillar_role"],
                "selection_slot": row["selection_slot"],
                "opportunity_status": row["opportunity_status"],
                "status_action": row["status_action"],
                **parts,
                "confidence_band": row["confidence_band"],
                "narrative": _narrative(parts),
                "source_fields": ", ".join(EVIDENCE_FIELDS[row["product"]]),
                "intelligence_version": config.INTELLIGENCE_VERSION,
                "methodology_version": row["methodology_version"],
            }
        )

    return pd.DataFrame(rows)[list(EXPLANATION_COLUMNS)]
