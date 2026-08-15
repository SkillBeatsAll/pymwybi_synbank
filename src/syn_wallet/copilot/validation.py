"""Check the generated answer against the context that produced it.

A prompt is a request. A check is a guarantee. Everything in the system prompt
can be ignored by a model having a bad day, so every answer is inspected before
a banker sees it.

**Two severities, and the split is the whole design.** Discarding an answer is
expensive: the banker loses fluent, useful prose and gets a template instead. So
rejection is reserved for the things that would make the answer *wrong*, and
everything else is recorded as a warning on an answer that is still shown.

Rejected (:data:`ERROR`):

* **Unsupported figures.** A rand amount or percentage that does not trace to
  the context. This is the load-bearing guarantee -- a model that helpfully adds
  two pillars together and reports the total is caught, because the total is not
  in the allow-list, because the deterministic layer never produced one.
* **Asserted forbidden claims.** The vocabulary bans from the commercial
  intelligence layer: no fee pool, no bank revenue, no competitor-held business,
  no confirmed revenue. Asserted, not merely mentioned -- a sentence *denying*
  the claim is exactly what the prompt asks for.
* **Cross-pillar totalling**, in words rather than digits: "combined opportunity
  across", "total across all pillars".

Warned (:data:`WARNING`) -- logged, surfaced in the audit record, answer kept:

* Share-of-wallet language sitting near lending or investment banking. The check
  is a proximity heuristic and it cries wolf on correct methodology sentences
  that distinguish the pillars precisely because they name both.
* A rand figure in a sentence that also mentions investment banking. The figure
  itself already had to come from the allow-list, so the worst case is a
  mis-attribution of a real number, not an invented one.
* First-person calculation tells ("I estimate", "which works out to"). These are
  style, and the figure check is what actually catches a computed number.

**Figures are matched with rounding tolerance.** ``R278.7bn`` in an answer is
supported by ``R278.72bn`` in the context: it is the same number, presented one
digit shorter. What tolerance cannot do is admit a figure that rounds to nothing
in the context, so a fabricated or summed amount still fails.

A rejected answer is discarded, not patched. The banker gets the deterministic
fallback and a notice saying why, and the violation goes into the audit log --
so a reviewer can see how often the model misbehaved rather than having to trust
that it did not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..intelligence import config as intelligence_config
from .context import extract_figures

#: An answer carrying one of these is discarded.
ERROR = "error"
#: An answer carrying one of these is shown, with the note kept in the audit log.
WARNING = "warning"

#: Phrases banned in any generated answer. Inherited from the deterministic
#: layer so the two can never drift apart. Asserting one of these is a factual
#: error about what the system measures, so it is a rejection.
FORBIDDEN_PHRASES = intelligence_config.FORBIDDEN_PHRASES

#: Cross-pillar totalling, expressed in words rather than in digits. The figure
#: check catches the arithmetic; this catches the claim made without one.
#: Rejected, because §3.1 of the data contract makes a portfolio total not
#: merely unavailable but meaningless.
FORBIDDEN_GENERATIVE_PHRASES = (
    "total across all pillars",
    "combined opportunity across",
    "total wallet across",
    "aggregate opportunity of",
)

#: First-person calculation tells. These read badly -- the model is a writer,
#: not an analyst, and should not narrate itself as one -- but they are a matter
#: of voice, not of fact. If such a sentence also contains a computed number,
#: the figure check rejects the answer on that ground instead. Warned, not
#: rejected: discarding a correct answer over the word "estimate" costs the
#: banker a good briefing and gains nothing.
DISCOURAGED_GENERATIVE_PHRASES = (
    "i estimate",
    "i calculate",
    "i have calculated",
    "my estimate",
    "roughly equates to",
    "which works out to",
)

# ---------------------------------------------------------------------------
# Claims that are always false in this system
# ---------------------------------------------------------------------------
#
# Three things the data cannot support, however the sentence is phrased. Unlike
# the substring bans these are patterns, because the claim has more shapes than
# a fixed phrase does -- "competitor-held" is banned, but "held by a competitor
# bank" says the same thing and was sailing through until the adversarial suite
# asked for it directly.
#
# Each is negation-aware: denying the claim is exactly what the prompt asks for.

#: A rand amount *attributed to* investment banking. Deliberately narrower than
#: "a sentence that mentions both": the gap may not cross a clause boundary or
#: name another pillar, so "FX is R8.75bn; investment banking is a ranked
#: signal" reads correctly and passes, while "the investment banking wallet is
#: R14.20bn" does not.
IB_RAND_ATTRIBUTION = re.compile(
    r"(?:investment[- ]banking|capital markets)"
    r"(?P<gap>[^.;\n]{0,50}?)"
    r"\b(?:is|of|worth|at|totals?|totalling|amounts? to)\s+"
    r"(?:approximately\s+|roughly\s+|about\s+|around\s+|c\.\s*)?"
    r"-?R\s?\d",
    re.IGNORECASE,
)

#: Pillar names that, appearing in that gap, mean the rand figure belongs to a
#: different pillar and the match is a false positive.
_OTHER_PILLARS = re.compile(r"\b(?:fx|foreign exchange|trade|cash|lending)\b", re.IGNORECASE)

#: Claims that somebody else holds the unobserved flow. Syn Bank's data shows
#: what Syn Bank handled. Where the rest sits is not in the dataset in any form.
COMPETITOR_OWNERSHIP = (
    re.compile(r"held by (?:a |the |another )?(?:competitor|rival|bank)", re.IGNORECASE),
    re.compile(r"\b(?:currently |primarily )?banks? with\b", re.IGNORECASE),
    re.compile(r"\b(?:their|its|the) (?:incumbent|primary|main|current) bank\b", re.IGNORECASE),
    re.compile(r"\bthe lender is\b", re.IGNORECASE),
    re.compile(r"\bcompetitor wallet\b", re.IGNORECASE),
)

#: Claims the answer went and looked something up. It did not: the context is
#: the only thing it saw, and anything sourced elsewhere is invented.
EXTERNAL_SOURCE = (
    re.compile(
        r"according to (?:their|its|the company'?s?|the) "
        r"(?:annual report|website|filings|results|accounts)",
        re.IGNORECASE,
    ),
    re.compile(r"public(?:ly available)? sources", re.IGNORECASE),
    re.compile(r"\bi looked up\b", re.IGNORECASE),
    re.compile(r"\b(?:bloomberg|reuters|refinitiv|factset)\b", re.IGNORECASE),
)

#: Sentences mentioning lending must not also claim a share of wallet.
LENDING_TERMS = ("lending", "financing opportunity", "loan book", "refinancing")
IB_TERMS = ("investment banking", "investment-banking", "capital markets")
SHARE_TERMS = ("share of wallet", "share-of-wallet", "wallet share")

#: A figure smaller than this is usually a rank, a count or a year rather than a
#: monetary claim, so percentages under it are not policed. Rand amounts always
#: are.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Violation:
    """One specific thing wrong with a generated answer."""

    kind: str
    detail: str
    evidence: str = ""
    severity: str = ERROR

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "evidence": self.evidence,
            "severity": self.severity,
        }


@dataclass
class ValidationResult:
    """The verdict, and everything needed to explain it.

    ``violations`` holds only what caused a rejection. ``warnings`` holds what
    was noticed and tolerated -- kept separate so "how often did the model
    misbehave" and "how often was an answer thrown away" stay different
    questions with different answers.
    """

    ok: bool
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)
    unsupported_figures: list[str] = field(default_factory=list)
    checked_figures: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "violation_count": len(self.violations),
            "violations": [violation.as_dict() for violation in self.violations],
            "warning_count": len(self.warnings),
            "warnings": [warning.as_dict() for warning in self.warnings],
            "unsupported_figures": list(self.unsupported_figures),
            "checked_figures": self.checked_figures,
        }

    def summary(self) -> str:
        if self.ok:
            passed = f"passed: {self.checked_figures} figures all supported by the context"
            if self.warnings:
                noted = ", ".join(sorted({warning.kind for warning in self.warnings}))
                return f"{passed} ({len(self.warnings)} warning(s): {noted})"
            return passed
        return "; ".join(f"{violation.kind}: {violation.detail}" for violation in self.violations)


def _sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in _SENTENCE_SPLIT.split(text) if sentence.strip()]


# ---------------------------------------------------------------------------
# Figure support, with rounding tolerance
# ---------------------------------------------------------------------------
#
# The strict rule -- exact string match against the allow-list -- rejects
# `R278.7bn` when the context says `R278.72bn`. That is the same number written
# one digit shorter, and throwing away a whole briefing over it is the single
# largest source of avoidable rejections.
#
# So a figure is supported when some context figure of the same kind ROUNDS to
# it at the precision the answer chose to write. This cannot admit an invented
# number: `R5.79bn` (the sum of `R1.23bn` and `R4.56bn`) rounds from neither, so
# cross-pillar arithmetic still fails exactly as before. What it admits is
# rescaling and shortening -- presentation, not calculation.

#: Magnitude suffixes as :func:`..wallet.common.zar` renders them.
MAGNITUDES = {"tn": 1e12, "bn": 1e9, "m": 1e6, "k": 1e3, "": 1.0}

#: A normalised rand token: sign, digits, optional magnitude. Tokens reaching
#: here have already been through ``context._normalise`` -- lowercased, spaces
#: and thousands separators stripped.
_ZAR_TOKEN = re.compile(r"^(-?)r(\d+(?:\.\d+)?)(tn|bn|m|k)?$")
_PCT_TOKEN = re.compile(r"^(\d+(?:\.\d+)?)%$")


def _parse_figure(token: str) -> tuple[str, float, float, int] | None:
    """``(kind, absolute value, the scale it was written at, decimal places)``.

    Returns None for anything that does not parse, which is then held to exact
    string matching -- an unparseable token gets no tolerance at all.
    """
    match = _ZAR_TOKEN.match(token)
    if match:
        sign, digits, suffix = match.groups()
        scale = MAGNITUDES[suffix or ""]
        value = float(digits) * scale * (-1.0 if sign else 1.0)
        return ("zar", value, scale, _decimals(digits))
    match = _PCT_TOKEN.match(token)
    if match:
        (digits,) = match.groups()
        return ("pct", float(digits), 1.0, _decimals(digits))
    return None


def _decimals(digits: str) -> int:
    _, _, fraction = digits.partition(".")
    return len(fraction)


def unsupported_figures(answer_figures: set[str], context_figures: set[str]) -> list[str]:
    """Which of ``answer_figures`` cannot be traced to ``context_figures``."""
    parsed_context = [
        parsed for parsed in map(_parse_figure, context_figures) if parsed is not None
    ]
    unsupported = []
    for figure in answer_figures:
        if figure in context_figures:
            continue
        if _rounds_from_context(figure, parsed_context):
            continue
        unsupported.append(figure)
    return unsupported


def _rounds_from_context(
    figure: str, parsed_context: list[tuple[str, float, float, int]]
) -> bool:
    """Whether any context figure, rounded as the answer wrote it, equals it."""
    parsed = _parse_figure(figure)
    if parsed is None:
        return False
    kind, value, scale, places = parsed
    written = round(value / scale, places)
    for context_kind, context_value, _, _ in parsed_context:
        if context_kind != kind:
            continue
        if round(context_value / scale, places) == written:
            return True
    return False


def validate(answer: str, context_figures: set[str]) -> ValidationResult:
    """Inspect an answer. Returns a verdict, never raises on bad content."""
    violations: list[Violation] = []
    warnings: list[Violation] = []
    lowered = answer.lower()

    # 1. Every figure must trace to the context, exactly or as a rounding of one.
    answer_figures = extract_figures(answer)
    unsupported = sorted(unsupported_figures(answer_figures, context_figures))
    if unsupported:
        violations.append(
            Violation(
                kind="unsupported_figure",
                detail=(
                    f"{len(unsupported)} figure(s) in the answer do not appear in the retrieved "
                    "context"
                ),
                evidence=", ".join(unsupported[:8]),
                severity=ERROR,
            )
        )

    # 2. Banned vocabulary, inherited and extended -- but judged on the claim,
    #    not the substring. "These are unserved client flows, NOT bank revenue"
    #    is exactly the sentence the system prompt asks for, and a strict ban
    #    rejected it four times in five. A phrase preceded by a negation is a
    #    correct denial and is allowed; the same phrase asserted is not.
    #
    #    Note this is looser than the rule applied to the deterministic layer's
    #    own text, which is banned outright. That asymmetry is deliberate: for
    #    prose we author, avoiding the phrase entirely is free and removes all
    #    ambiguity; for prose a model writes, the meaning is what matters.
    for phrase in (*FORBIDDEN_PHRASES, *FORBIDDEN_GENERATIVE_PHRASES):
        for position in _occurrences(lowered, phrase):
            if _negated_before(lowered, position):
                continue
            violations.append(
                Violation(
                    kind="forbidden_phrase",
                    detail=f"answer asserts the banned phrase {phrase!r}",
                    evidence=_excerpt(answer, phrase),
                    severity=ERROR,
                )
            )

    #    Voice, not fact. Noted and kept -- see DISCOURAGED_GENERATIVE_PHRASES.
    for phrase in DISCOURAGED_GENERATIVE_PHRASES:
        for position in _occurrences(lowered, phrase):
            if _negated_before(lowered, position):
                continue
            warnings.append(
                Violation(
                    kind="analyst_voice",
                    detail=f"answer narrates itself as an analyst: {phrase!r}",
                    evidence=_excerpt(answer, phrase),
                    severity=WARNING,
                )
            )

    # 3. Pillar-specific terminology. Checked by *proximity*, not by sentence
    #    co-occurrence: "Three Share of Wallet pillars ... and two opportunity
    #    signals (Lending, Investment Banking)" is a correct sentence that
    #    distinguishes the two, and failing it would make the guard cry wolf on
    #    the model's own methodology note. What is actually wrong is a share
    #    term *attached* to lending or investment banking.
    #
    #    Warned rather than rejected. Proximity is a heuristic over English word
    #    order, and it has no way to tell "lending has no share of wallet"
    #    restructured across a clause boundary from the error it is looking for.
    #    The prompt states the rule in three places; this records how well that
    #    worked instead of throwing away the answer when it reads ambiguously.
    warnings.extend(
        _attribution_violations(
            answer,
            LENDING_TERMS,
            "lending_share_of_wallet",
            "attaches a share of wallet to lending, which does not exist",
        )
    )
    warnings.extend(
        _attribution_violations(
            answer,
            IB_TERMS,
            "ib_share_of_wallet",
            "attaches a share of wallet to investment banking, which does not exist",
        )
    )

    # 4. Claims the data cannot support, in whatever words. Rejections: each of
    #    these is false about the system itself, not merely off-message.
    for pattern in COMPETITOR_OWNERSHIP:
        for match in pattern.finditer(answer):
            if _negated_before(lowered, match.start()):
                continue
            violations.append(
                Violation(
                    kind="competitor_ownership",
                    detail="answer claims to know who holds the unobserved flow",
                    evidence=_around(answer, match.start(), match.end()),
                    severity=ERROR,
                )
            )
    for pattern in EXTERNAL_SOURCE:
        for match in pattern.finditer(answer):
            violations.append(
                Violation(
                    kind="external_source",
                    detail="answer attributes a figure to research outside the context",
                    evidence=_around(answer, match.start(), match.end()),
                    severity=ERROR,
                )
            )
    for match in IB_RAND_ATTRIBUTION.finditer(answer):
        if _OTHER_PILLARS.search(match.group("gap")):
            continue
        if _negated_before(lowered, match.start()):
            continue
        violations.append(
            Violation(
                kind="ib_rand_attribution",
                detail="answer gives investment banking a rand figure, which does not exist",
                evidence=_around(answer, match.start(), match.end()),
                severity=ERROR,
            )
        )

    # 5. Investment banking must never carry a rand figure.
    #
    #    Warned rather than rejected, for one specific reason: the figure itself
    #    has already passed the allow-list check above, so it is a real number
    #    from the deterministic layer. The risk here is mis-attribution, not
    #    fabrication. And the test is sentence co-occurrence, which fails on
    #    "the FX opportunity is R8.75bn; investment banking is a ranked signal
    #    with no rand figure" -- a sentence that states the rule correctly and
    #    mentions a rand amount belonging to a different pillar.
    for sentence in _sentences(answer):
        sentence_lower = sentence.lower()
        if not any(term in sentence_lower for term in IB_TERMS):
            continue
        if any(token.startswith("r") for token in extract_figures(sentence)):
            warnings.append(
                Violation(
                    kind="ib_rand_figure",
                    detail="a sentence attaches a rand amount to investment banking",
                    evidence=sentence[:200],
                    severity=WARNING,
                )
            )

    return ValidationResult(
        ok=not violations,
        violations=violations,
        warnings=warnings,
        unsupported_figures=unsupported,
        checked_figures=len(answer_figures),
    )


#: How far before a share term to look for the pillar it might be attached to.
ATTRIBUTION_WINDOW = 60

#: Words that mean the sentence is denying the claim rather than making it.
#: "Lending has no share of wallet" and "client flows, not bank revenue" are
#: both correct statements, not breaches.
NEGATIONS = (
    "no ",
    "not ",
    "never",
    "cannot",
    "does not",
    "is not",
    "are not",
    "no share",
    "without",
    "rather than",
    "instead of",
    "neither",
    "nor ",
)

#: How far before a banned phrase to look for a negation. One clause.
NEGATION_WINDOW = 45


def _occurrences(lowered: str, phrase: str):
    """Every start index of ``phrase`` in an already-lowercased string."""
    start = 0
    while True:
        position = lowered.find(phrase, start)
        if position < 0:
            return
        yield position
        start = position + len(phrase)


def _negated_before(lowered: str, position: int, window: int = NEGATION_WINDOW) -> bool:
    """Whether the text just before ``position`` denies what follows it."""
    return any(
        negation in lowered[max(0, position - window) : position] for negation in NEGATIONS
    )


def _attribution_violations(
    answer: str, pillar_terms: tuple[str, ...], kind: str, detail: str
) -> list[Violation]:
    """Flag a share term whose immediately preceding text names a signal pillar."""
    lowered = answer.lower()
    found: list[Violation] = []
    for share_term in SHARE_TERMS:
        start = 0
        while True:
            position = lowered.find(share_term, start)
            if position < 0:
                break
            start = position + len(share_term)
            window = lowered[max(0, position - ATTRIBUTION_WINDOW) : position]
            if not any(term in window for term in pillar_terms):
                continue
            if any(negation in window for negation in NEGATIONS):
                continue
            found.append(
                Violation(
                    kind=kind,
                    detail=detail,
                    evidence=answer[max(0, position - ATTRIBUTION_WINDOW) : position + 40].replace(
                        "\n", " "
                    ),
                    severity=WARNING,
                )
            )
    return found


def _around(text: str, start: int, end: int, width: int = 45) -> str:
    """The match plus enough either side to read it as a claim."""
    return text[max(0, start - width) : end + width].replace("\n", " ")


def _excerpt(text: str, phrase: str, width: int = 90) -> str:
    position = text.lower().find(phrase)
    if position < 0:
        return ""
    start = max(0, position - width // 2)
    return text[start : start + width].replace("\n", " ")
