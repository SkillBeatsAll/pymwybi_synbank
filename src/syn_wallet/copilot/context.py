"""Render retrieved rows as the compact text block the model is allowed to see.

Two jobs, and the second matters more than it looks.

**Render.** Turn dataframes into labelled lines a language model reads reliably:
one fact per line, units attached, no tables, no JSON. Figures are pre-formatted
here so the model never has to render a number itself -- ``R8.75tn`` is copied,
not computed.

**Enumerate.** Every figure written into the context is also recorded in
:attr:`ContextBundle.figures`. That set is what :mod:`.validation` checks the
generated answer against: any currency amount in the answer that is not in the
set was invented. Building the allow-list at render time, from the same strings
the model sees, is the only way to make that check exact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..wallet import assumptions
from ..wallet.common import pct, zar
from . import config
from .retrieval import Retrieved

#: Matches any rand amount as this system renders it: R8.75tn, R443.98bn,
#: R61.4m, R11,048, -R9.25bn.
#:
#: Two details earn their keep. A **digit must follow the R**, because an
#: earlier ``[\d,]+`` also matched a lone comma -- so "Howeve*r,*" was extracted
#: as a currency figure and every answer containing the word "however" failed
#: validation for an invented amount that was really an English adverb. And the
#: **lookbehind** stops the case-insensitive ``r`` from matching mid-word, which
#: would turn "ove*r 3*00 clients" into a rand figure.
CURRENCY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])-?R\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:tn|bn|m|k))?\b",
    re.IGNORECASE,
)
#: Matches a percentage as this system renders it: 0.41%, 28.13%, 110%.
PERCENT_PATTERN = re.compile(r"\d+(?:\.\d+)?\s?%")


@dataclass
class ContextBundle:
    """The rendered context, plus everything needed to police the answer."""

    text: str
    figures: set[str] = field(default_factory=set)
    entity_ids: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    token_estimate: int = 0
    truncated: bool = False
    sections: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_ids": list(self.entity_ids),
            "products": list(self.products),
            "token_estimate": self.token_estimate,
            "truncated": self.truncated,
            "sections": list(self.sections),
            "figure_count": len(self.figures),
        }


def extract_figures(text: str) -> set[str]:
    """Every currency and percentage token in a string, normalised.

    Normalisation strips spaces and lowercases the magnitude suffix, so that
    ``R8.75 BN`` in an answer matches ``R8.75bn`` in the context. It does not
    round: ``R8.75bn`` and ``R8.8bn`` are different figures and must stay so.
    """
    found = set()
    for match in CURRENCY_PATTERN.findall(text):
        found.add(_normalise(match))
    for match in PERCENT_PATTERN.findall(text):
        found.add(_normalise(match))
    return found


def _normalise(token: str) -> str:
    return token.replace(" ", "").replace(",", "").lower().rstrip(".")


def _fmt(value: Any, kind: str = "zar") -> str:
    """Format one cell the way the whole system formats it."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "not available"
    try:
        if pd.isna(value):
            return "not available"
    except (TypeError, ValueError):
        pass
    if kind == "zar":
        return zar(float(value))
    if kind == "pct":
        return pct(float(value))
    if kind == "pct0":
        return pct(float(value), 0)
    if kind == "score":
        return f"{float(value):.2f}"
    if kind == "int":
        return f"{int(value):,}"
    return str(value)


class ContextBuilder:
    """Renders a :class:`Retrieved` into a token-budgeted context block."""

    def __init__(self, max_tokens: int = config.MAX_CONTEXT_TOKENS) -> None:
        self._max_tokens = max_tokens

    # -- sections ----------------------------------------------------------

    def _client_section(self, retrieved: Retrieved) -> list[str]:
        if retrieved.clients.empty:
            return []
        lines = ["## CLIENTS"]
        for _, row in retrieved.clients.iterrows():
            lines.append(
                f"### {row['entity_id']} {row['entity_name']} "
                f"({row['sector']}, fiscal year {row['fy_label']})"
            )
            lines.append(f"- Model summary: {row['opportunity_summary']}")
            lines.append(
                f"- Portfolio position: commercial rank {int(row['commercial_rank'])} of 100 "
                f"client-product rows; mean confidence across the three Share of Wallet pillars "
                f"{_fmt(row['mean_core_confidence'], 'score')}"
            )
            if row["has_primary_opportunity"]:
                lines.append(
                    f"- Primary opportunity: {row['primary_label']} — status "
                    f"{row['primary_status']}, action '{row['primary_action']}', "
                    f"confidence {row['primary_confidence_band']}"
                )
                if pd.notna(row["secondary_label"]):
                    lines.append(
                        f"- Secondary opportunity: {row['secondary_label']} "
                        f"(status {row['secondary_status']})"
                    )
                if pd.notna(row["supporting_signal_label"]):
                    lines.append(
                        f"- Supporting signal: {row['supporting_signal_label']} "
                        f"(status {row['supporting_signal_status']})"
                    )
            else:
                lines.append(f"- No primary opportunity: {row['no_opportunity_reason']}")
            if row["high_severity_flag"]:
                lines.append(
                    f"- HIGH-severity model diagnostics open on: {row['high_severity_pillars']}"
                )

            # The relationship snapshot must cover every pillar, including the
            # ones that did not make the client's three selected opportunities.
            # Without this a briefing has to say "no figures are supplied" for
            # FX -- honest, but a hole where a fact belongs.
            lines.append(
                "- OBSERVED ACTIVITY, all five pillars (what Syn Bank actually handled; this is "
                "the relationship snapshot):"
            )
            lines.append(
                f"  - Cash Management: {_fmt(row['cash_observed_zar'])} observed against "
                f"{_fmt(row['addressable_cash_flow_zar'])} Addressable Cash Flow, share "
                f"{_fmt(row['cash_share'], 'pct')}"
            )
            lines.append(
                f"  - FX / Global Markets: {_fmt(row['fx_observed_zar'])} observed against "
                f"{_fmt(row['fx_addressable_zar'])} peer-benchmark addressable, share "
                f"{_fmt(row['fx_share'], 'pct')}"
            )
            lines.append(
                f"  - Trade Finance: {_fmt(row['trade_observed_zar'])} observed against "
                f"{_fmt(row['trade_addressable_zar'])} peer-benchmark addressable, share "
                f"{_fmt(row['trade_share'], 'pct')}"
            )
            lines.append(
                "  - Lending: no observed activity exists in Syn Bank's data (no loan book), so "
                "no share of wallet is computed. Financing opportunity "
                f"{_fmt(row['lending_opportunity_zar'])}"
            )
            lines.append(
                "  - Investment Banking: no observed activity and no rand figure. Opportunity "
                f"signal {_fmt(row['ib_signal_score'], 'score')}, category "
                f"{row['ib_opportunity_type']}"
            )
            lines.append("")
        return lines

    def _pillar_section(self, retrieved: Retrieved) -> list[str]:
        if retrieved.pillars.empty:
            return []
        sensitivity = (
            retrieved.sensitivity.set_index(["entity_id", "product"])
            if not retrieved.sensitivity.empty
            else None
        )
        lines = [
            "## PILLAR FIGURES",
            "(observed = what Syn Bank actually handled; addressable = what the model estimates "
            "the client's total to be; opportunity = addressable minus observed, NOT OBSERVED in "
            "Syn Bank's data)",
            "",
        ]
        for _, row in retrieved.pillars.iterrows():
            product = row["product"]
            lines.append(
                f"### {row['entity_id']} {row['entity_name']} — {row['product_label']} "
                f"[{row['product_class']}]"
            )
            if product == assumptions.CASH:
                lines.append(f"- Addressable Cash Flow: {_fmt(row['addressable_zar'])}")
                lines.append(
                    "  (the client's own operating turnover, revenue + cost of sales; an "
                    "accounting identity, never bank income)"
                )
            elif product in (assumptions.FX, assumptions.TRADE):
                label = (
                    "peer-benchmark addressable FX activity"
                    if product == assumptions.FX
                    else "peer-benchmark addressable trade-finance activity"
                )
                lines.append(f"- {label.capitalize()}: {_fmt(row['addressable_zar'])}")
                lines.append(
                    "  (a PEER BENCHMARK, not a disclosed total: the client's own exposure "
                    "scaled by peer intensity, this client excluded from the peer population)"
                )
            elif product == assumptions.LENDING:
                lines.append(f"- Financing opportunity: {_fmt(row['opportunity_zar'])}")
                lines.append(
                    "  (disclosed debt structure inside a 12-month horizon; NO share of wallet "
                    "exists for lending — Syn Bank's data holds no loan book)"
                )
            else:
                lines.append(
                    "- Investment-banking opportunity signal only. NO rand amount and NO share "
                    "of wallet exist for this pillar."
                )

            if product != assumptions.IB:
                if pd.notna(row["observed_zar"]):
                    lines.append(f"- Observed by Syn Bank: {_fmt(row['observed_zar'])}")
                if pd.notna(row["share"]):
                    lines.append(f"- Share of wallet: {_fmt(row['share'], 'pct')}")
                if pd.notna(row["opportunity_zar"]) and product != assumptions.LENDING:
                    lines.append(f"- Opportunity: {_fmt(row['opportunity_zar'])}")

            lines.append(
                f"- Confidence: {row['confidence_band']} "
                f"({_fmt(row['confidence'], 'score')})"
            )
            lines.append(
                f"- Status: {row['opportunity_status']} — {row['status_action']}. "
                f"Reason: {row['status_reason']}"
            )
            if pd.notna(row.get("selection_slot")):
                lines.append(f"- Selected as this client's {row['selection_slot']} opportunity")
            if row["diagnostic_count"]:
                severity = "including HIGH severity" if row["high_severity_diagnostic"] else "none HIGH"
                lines.append(
                    f"- Model diagnostics: {int(row['diagnostic_count'])} ({severity})"
                )

            key = (row["entity_id"], product)
            if sensitivity is not None and key in sensitivity.index:
                spread = sensitivity.loc[key]
                if pd.notna(spread["estimate_base"]):
                    lines.append(
                        f"- Sensitivity: {spread['sensitivity_flag']} — across "
                        f"{int(spread['scenarios_tested'])} tested model configurations the "
                        f"addressable figure ranges {_fmt(spread['estimate_low'])} to "
                        f"{_fmt(spread['estimate_high'])} around a base of "
                        f"{_fmt(spread['estimate_base'])}"
                        + (
                            f" ({_fmt(spread['estimate_range_pct'], 'pct0')} of base)"
                            if pd.notna(spread["estimate_range_pct"])
                            else ""
                        )
                    )
                    # The OPPORTUNITY range as well as the addressable one.
                    # Without it, a model asked how reliable an opportunity is
                    # subtracts the addressable low from the addressable high
                    # and reports the difference -- arithmetic it is forbidden
                    # to do, producing a figure that is both unsupported and
                    # wrong. Supplying the number removes the temptation; the
                    # validator catches it either way, but a fallback is a
                    # worse answer than a correct one.
                    if pd.notna(spread["opportunity_base"]):
                        lines.append(
                            f"- Sensitivity of the OPPORTUNITY figure specifically: ranges "
                            f"{_fmt(spread['opportunity_low'])} to "
                            f"{_fmt(spread['opportunity_high'])} around a base of "
                            f"{_fmt(spread['opportunity_base'])}. Do not compute this range "
                            "yourself; quote these figures."
                        )
                    lines.append(
                        f"- Rank stability: {spread['rank_stability']} — "
                        f"{spread['rank_stability_phrase']}"
                    )
            lines.append("")
        return lines

    def _explanation_section(self, retrieved: Retrieved) -> list[str]:
        if retrieved.explanations.empty:
            return []
        lines = ["## MODEL EXPLANATIONS (deterministic, generated by the analytical layer)"]
        for _, row in retrieved.explanations.iterrows():
            lines.append(f"### {row['entity_id']} — {row['product_label']}")
            lines.append(f"- WHAT: {row['what']}")
            lines.append(f"- WHY: {row['why']}")
            lines.append(f"- EVIDENCE: {row['evidence']}")
            lines.append(f"- CONFIDENCE: {row['confidence_statement']}")
            lines.append(f"- LIMITATION: {row['limitation']}")
            lines.append(f"- NEXT ACTION: {row['next_action']}")
            lines.append("")
        return lines

    def _question_section(self, retrieved: Retrieved) -> list[str]:
        if retrieved.questions.empty:
            return []
        lines = [
            "## BANKER QUESTIONS (pre-written by the analytical layer; use these, do not invent)"
        ]
        for _, row in retrieved.questions.iterrows():
            lines.append(f"{int(row['question_index'])}. [{row['product_label']}] {row['question']}")
            lines.append(f"   Why it matters: {row['rationale']}")
        lines.append("")
        return lines

    def _diagnostic_section(self, retrieved: Retrieved) -> list[str]:
        if retrieved.diagnostics.empty:
            return []
        lines = ["## MODEL DIAGNOSTICS"]
        for _, row in retrieved.diagnostics.iterrows():
            scope = row["entity_id"] if pd.notna(row["entity_id"]) else "portfolio"
            lines.append(
                f"- [{row['severity']}] {scope} / {row['product']}: {row['diagnostic']} — "
                f"{row['detail']}"
            )
        lines.append("")
        return lines

    def _portfolio_section(self, retrieved: Retrieved) -> list[str]:
        if retrieved.portfolio.empty:
            return []
        lines = ["## PORTFOLIO METRICS"]
        for _, row in retrieved.portfolio.iterrows():
            label = row["product_label"] if pd.notna(row["product_label"]) else "portfolio"
            value = row["value_text"] if row["value_text"] else _fmt(row["value_numeric"], "score")
            lines.append(f"- [{row['section']}] {label} / {row['metric']}: {value}")
            if row["note"]:
                lines.append(f"  {row['note']}")
        lines.append("")
        return lines

    def _methodology_section(self, retrieved: Retrieved) -> list[str]:
        if not retrieved.methodology:
            return []
        lines = ["## HOW THE MODEL WORKS"]
        lines.extend(f"- {note}" for note in retrieved.methodology)
        lines.append("")
        return lines

    def _notes_section(self, retrieved: Retrieved) -> list[str]:
        if not retrieved.notes:
            return []
        return ["## RETRIEVAL NOTES", *(f"- {note}" for note in retrieved.notes), ""]

    # -- assembly ----------------------------------------------------------

    #: Section order, most important first. Trimming drops from the end, so the
    #: figures a banker needs survive and the background goes first.
    SECTION_ORDER = (
        ("notes", "_notes_section"),
        ("clients", "_client_section"),
        ("pillars", "_pillar_section"),
        ("questions", "_question_section"),
        ("explanations", "_explanation_section"),
        ("sensitivity_portfolio", "_portfolio_section"),
        ("diagnostics", "_diagnostic_section"),
        ("methodology", "_methodology_section"),
    )

    def build(self, retrieved: Retrieved) -> ContextBundle:
        """Render, budget, and enumerate every figure in the result."""
        blocks: list[tuple[str, list[str]]] = []
        for name, method in self.SECTION_ORDER:
            lines = getattr(self, method)(retrieved)
            if lines:
                blocks.append((name, lines))

        if not blocks:
            return ContextBundle(
                text="(No matching data was retrieved for this question.)",
                token_estimate=config.estimate_tokens("(No matching data...)"),
            )

        kept: list[str] = []
        sections: list[str] = []
        truncated = False
        for name, lines in blocks:
            candidate = "\n".join([*kept, *lines])
            if config.estimate_tokens(candidate) > self._max_tokens and kept:
                truncated = True
                continue
            kept.extend(lines)
            sections.append(name)

        text = "\n".join(kept).strip()
        if truncated:
            text += (
                "\n\n(Some lower-priority background was omitted to fit the context budget. "
                "Every figure above is complete.)"
            )

        return ContextBundle(
            text=text,
            figures=extract_figures(text),
            entity_ids=retrieved.entity_ids,
            products=retrieved.products,
            token_estimate=config.estimate_tokens(text),
            truncated=truncated,
            sections=sections,
        )
