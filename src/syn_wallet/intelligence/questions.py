"""Questions a relationship manager can actually put to the client.

Generic questions are worse than no questions: they signal that nobody looked at
the account. So every question here is parameterised by figures the model
produced for *this* client -- its share, its observed volume, its benchmark
population, its financing horizon -- and a question that cannot be filled in
with real numbers is not emitted at all.

Each question also carries the fields it was built from and the reason it is
worth asking, so a banker can see why the system thinks it matters and can drop
it if they disagree.

Questions are generated for the primary opportunity by default. The secondary
opportunity gets one, because a meeting agenda with seven questions on it is not
an agenda.
"""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from ..wallet import assumptions
from ..wallet.common import count, pct, zar
from . import config, selection

#: Columns published in ``banker_questions.parquet``.
QUESTION_COLUMNS = (
    "entity_id",
    "entity_name",
    "sector",
    "product",
    "product_label",
    "selection_slot",
    "question_index",
    "question",
    "rationale",
    "source_fields",
    "intelligence_version",
)

#: How many questions each slot earns.
QUESTIONS_PER_SLOT = {selection.PRIMARY: 4, selection.SECONDARY: 1}

#: A question generator returns ``(question, rationale, fields)`` or None when
#: the figures it needs are not available for this client.
Generator = Callable[[pd.Series], tuple[str, str, tuple[str, ...]] | None]


def _has(row: pd.Series, *fields: str) -> bool:
    return all(pd.notna(row.get(field)) for field in fields)


# ---------------------------------------------------------------------------
# Cash management
# ---------------------------------------------------------------------------


def _cash_primary_bank(row: pd.Series):
    if not _has(row, "share", "observed_zar", "addressable_zar"):
        return None
    return (
        f"Our records show {zar(row['observed_zar'])} of your operating collections and supplier "
        f"payments settling through Syn Bank in {row['fy_label']}, against an addressable "
        f"operating cash flow of about {zar(row['addressable_zar'])} implied by your reported "
        f"revenue and cost of sales — roughly {pct(row['share'], 1)}. Which bank currently holds "
        "your primary operating account, and what drives that split?",
        "Cash management is the best-evidenced pillar in the model and the share is strikingly "
        "low; establishing where the primary operating mandate sits is the single most useful "
        "fact this conversation can produce.",
        ("observed_zar", "addressable_zar", "share"),
    )


def _cash_split_rationale(row: pd.Series):
    if not _has(row, "opportunity_zar"):
        return None
    return (
        f"About {zar(row['opportunity_zar'])} of your annual operating flow is not visible in our "
        "transaction data. Is that a deliberate multi-banking arrangement, a legacy of an "
        "acquisition, or simply where the relationship has settled?",
        "Distinguishes a policy decision from an accident of history — the two lead to very "
        "different conversations.",
        ("opportunity_zar",),
    )


def _cash_payroll(row: pd.Series):
    return (
        "Where is your payroll run, and would you consider consolidating it alongside your "
        "collections and supplier payments?",
        "Payroll is the stickiest transactional product and the sharpest engagement signal in "
        "this dataset; a client running payroll elsewhere has its primary banking relationship "
        "elsewhere.",
        ("observed_zar",),
    )


def _cash_channel(row: pd.Series):
    return (
        "What would need to change in service, pricing or systems integration for a larger share "
        "of your day-to-day settlement to run through us?",
        "Converts a measured gap into a specific, actionable objection.",
        ("share",),
    )


# ---------------------------------------------------------------------------
# FX
# ---------------------------------------------------------------------------


def _fx_concentration(row: pd.Series):
    if not _has(row, "observed_zar", "share"):
        return None
    return (
        f"We handled {zar(row['observed_zar'])} of your cross-border settlement in "
        f"{row['fy_label']}, which peer benchmarking suggests is around {pct(row['share'], 1)} of "
        "your likely activity. What proportion of your cross-border settlement is currently "
        "concentrated with your primary banking partners?",
        "The FX denominator is a peer benchmark rather than a disclosed total, so the client's "
        "own answer is the only way to test it.",
        ("observed_zar", "share", "addressable_zar"),
    )


def _fx_corridors(row: pd.Series):
    return (
        "Which corridors and currency pairs carry most of your settlement value, and are any of "
        "them currently served on terms you would want to revisit?",
        "Moves from an aggregate benchmark to the specific flows a global-markets desk can "
        "actually price.",
        ("observed_zar",),
    )


def _fx_hedging(row: pd.Series):
    return (
        "How much of your foreign-currency exposure is hedged, over what tenor, and who executes "
        "those forwards today?",
        "The hedging component of the estimate assumes a disclosed forward book is executed once "
        "a year, which is a deliberate floor; the client's actual roll frequency would materially "
        "change the picture.",
        ("addressable_zar",),
    )


def _fx_benchmark_test(row: pd.Series):
    if not _has(row, "addressable_zar", "benchmark_n"):
        return None
    return (
        f"Our peer-benchmark estimate puts your addressable cross-border activity near "
        f"{zar(row['addressable_zar'])}, based on {count(row['benchmark_n'])} comparable clients. "
        "Does that match your own view of your annual settlement volume?",
        "Directly tests the benchmark assumption with the one party who knows the answer, and "
        "the reply either validates or retires the estimate.",
        ("addressable_zar", "benchmark_n", "benchmark_level"),
    )


# ---------------------------------------------------------------------------
# Trade finance
# ---------------------------------------------------------------------------


def _trade_facilities(row: pd.Series):
    if not _has(row, "observed_zar", "addressable_zar"):
        return None
    return (
        f"We issued {zar(row['observed_zar'])} of trade instruments for you in {row['fy_label']}, "
        f"against a peer-benchmark expectation nearer {zar(row['addressable_zar'])}. Are your "
        "current trade-finance facilities sufficient for your expected import and export cycle?",
        "Tests whether the measured gap reflects unmet need or simply facilities held elsewhere.",
        ("observed_zar", "addressable_zar"),
    )


def _trade_instrument_mix(row: pd.Series):
    return (
        "What mix of letters of credit, guarantees and collections does your trade cycle actually "
        "require, and where is each currently placed?",
        "The estimate is built from three separate sub-models; knowing which instrument type "
        "dominates tells a trade desk where to start.",
        ("addressable_zar",),
    )


def _trade_tenor(row: pd.Series):
    return (
        "Has your import or export cycle changed in tenor or counterparty concentration over the "
        "past year, and does your current facility structure still fit it?",
        "Trade-finance demand is driven by the procurement and export base, both of which move "
        "with the client's own commercial cycle.",
        ("observed_zar",),
    )


def _trade_benchmark_test(row: pd.Series):
    if not _has(row, "benchmark_n"):
        return None
    return (
        f"Our estimate of your annual trade-instrument requirement is benchmarked against "
        f"{count(row['benchmark_n'])} comparable clients rather than taken from your disclosure. "
        "Does the scale look right to you?",
        "Peer-benchmark denominators are the least defensible part of the model; the client's own "
        "answer is worth more than any coefficient.",
        ("benchmark_n", "benchmark_level"),
    )


# ---------------------------------------------------------------------------
# Lending
# ---------------------------------------------------------------------------


def _lending_committed(row: pd.Series):
    if not _has(row, "opportunity_zar"):
        return None
    return (
        f"Your disclosed debt structure points to roughly {zar(row['opportunity_zar'])} of "
        "financing decisions falling inside the next twelve months. How much of that requirement "
        "is already committed?",
        "Separates a genuine financing conversation from a refinancing that is already placed.",
        ("opportunity_zar",),
    )


def _lending_undrawn(row: pd.Series):
    return (
        "Your undrawn committed facilities are capacity you are paying for and not using — are "
        "those lines up for renewal, and what would make you consolidate them?",
        "Undrawn committed facilities are a structural, disclosed figure and the most concrete "
        "part of the lending estimate.",
        ("opportunity_zar",),
    )


def _lending_working_capital(row: pd.Series):
    return (
        "How is your working-capital cycle funded today — from operating cash, from committed "
        "lines, or from a mix — and is that mix under review?",
        "The working-capital component is scaled by a peer median rather than a disclosure, so "
        "the client's own funding policy is the check on it.",
        ("opportunity_zar",),
    )


def _lending_capex(row: pd.Series):
    return (
        "How much of your planned capital expenditure do you expect to fund with new debt rather "
        "than operating cash flow?",
        "The capex component rests on the engine's single underived coefficient — a 30% "
        "debt-funded assumption — so the client's answer replaces a judgement with a fact.",
        ("opportunity_zar",),
    )


# ---------------------------------------------------------------------------
# Investment banking
# ---------------------------------------------------------------------------


def _ib_plans(row: pd.Series):
    if not _has(row, "signal_score"):
        return None
    category = str(row.get("ib_opportunity_type") or "none_supported")
    if category == "none_supported":
        return (
            "Is any capital-markets or corporate-finance activity planned over the next twelve "
            "to eighteen months that we should be aware of?",
            "The balance sheet meets no mandate threshold, so this is a relationship question "
            "rather than a detected opportunity.",
            ("signal_score",),
        )
    return (
        f"Your disclosed balance sheet points towards {category.replace('_', ' ')}. Is that an "
        "area where you expect to need external structuring or advisory support?",
        f"The {category.replace('_', ' ')} category was triggered by a declared threshold on "
        "disclosed figures, not by any indication of a planned transaction.",
        ("signal_score",),
    )


def _ib_panel(row: pd.Series):
    return (
        "Who sits on your current advisory and capital-markets panel, and how is that reviewed?",
        "Relationship breadth is one of the five inputs to the signal; the client's panel "
        "structure is the fact behind it.",
        ("signal_score",),
    )


GENERATORS: dict[str, tuple[Generator, ...]] = {
    assumptions.CASH: (_cash_primary_bank, _cash_split_rationale, _cash_payroll, _cash_channel),
    assumptions.FX: (_fx_concentration, _fx_benchmark_test, _fx_corridors, _fx_hedging),
    assumptions.TRADE: (
        _trade_facilities,
        _trade_benchmark_test,
        _trade_instrument_mix,
        _trade_tenor,
    ),
    assumptions.LENDING: (
        _lending_committed,
        _lending_undrawn,
        _lending_working_capital,
        _lending_capex,
    ),
    assumptions.IB: (_ib_plans, _ib_panel),
}


def build(scored: pd.DataFrame) -> pd.DataFrame:
    """Two to four questions for each client's primary opportunity, one for the secondary."""
    rows: list[dict[str, Any]] = []
    selected = scored[scored["selection_slot"].isin(QUESTIONS_PER_SLOT)]

    for _, row in selected.iterrows():
        wanted = QUESTIONS_PER_SLOT[row["selection_slot"]]
        produced = 0
        for generator in GENERATORS[row["product"]]:
            if produced >= wanted:
                break
            result = generator(row)
            if result is None:
                continue
            question, rationale, fields = result
            produced += 1
            rows.append(
                {
                    "entity_id": row["entity_id"],
                    "entity_name": row["entity_name"],
                    "sector": row["sector"],
                    "product": row["product"],
                    "product_label": row["product_label"],
                    "selection_slot": row["selection_slot"],
                    "question_index": produced,
                    "question": question,
                    "rationale": rationale,
                    "source_fields": ", ".join(fields),
                    "intelligence_version": config.INTELLIGENCE_VERSION,
                }
            )

    return pd.DataFrame(rows, columns=list(QUESTION_COLUMNS))
