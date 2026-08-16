"""Work out what was asked, without asking a language model.

The router runs before any generation. It decides the intent, resolves which
client and which product the question is about, and hands
:mod:`.retrieval` a precise instruction. Nothing here is fuzzy or learned: it is
keyword and entity matching over a fixed vocabulary of 20 clients, 5 products
and 7 sectors.

Doing it deterministically matters for three reasons. Retrieval stays cheap and
auditable -- the audit log records exactly which entities were pulled. The model
never sees the whole database, so it cannot quote a client the banker did not
ask about. And when there is no API key at all, routing still works, which is
what makes the offline fallback a real answer rather than an error page.

Client matching is deliberately generous about how people actually type: "MTN",
"mtn group", "E16" and "Shoprite" all resolve, and a name that appears inside
another ("Naspers" within "Prosus/Naspers") resolves to the longest match rather
than the first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..wallet import assumptions

# ---------------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------------

CLIENT_BRIEFING = "client_briefing"
OPPORTUNITY_EXPLANATION = "opportunity_explanation"
PORTFOLIO_QUERY = "portfolio_query"
PRODUCT_QUERY = "product_query"
SENSITIVITY_QUERY = "sensitivity_query"
MEETING_PREPARATION = "meeting_preparation"
METHODOLOGY_QUERY = "methodology_query"
EXECUTIVE_SUMMARY = "executive_summary"

INTENTS = (
    CLIENT_BRIEFING,
    OPPORTUNITY_EXPLANATION,
    PORTFOLIO_QUERY,
    PRODUCT_QUERY,
    SENSITIVITY_QUERY,
    MEETING_PREPARATION,
    METHODOLOGY_QUERY,
    EXECUTIVE_SUMMARY,
)

#: Phrases that identify each intent. Ordered by specificity: the first block to
#: match wins, so "how reliable is this FX opportunity" routes to sensitivity
#: rather than to the product query its wording also matches.
INTENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        SENSITIVITY_QUERY,
        (
            "how reliable",
            "how confident",
            "how robust",
            "how sensitive",
            "sensitivity",
            "how much can i trust",
            "can i trust",
            "how solid",
            "how certain",
            "range of",
            "what is the range",
            "stable",
            "assumption",
        ),
    ),
    (
        MEETING_PREPARATION,
        (
            "what should the banker ask",
            "what should i ask",
            "questions to ask",
            "what to ask",
            "meeting prep",
            "prepare for the meeting",
            "prepare for a meeting",
            "before the meeting",
            "talking points",
            "agenda",
        ),
    ),
    (
        METHODOLOGY_QUERY,
        (
            "how does the model",
            "how is it calculated",
            "how is this calculated",
            "how do you calculate",
            "what is a peer benchmark",
            "what does addressable",
            "methodology",
            "what does confidence mean",
            "how is confidence",
            "what is share of wallet",
            "why is there no",
            "leave-one-out",
            "leave one out",
        ),
    ),
    (
        EXECUTIVE_SUMMARY,
        (
            "executive summary",
            "summarize the top",
            "summarise the top",
            "summarize the portfolio",
            "summarise the portfolio",
            "top five opportunities",
            "top 5 opportunities",
            "board summary",
            "overview of the portfolio",
        ),
    ),
    (
        OPPORTUNITY_EXPLANATION,
        (
            "why is",
            "why has",
            "why does",
            "why was",
            "why flagged",
            "flagged for",
            "explain the",
            "explain why",
            "what drives",
            "reason for",
        ),
    ),
    (
        CLIENT_BRIEFING,
        (
            "prepare a briefing",
            "brief me",
            "briefing for",
            "tell me about",
            "give me a briefing",
            "client briefing",
            "overview of",
            "profile for",
            "what do we know about",
        ),
    ),
    (
        PORTFOLIO_QUERY,
        (
            "which clients",
            "which client",
            "who has the",
            "across the portfolio",
            "rank the",
            "top clients",
            "largest opportunities",
            "biggest opportunities",
            "highest confidence",
            "low confidence",
        ),
    ),
)

# ---------------------------------------------------------------------------
# Products and sectors
# ---------------------------------------------------------------------------

#: How a banker refers to each pillar in the wild.
PRODUCT_ALIASES: dict[str, tuple[str, ...]] = {
    assumptions.CASH: (
        "cash management",
        "cash-management",
        "transactional",
        "addressable cash flow",
        "cash flow",
        "operating flow",
        "collections",
        "supplier payments",
        "payments",
        "cash",
    ),
    assumptions.FX: (
        "fx",
        "foreign exchange",
        "global markets",
        "cross-border",
        "cross border",
        "currency",
        "hedging",
        "forwards",
    ),
    assumptions.TRADE: (
        "trade finance",
        "trade-finance",
        "trade",
        "letters of credit",
        "letter of credit",
        "documentary",
        "guarantees",
        "import",
        "export",
    ),
    assumptions.LENDING: (
        "lending",
        "loan",
        "loans",
        "credit",
        "debt",
        "refinancing",
        "financing",
        "facilities",
    ),
    assumptions.IB: (
        "investment banking",
        "investment-banking",
        "capital markets",
        "dcm",
        "advisory",
        "corporate finance",
        "ib ",
        "mandate",
    ),
}

SECTOR_ALIASES: dict[str, tuple[str, ...]] = {
    "mining": ("mining", "miner", "miners", "resources", "resource"),
    "consumer": ("consumer", "retail", "retailer", "retailers", "fmcg"),
    "insurance": ("insurance", "insurer", "insurers"),
    "real_estate": ("real estate", "real-estate", "property", "reit", "reits"),
    "tech": ("tech", "technology", "internet"),
    "telecoms": ("telecoms", "telecom", "telco", "telcos", "mobile operator"),
    "industrials_pharma": (
        "industrials",
        "industrial",
        "pharma",
        "pharmaceutical",
        "pharmaceuticals",
        "healthcare",
    ),
}

#: Prepositions that mark the client a question is *about*, as opposed to one
#: mentioned in passing. Anchored to the end so they must sit immediately before
#: the client name, allowing only a determiner in between.
TARGET_PREPOSITIONS = re.compile(
    r"\b(?:for|about|on|regarding|re|of|at|with)\s+(?:the\s+)?$"
)

#: How far back to look for one of those. Long enough for "regarding the ",
#: short enough that an unrelated "for" earlier in the clause does not count.
TARGET_WINDOW = 16

#: Words that mean "sort descending" and "sort ascending" respectively.
SUPERLATIVE_TOP = ("largest", "biggest", "top", "highest", "strongest", "best", "most")
SUPERLATIVE_BOTTOM = ("smallest", "lowest", "weakest", "worst", "least")

#: Filters a banker asks for by name.
HIGH_CONFIDENCE_TERMS = ("high-confidence", "high confidence", "well evidenced", "reliable")
LOW_CONFIDENCE_TERMS = ("low-confidence", "low confidence", "weakly evidenced", "uncertain")

#: Vocabulary that marks a question as being about this portfolio at all.
#: Without this, "what is the weather in Johannesburg" falls through to the
#: default portfolio query and gets a confident list of opportunities -- an
#: answer to a question nobody asked, which is the most embarrassing failure
#: mode a copilot has.
DOMAIN_TERMS = (
    "client",
    "clients",
    "portfolio",
    "opportunity",
    "opportunities",
    "wallet",
    "share",
    "confidence",
    "sensitivity",
    "bank",
    "banker",
    "briefing",
    "brief",
    "revenue",
    "exposure",
    "sector",
    "pillar",
    "product",
    "model",
    "estimate",
    "headroom",
    "penetration",
    "relationship",
    "meeting",
    "flag",
    "flagged",
    "rank",
    "top",
    "largest",
    "biggest",
    "highest",
    "strongest",
    "summarize",
    "summarise",
    "summary",
    "observed",
    "addressable",
)


#: Words that must never stand in for a client name. "The Bidvest Group" begins
#: with an article, and indexing it on "the" made that client match almost every
#: question ever asked -- a shortcut that silently poisons every downstream
#: retrieval. Corporate names are also full of these.
NAME_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "group",
        "holdings",
        "limited",
        "ltd",
        "plc",
        "corporation",
        "corp",
        "company",
        "co",
        "sa",
        "south",
        "african",
        "capital",
        "international",
        "global",
    }
)


def _distinctive_token(name: str) -> str | None:
    """The first word of a client name that could only mean that client.

    ``"The Bidvest Group"`` gives ``"bidvest"``, not ``"the"``. Returns None
    when no word in the name is distinctive enough to match on alone.
    """
    for word in name.split():
        token = word.strip(".,&").lower()
        if len(token) >= 3 and token not in NAME_STOP_WORDS:
            return token
    return None


@dataclass(frozen=True)
class Route:
    """What the question turned out to be about."""

    intent: str
    question: str
    entity_ids: tuple[str, ...] = ()
    entity_names: tuple[str, ...] = ()
    products: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()
    #: ``desc`` unless the question explicitly asked for the weakest.
    order: str = "desc"
    confidence_filter: str | None = None
    limit: int = 5
    matched_on: tuple[str, ...] = field(default_factory=tuple)
    unresolved_client: str | None = None
    #: True when the question contains no client, product, sector or banking
    #: vocabulary at all, so there is nothing here to answer.
    off_topic: bool = False

    @property
    def entity_id(self) -> str | None:
        return self.entity_ids[0] if self.entity_ids else None

    @property
    def product(self) -> str | None:
        return self.products[0] if self.products else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "entity_ids": list(self.entity_ids),
            "entity_names": list(self.entity_names),
            "products": list(self.products),
            "sectors": list(self.sectors),
            "order": self.order,
            "confidence_filter": self.confidence_filter,
            "limit": self.limit,
            "matched_on": list(self.matched_on),
            "unresolved_client": self.unresolved_client,
            "off_topic": self.off_topic,
        }


class Router:
    """Resolves a natural-language question against the known vocabulary."""

    def __init__(self, clients: dict[str, str]) -> None:
        """``clients`` maps ``entity_id`` to ``entity_name``."""
        self._clients = dict(clients)
        # Longest first, so "Bid Corporation" is not swallowed by "Bid".
        self._name_index: list[tuple[str, str]] = sorted(
            ((name.lower(), entity_id) for entity_id, name in clients.items()),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        #: Distinctive leading words, for the way people actually type. Built
        #: from the roster rather than hardcoded, and only kept where the token
        #: is unambiguous across the whole portfolio.
        counts: dict[str, int] = {}
        for name in clients.values():
            token = _distinctive_token(name)
            if token:
                counts[token] = counts.get(token, 0) + 1
        self._token_index = {}
        for entity_id, name in clients.items():
            token = _distinctive_token(name)
            if token and counts[token] == 1:
                self._token_index[token] = entity_id

    # -- entity resolution -------------------------------------------------
    #
    # See :meth:`Router._targeted`. The window is short on purpose: "for
    # Vodacom Group" marks Vodacom, while "for the last three years, Vodacom..."
    # does not, and only a tight window tells them apart.

    def find_clients(self, question: str) -> tuple[list[str], list[str]]:
        """Every client named in the question. Returns ``(ids, matched_text)``.

        When more than one client is named but only one is the *subject* of the
        question, the others are dropped -- see :meth:`_targeted`.
        """
        lowered = question.lower()
        found: list[str] = []
        matched: list[str] = []

        for entity_id in self._clients:
            if re.search(rf"\b{entity_id.lower()}\b", lowered):
                found.append(entity_id)
                matched.append(entity_id)

        for name, entity_id in self._name_index:
            if entity_id in found:
                continue
            if name in lowered:
                found.append(entity_id)
                matched.append(name)

        for token, entity_id in self._token_index.items():
            if entity_id in found:
                continue
            if re.search(rf"\b{re.escape(token)}\b", lowered):
                found.append(entity_id)
                matched.append(token)

        if len(found) > 1:
            subject = self._targeted(lowered, found, matched)
            if subject is not None:
                index = found.index(subject)
                return [subject], [matched[index]]

        return found, matched

    def _targeted(
        self, lowered: str, found: list[str], matched: list[str]
    ) -> str | None:
        """The one client the question is *about*, when exactly one is marked.

        A banker mentions other companies in passing all the time -- "my
        colleague who used to cover Shoprite asked me to brief Vodacom",
        "unlike MTN, what is the position for Clicks?". Retrieving both wastes
        context and, worse, hands the model two clients' figures for a
        one-client question, which is how a briefing ends up quoting the wrong
        company's numbers.

        The subject is the one introduced by a targeting preposition. If several
        are (a genuine comparison: "compare MTN and Vodacom"), or none is, every
        match is kept and the downstream retrieval treats it as multi-client.
        """
        targeted = [
            entity_id
            for entity_id, text in zip(found, matched, strict=True)
            if self._is_targeted(lowered, text)
        ]
        return targeted[0] if len(targeted) == 1 else None

    @staticmethod
    def _is_targeted(lowered: str, text: str) -> bool:
        """Whether ``text`` is introduced by a preposition that marks a subject."""
        for match in re.finditer(rf"\b{re.escape(text.lower())}\b", lowered):
            before = lowered[max(0, match.start() - TARGET_WINDOW) : match.start()]
            if TARGET_PREPOSITIONS.search(before):
                return True
        return False

    def _find_products(self, question: str) -> list[str]:
        lowered = f" {question.lower()} "
        found = []
        for product, aliases in PRODUCT_ALIASES.items():
            if any(alias in lowered for alias in aliases):
                found.append(product)
        # "trade finance" also contains "financing"-adjacent words; if both
        # trade and lending matched only because of a shared word, prefer the
        # more specific phrase the banker actually used.
        if assumptions.TRADE in found and assumptions.LENDING in found:
            if "trade finance" in lowered or "trade-finance" in lowered:
                found = [product for product in found if product != assumptions.LENDING]
        return found

    def _find_sectors(self, question: str) -> list[str]:
        lowered = f" {question.lower()} "
        return [
            sector
            for sector, aliases in SECTOR_ALIASES.items()
            if any(alias in lowered for alias in aliases)
        ]

    # -- intent ------------------------------------------------------------

    def _find_intent(self, question: str, has_client: bool, has_product: bool) -> str:
        lowered = question.lower()
        for intent, patterns in INTENT_PATTERNS:
            if any(pattern in lowered for pattern in patterns):
                # "why is this client flagged for FX" is an opportunity
                # explanation only when we know which opportunity; without a
                # client it is a portfolio question about a product.
                if intent == OPPORTUNITY_EXPLANATION and not has_client:
                    continue
                if intent == CLIENT_BRIEFING and not has_client:
                    continue
                return intent

        if has_client and has_product:
            return OPPORTUNITY_EXPLANATION
        if has_client:
            return CLIENT_BRIEFING
        if has_product:
            return PRODUCT_QUERY
        return PORTFOLIO_QUERY

    def _find_limit(self, question: str) -> int:
        match = re.search(r"\btop\s+(\d{1,2})\b", question.lower())
        if match:
            return max(1, min(20, int(match.group(1))))
        words = {
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "ten": 10,
        }
        for word, value in words.items():
            if re.search(rf"\btop\s+{word}\b", question.lower()):
                return value
        return 5

    # -- the public entry point --------------------------------------------

    def route(self, question: str) -> Route:
        """Classify a question and resolve everything it refers to."""
        if not question or not question.strip():
            raise ValueError("empty question")

        entity_ids, matched = self.find_clients(question)
        products = self._find_products(question)
        sectors = self._find_sectors(question)
        intent = self._find_intent(question, bool(entity_ids), bool(products))
        lowered = question.lower()

        confidence_filter = None
        if any(term in lowered for term in HIGH_CONFIDENCE_TERMS):
            confidence_filter = "HIGH"
        elif any(term in lowered for term in LOW_CONFIDENCE_TERMS):
            confidence_filter = "LOW"

        order = (
            "asc"
            if any(term in lowered for term in SUPERLATIVE_BOTTOM)
            and not any(term in lowered for term in SUPERLATIVE_TOP)
            else "desc"
        )

        return Route(
            intent=intent,
            question=question.strip(),
            entity_ids=tuple(entity_ids),
            entity_names=tuple(self._clients[entity_id] for entity_id in entity_ids),
            products=tuple(products),
            sectors=tuple(sectors),
            order=order,
            confidence_filter=confidence_filter,
            limit=self._find_limit(question),
            matched_on=tuple(matched),
            unresolved_client=self._unresolved(question, entity_ids),
            off_topic=not (
                entity_ids
                or products
                or sectors
                or any(
                    re.search(rf"\b{re.escape(term)}\b", lowered) for term in DOMAIN_TERMS
                )
            ),
        )

    def _unresolved(self, question: str, found: list[str]) -> str | None:
        """Flag a question that looks client-shaped but names nobody we hold.

        Without this, "prepare a briefing for Sasol" -- a real JSE company that
        is not in this portfolio -- would quietly become a portfolio query and
        the banker would get an answer to a question they did not ask.
        """
        if found:
            return None
        match = re.search(
            r"(?:briefing|brief|profile|about|for|on)\s+(?:me\s+(?:about|on)\s+)?"
            r"([A-Z][A-Za-z&.\-]*(?:\s+[A-Z][A-Za-z&.\-]*){0,3})",
            question,
        )
        if not match:
            return None
        candidate = match.group(1).strip().rstrip(".,;:?!")
        stop_words = {
            "The",
            "This",
            "That",
            "Which",
            "What",
            "Syn",
            "Syn Bank",
            "FX",
            "Trade",
            "Lending",
        }
        if candidate in stop_words or len(candidate) < 3:
            return None
        return candidate
