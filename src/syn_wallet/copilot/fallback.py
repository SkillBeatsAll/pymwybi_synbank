"""Deterministic answers, written without a language model.

The copilot must work with no API key, no network, and no budget. It must also
work when the model returns something that fails validation. In all three cases
the banker gets an answer built here: real figures, templated prose, and a
notice saying which mode produced it.

This is not a degraded stub. The commercial intelligence layer already generated
six-part explanations and client-specific questions for every client x pillar,
so the fallback assembles genuine content -- it simply reads less fluently than
generated prose. The figures are identical either way, which is the point: the
language model was never the thing producing them.
"""

from __future__ import annotations

import pandas as pd

from ..wallet import assumptions
from ..wallet.common import pct, zar
from . import router as router_module
from .retrieval import Retrieved

MAX_OPPORTUNITIES = 3


def _fmt_zar(value) -> str:
    return zar(float(value)) if pd.notna(value) else "not available"


def _sensitivity_line(sensitivity: pd.DataFrame, entity_id: str, product: str) -> str:
    if sensitivity.empty:
        return ""
    rows = sensitivity[
        (sensitivity["entity_id"] == entity_id) & (sensitivity["product"] == product)
    ]
    if rows.empty:
        return ""
    row = rows.iloc[0]
    if pd.isna(row["estimate_base"]) or row["sensitivity_flag"] == "NOT_APPLICABLE":
        return ""
    if row["sensitivity_flag"] == "STABLE":
        return "Stable across all tested assumptions."
    return (
        f"Sensitive to benchmark assumptions: across {int(row['scenarios_tested'])} tested "
        f"configurations the addressable figure ranges {_fmt_zar(row['estimate_low'])} to "
        f"{_fmt_zar(row['estimate_high'])} around a base of {_fmt_zar(row['estimate_base'])}."
    )


def _briefing(retrieved: Retrieved) -> str:
    client = retrieved.clients.iloc[0]
    lines: list[str] = []

    lines.append("## Executive Summary")
    lines.append("")
    lines.append(str(client["opportunity_summary"]))
    lines.append("")

    lines.append("## Relationship Snapshot")
    lines.append("")
    lines.append(
        f"Syn Bank handled {_fmt_zar(client['cash_observed_zar'])} of "
        f"{client['entity_name']}'s operating collections and supplier payments in "
        f"{client['fy_label']}, against an Addressable Cash Flow of "
        f"{_fmt_zar(client['addressable_cash_flow_zar'])} — a share of "
        f"{pct(client['cash_share'])}. Addressable Cash Flow is the client's own operating "
        "turnover, not bank income."
    )
    if pd.notna(client["fx_observed_zar"]):
        lines.append(
            f"- FX / Global Markets: {_fmt_zar(client['fx_observed_zar'])} of cross-border "
            f"payments routed, a share of {pct(client['fx_share'])} of peer-benchmark "
            "addressable FX activity."
        )
    if pd.notna(client["trade_observed_zar"]):
        lines.append(
            f"- Trade Finance: {_fmt_zar(client['trade_observed_zar'])} of instruments issued, "
            f"a share of {pct(client['trade_share'])} of peer-benchmark addressable "
            "trade-finance activity."
        )
    lines.append(
        "- Lending: no observed activity — Syn Bank's supplied data contains no loan book, so "
        "no share of wallet exists for this pillar."
    )
    lines.append("")

    lines.append("## Priority Opportunities")
    lines.append("")
    explanations = retrieved.explanations.set_index("product") if not retrieved.explanations.empty else None
    shown = 0
    for _, row in retrieved.pillars.iterrows():
        if shown >= MAX_OPPORTUNITIES:
            break
        if row["opportunity_status"] == "NO_HEADROOM_DEMONSTRATED":
            continue
        shown += 1
        product = row["product"]
        lines.append(f"### {row['product_label']}")
        lines.append("")
        if product == assumptions.IB:
            lines.append(
                "- **Opportunity:** investment-banking opportunity signal only. No rand amount "
                "and no share of wallet exist for this pillar."
            )
        elif product == assumptions.LENDING:
            lines.append(
                f"- **Opportunity:** financing opportunity of "
                f"{_fmt_zar(row['opportunity_zar'])} inside a twelve-month horizon."
            )
        else:
            lines.append(
                f"- **Opportunity:** {_fmt_zar(row['opportunity_zar'])} of activity not observed "
                f"in Syn Bank's data, against {_fmt_zar(row['addressable_zar'])} addressable."
            )
        lines.append(
            f"- **Confidence:** {row['confidence_band']} ({float(row['confidence']):.2f})."
        )
        if explanations is not None and product in explanations.index:
            explanation = explanations.loc[product]
            lines.append(f"- **Why:** {explanation['why']}")
            lines.append(f"- **Evidence:** {explanation['evidence']}")
            lines.append(f"- **Limitation:** {explanation['limitation']}")
            lines.append(f"- **Recommended action:** {explanation['next_action']}")
        else:
            lines.append(f"- **Recommended action:** {row['status_action']}.")
        sensitivity = _sensitivity_line(retrieved.sensitivity, row["entity_id"], product)
        if sensitivity:
            lines.append(f"- **Sensitivity:** {sensitivity}")
        lines.append("")

    if shown == 0:
        lines.append(
            "No pillar demonstrated headroom for this client. Syn Bank already handles "
            "essentially all of the activity the model can size, so this is a retention "
            "relationship rather than a growth one."
        )
        lines.append("")

    if not retrieved.questions.empty:
        lines.append("## Banker Questions")
        lines.append("")
        for _, question in retrieved.questions.iterrows():
            lines.append(f"{int(question['question_index'])}. {question['question']}")
        lines.append("")

    lines.append("## Model Caveats")
    lines.append("")
    caveats = _caveats(retrieved)
    lines.extend(f"- {caveat}" for caveat in caveats)
    return "\n".join(lines)


def _caveats(retrieved: Retrieved) -> list[str]:
    caveats: list[str] = []
    products = set(retrieved.pillars["product"]) if not retrieved.pillars.empty else set()
    if products & {assumptions.FX, assumptions.TRADE}:
        caveats.append(
            "FX and trade-finance figures are peer-benchmark estimates, not disclosed totals: "
            "no disclosure states this client's true activity, so the benchmark choice is the "
            "denominator."
        )
    if not retrieved.pillars.empty and (
        retrieved.pillars["confidence_band"] == "LOW"
    ).any():
        caveats.append(
            "At least one figure above sits on LOW confidence and should be validated against "
            "the client's own disclosure before it is used in a commercial conversation."
        )
    if not retrieved.diagnostics.empty and (
        retrieved.diagnostics["severity"] == "HIGH"
    ).any():
        first = retrieved.diagnostics[retrieved.diagnostics["severity"] == "HIGH"].iloc[0]
        caveats.append(f"Open HIGH-severity diagnostic: {first['diagnostic']} — {first['detail']}")
    caveats.append(
        "An opportunity is addressable activity not observed in Syn Bank's data. It is not "
        "evidence that another bank holds it, and it is not business Syn Bank has booked."
    )
    return caveats[:4]


def _explanation(retrieved: Retrieved) -> str:
    if retrieved.explanations.empty:
        return _ranked(retrieved)
    row = retrieved.explanations.iloc[0]
    parts = [
        f"**{row['entity_name']} — {row['product_label']}**",
        "",
        row["what"],
        "",
        row["why"],
        "",
        f"**Evidence.** {row['evidence']}",
        "",
        f"**Confidence.** {row['confidence_statement']}",
        "",
        f"**Limitation.** {row['limitation']}",
        "",
        f"**Next action.** {row['next_action']}",
    ]
    return "\n".join(parts)


def _ranked(retrieved: Retrieved) -> str:
    if retrieved.pillars.empty:
        return _nothing_found(retrieved)
    lines = ["Ranked by the model's own selection score, best first.", ""]
    for position, (_, row) in enumerate(retrieved.pillars.iterrows(), start=1):
        product = row["product"]
        if product == assumptions.IB:
            figure = "signal only, no rand amount"
        elif product == assumptions.LENDING:
            figure = f"financing opportunity {_fmt_zar(row['opportunity_zar'])}"
        else:
            figure = f"{_fmt_zar(row['opportunity_zar'])} not observed by Syn Bank"
        lines.append(
            f"{position}. **{row['entity_name']}** — {row['product_label']}: {figure}; "
            f"{row['confidence_band']} confidence; status {row['opportunity_status']} "
            f"({row['status_action']})."
        )
        sensitivity = _sensitivity_line(retrieved.sensitivity, row["entity_id"], product)
        if sensitivity and "Stable" not in sensitivity:
            lines.append(f"   {sensitivity}")
    lines.append("")
    lines.append(
        "These figures are measured on different bases per pillar and must not be added "
        "together. An opportunity is addressable activity not observed in Syn Bank's data."
    )
    return "\n".join(lines)


def _sensitivity(retrieved: Retrieved) -> str:
    if retrieved.sensitivity.empty:
        return _nothing_found(retrieved)
    lines = ["**How reliable are these estimates?**", ""]
    names = (
        dict(zip(retrieved.pillars["entity_id"], retrieved.pillars["entity_name"]))
        if not retrieved.pillars.empty
        else {}
    )
    for _, row in retrieved.sensitivity.iterrows():
        name = names.get(row["entity_id"], row["entity_id"])
        label = assumptions.PRODUCT_LABELS.get(row["product"], row["product"])
        if pd.isna(row["estimate_base"]):
            lines.append(
                f"- **{name} — {label}:** no rand estimate exists for this pillar, so there is "
                "nothing to be sensitive."
            )
            continue
        lines.append(
            f"- **{name} — {label}:** base case {_fmt_zar(row['estimate_base'])}, ranging "
            f"{_fmt_zar(row['estimate_low'])} to {_fmt_zar(row['estimate_high'])} across "
            f"{int(row['scenarios_tested'])} tested model configurations "
            f"({row['sensitivity_phrase']}). Rank stability: {row['rank_stability']} — "
            f"{row['rank_stability_phrase']}."
        )
    lines.append("")
    lines.append(
        "FX and trade-finance estimates move because no disclosure states the client's true "
        "activity, so the peer-benchmark choice is the denominator. Cash Management does not "
        "move at all: both its coefficients are accounting identities."
    )
    return "\n".join(lines)


def _meeting(retrieved: Retrieved) -> str:
    if retrieved.questions.empty:
        return _nothing_found(retrieved)
    client = retrieved.clients.iloc[0] if not retrieved.clients.empty else None
    lines: list[str] = []
    if client is not None:
        lines.append(str(client["opportunity_summary"]))
        lines.append("")
    lines.append("**Questions to put to the client**")
    lines.append("")
    for _, row in retrieved.questions.iterrows():
        lines.append(f"{int(row['question_index'])}. {row['question']}")
        lines.append(f"   *Why it matters:* {row['rationale']}")
        lines.append("")
    return "\n".join(lines)


def _methodology(retrieved: Retrieved) -> str:
    if not retrieved.methodology:
        return _nothing_found(retrieved)
    return "\n\n".join(retrieved.methodology)


def _executive(retrieved: Retrieved) -> str:
    lines = ["**Portfolio position**", ""]
    if not retrieved.portfolio.empty:
        for _, row in retrieved.portfolio.iterrows():
            if row["section"] != "portfolio_position":
                continue
            label = row["product_label"] if pd.notna(row["product_label"]) else "portfolio"
            lines.append(f"- {label} — {row['metric']}: {row['value_text']}")
        lines.append("")
    lines.append("**Top opportunities**")
    lines.append("")
    lines.append(_ranked(retrieved))
    lines.append("")
    lines.append(
        "The five pillars are measured on incomparable bases and are never added. There is no "
        "portfolio total and none can be constructed."
    )
    return "\n".join(lines)


def _nothing_found(retrieved: Retrieved) -> str:
    if retrieved.notes:
        return "\n".join(retrieved.notes)
    return (
        "Nothing in the analytical outputs matches that question. The portfolio covers 20 "
        "JSE-listed clients across five product pillars; try naming a client, a product, or a "
        "sector."
    )


#: Intent to renderer.
RENDERERS = {
    router_module.CLIENT_BRIEFING: _briefing,
    router_module.OPPORTUNITY_EXPLANATION: _explanation,
    router_module.PORTFOLIO_QUERY: _ranked,
    router_module.PRODUCT_QUERY: _ranked,
    router_module.SENSITIVITY_QUERY: _sensitivity,
    router_module.MEETING_PREPARATION: _meeting,
    router_module.METHODOLOGY_QUERY: _methodology,
    router_module.EXECUTIVE_SUMMARY: _executive,
}


def render(retrieved: Retrieved) -> str:
    """The deterministic answer for this retrieval."""
    if retrieved.is_empty:
        return _nothing_found(retrieved)
    renderer = RENDERERS.get(retrieved.route.intent, _ranked)
    if renderer is _briefing and retrieved.clients.empty:
        return _ranked(retrieved)
    return renderer(retrieved)
