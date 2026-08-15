"""Every rule, threshold and phrase the intelligence layer uses.

This module is the whole configuration surface of stage 4. Nothing downstream
invents a threshold, a status, or a form of words: if a banker-facing sentence
says something, the sentence template is here and the numbers in it come from
the analytical contract.

Two things it is careful about.

**Status is a rule, not a judgement.** :func:`classify_status` is a pure function
of five published fields, and the rules are written out below rather than being
implied by nested ``if`` statements scattered across a renderer.

**Terminology is enforced, not encouraged.** :data:`FORBIDDEN_PHRASES` is checked
against every generated string by a test. The cash pillar's rand figure is the
client's own operating turnover; calling it a fee pool or a revenue opportunity
is a commercial misstatement, and the check makes that impossible to ship.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..wallet import assumptions

INTELLIGENCE_VERSION = "intelligence-1.0.0"

#: The analytical contract this layer is built against. A mismatch means the
#: model was rebuilt under a different methodology and this layer must be
#: re-validated rather than silently re-run.
REQUIRED_METHODOLOGY = assumptions.METHODOLOGY_VERSION

# ---------------------------------------------------------------------------
# Product roles -- taken from the model, never redeclared
# ---------------------------------------------------------------------------

#: How much weight a product's class earns in opportunity selection. A CORE
#: pillar has a defensible share of wallet behind it; a SIGNAL_ONLY pillar has a
#: ranked signal and no rand at all, so it can support a conversation but should
#: rarely be the reason for one.
ROLE_WEIGHT = {
    assumptions.CORE: 1.00,
    assumptions.SUPPORTING: 0.85,
    assumptions.SIGNAL_ONLY: 0.55,
}

#: How much weight the confidence band earns. LOW is not excluded -- a large,
#: uncertain opportunity is still worth knowing about -- but it is discounted
#: hard enough that it cannot outrank a well-evidenced smaller one.
CONFIDENCE_WEIGHT = {"HIGH": 1.00, "MEDIUM": 0.80, "LOW": 0.55}

#: Deduction applied when the pillar carries a HIGH-severity diagnostic for this
#: client -- the "do not quote before review" class.
HIGH_SEVERITY_PENALTY = 0.20

#: Deduction applied when the estimate is materially benchmark-sensitive.
SENSITIVITY_PENALTY = 0.10

# ---------------------------------------------------------------------------
# Sensitivity classification
# ---------------------------------------------------------------------------

#: ``(high - low) / base`` at or below this is stable across tested assumptions.
SENSITIVITY_STABLE_RANGE = 0.10
#: ...and at or below this is moderate. Above it, the estimate is sensitive.
SENSITIVITY_MODERATE_RANGE = 0.35

#: Swing in within-product rank across the scenario grid.
RANK_STABLE_SWING = 2
RANK_MODERATE_SWING = 5

STABLE = "STABLE"
MODERATE = "MODERATE"
SENSITIVE = "SENSITIVE"
NOT_APPLICABLE = "NOT_APPLICABLE"

SENSITIVITY_PHRASE = {
    STABLE: "stable across tested assumptions",
    MODERATE: "moderately sensitive to benchmark assumptions",
    SENSITIVE: "sensitive to benchmark assumptions",
    NOT_APPLICABLE: "no rand estimate to test",
}

RANK_STABILITY_PHRASE = {
    STABLE: "holds its position across every tested assumption",
    MODERATE: "moves a few places depending on the benchmark assumption",
    SENSITIVE: "moves substantially depending on the benchmark assumption",
    NOT_APPLICABLE: "not rank-tested",
}

# ---------------------------------------------------------------------------
# Opportunity status
# ---------------------------------------------------------------------------

PRIORITY = "PRIORITY"
INVESTIGATE = "INVESTIGATE"
MONITOR = "MONITOR"
NO_HEADROOM = "NO_HEADROOM_DEMONSTRATED"

STATUS_ORDER = (PRIORITY, INVESTIGATE, MONITOR, NO_HEADROOM)

#: A pillar with a rand basis must show at least this much headroom, as a
#: fraction of its addressable figure, to count as an opportunity at all. Below
#: it, Syn Bank already handles essentially everything the model can size and
#: the honest answer is that no headroom was demonstrated.
MIN_HEADROOM_FRACTION = 0.05

#: Commercial opportunity score floors.
PRIORITY_MIN_SCORE = 0.65
INVESTIGATE_MIN_SCORE = 0.45

#: **PRIORITY requires HIGH confidence.** A LOW or MEDIUM confidence row cannot
#: reach PRIORITY through any combination of size and score. This is the single
#: most important rule in the file: it is what stops a large, weakly evidenced
#: FX number becoming a call-list item.
PRIORITY_REQUIRED_BAND = "HIGH"

#: The only way a non-HIGH row becomes PRIORITY: a named, reasoned entry here.
#: Empty by design. An override is a decision a person made and signed, not a
#: threshold, so it is recorded per client x product with its reason and a test
#: asserts nothing reaches PRIORITY without either HIGH confidence or an entry.
PRIORITY_OVERRIDES: dict[tuple[str, str], str] = {}

#: A HIGH-severity diagnostic blocks PRIORITY regardless of confidence.
HIGH_SEVERITY_BLOCKS_PRIORITY = True

#: A SIGNAL_ONLY product can never be PRIORITY or INVESTIGATE: there is no rand
#: figure to investigate towards. It is a MONITOR signal by construction.
SIGNAL_ONLY_MAX_STATUS = MONITOR

STATUS_NOTE = {
    PRIORITY: (
        "High-confidence opportunity with demonstrated headroom and no unresolved model "
        "warning. Recommend investigation."
    ),
    INVESTIGATE: (
        "Credible opportunity carrying either a moderate confidence band or an open model "
        "warning. Consider investigation, with the caveat attached."
    ),
    MONITOR: (
        "Weakly evidenced, benchmark-sensitive, or signal-only. Monitor and validate before "
        "pursuing; do not take the rand figure to a client meeting on its own."
    ),
    NO_HEADROOM: (
        "The model could not demonstrate headroom: Syn Bank already handles essentially all of "
        "the activity the model can size, or the activity could not be sized at all. This is a "
        "retention conversation, not a growth one."
    ),
}

#: The banker-facing action phrase for each status. Section 6 of the brief.
STATUS_ACTION = {
    PRIORITY: "Recommend investigation",
    INVESTIGATE: "Consider investigation",
    MONITOR: "Monitor / validate before pursuing",
    NO_HEADROOM: "No headroom demonstrated — retention conversation",
}

#: Confidence band to recommendation phrasing, for text that speaks about the
#: evidence rather than the action.
CONFIDENCE_PHRASE = {
    "HIGH": (
        "HIGH — every driver behind this estimate was disclosed by the client and the method is "
        "anchored on an accounting identity or a structural fact"
    ),
    "MEDIUM": (
        "MEDIUM — the estimate is usable but leans on at least one imputed driver or a "
        "peer-benchmarked coefficient"
    ),
    "LOW": (
        "LOW — the estimate rests materially on imputed drivers or on a proxy whose economic "
        "logic is weak for this sector; validate before acting"
    ),
}


def classify_status(
    product_class: str,
    confidence_band: str,
    commercial_score: float,
    high_severity_diagnostic: bool,
    headroom_fraction: float | None,
    has_rand_basis: bool,
    entity_id: str = "",
    product: str = "",
) -> tuple[str, str]:
    """Assign an opportunity status. Pure function of published fields.

    Returns ``(status, reason)``. The reason names the rule that fired, so a
    banker asking "why is this only MONITOR" gets an answer rather than a
    shrug.

    The rules, in order:

    1. A rand pillar with no demonstrated headroom is ``NO_HEADROOM_DEMONSTRATED``.
    2. A ``SIGNAL_ONLY`` pillar can rise no higher than ``MONITOR``.
    3. ``PRIORITY`` needs HIGH confidence (or a named override), a score at or
       above :data:`PRIORITY_MIN_SCORE`, and no HIGH-severity diagnostic.
    4. ``INVESTIGATE`` needs a score at or above :data:`INVESTIGATE_MIN_SCORE`.
    5. Everything else is ``MONITOR``.
    """
    # A NULL headroom arrives as NaN rather than None whenever pandas has put
    # it in a float column alongside real values, and ``NaN < 0.05`` is False,
    # so without this the "could not be sized" case would fall through to the
    # ordinary scoring path and be published as a live opportunity.
    if headroom_fraction is not None and math.isnan(headroom_fraction):
        headroom_fraction = None

    if has_rand_basis:
        if headroom_fraction is None:
            return NO_HEADROOM, "no defensible rand denominator, so no headroom can be sized"
        if headroom_fraction < MIN_HEADROOM_FRACTION:
            return (
                NO_HEADROOM,
                f"headroom is {headroom_fraction:.1%} of the addressable figure, below the "
                f"{MIN_HEADROOM_FRACTION:.0%} floor",
            )

    if product_class == assumptions.SIGNAL_ONLY:
        return (
            SIGNAL_ONLY_MAX_STATUS,
            "signal-only pillar: no rand figure exists, so it can support a conversation but "
            "cannot be the subject of one",
        )

    override = PRIORITY_OVERRIDES.get((entity_id, product))
    band_allows_priority = confidence_band == PRIORITY_REQUIRED_BAND or override is not None

    if commercial_score >= PRIORITY_MIN_SCORE and band_allows_priority:
        if HIGH_SEVERITY_BLOCKS_PRIORITY and high_severity_diagnostic:
            return (
                INVESTIGATE,
                "score and confidence support PRIORITY, but an unresolved HIGH-severity model "
                "diagnostic blocks it until reviewed",
            )
        if override is not None:
            return PRIORITY, f"named override: {override}"
        return (
            PRIORITY,
            f"HIGH confidence, commercial score {commercial_score:.2f} at or above "
            f"{PRIORITY_MIN_SCORE:.2f}, and no HIGH-severity diagnostic",
        )

    if commercial_score >= INVESTIGATE_MIN_SCORE:
        if confidence_band == "LOW":
            return (
                MONITOR,
                f"commercial score {commercial_score:.2f} would support investigation, but LOW "
                "confidence means the estimate must be validated first",
            )
        return (
            INVESTIGATE,
            f"commercial score {commercial_score:.2f} at or above "
            f"{INVESTIGATE_MIN_SCORE:.2f} on {confidence_band} confidence",
        )

    return (
        MONITOR,
        f"commercial score {commercial_score:.2f} below the {INVESTIGATE_MIN_SCORE:.2f} "
        "investigation floor",
    )


# ---------------------------------------------------------------------------
# Terminology
# ---------------------------------------------------------------------------

#: The noun each pillar's rand figure must be given in banker-facing text.
DENOMINATOR_LABEL = {
    assumptions.CASH: "Addressable Cash Flow",
    assumptions.FX: "peer-benchmark addressable FX activity",
    assumptions.TRADE: "peer-benchmark addressable trade-finance activity",
    assumptions.LENDING: "financing opportunity",
    assumptions.IB: "investment-banking opportunity signal",
}

#: The one-line caveat that must travel with each pillar's rand figure.
DENOMINATOR_CAVEAT = {
    assumptions.CASH: (
        "Addressable Cash Flow is the client's own annual operating turnover — the collections "
        "and supplier payments it must move through a bank account somewhere. It is not bank "
        "income, and nothing is estimated on it in fee terms, because Syn Bank discloses no "
        "pricing."
    ),
    assumptions.FX: (
        "No disclosure states this client's total cross-border activity, so the addressable "
        "figure is the client's own disclosed exposure scaled by the settlement intensity of its "
        "peers, with this client excluded from that peer population. It is a benchmark "
        "expectation, not a disclosed market total."
    ),
    assumptions.TRADE: (
        "No disclosure states this client's total trade-finance issuance, so the addressable "
        "figure is the client's own procurement and export base scaled by peer issuance "
        "intensity, with this client excluded from that peer population. It is a benchmark "
        "expectation, not a disclosed market total."
    ),
    assumptions.LENDING: (
        "This is a financing-need indicator built from the client's disclosed debt structure — "
        "debt falling due, undrawn committed facilities, the working-capital cycle and capex. "
        "Syn Bank's supplied data contains no loan book, so no share of wallet is computed and "
        "none should be quoted."
    ),
    assumptions.IB: (
        "A ranked mandate-likelihood signal relative to the other nineteen clients in this "
        "portfolio. No rand amount is estimated, because nothing in the supplied data indicates "
        "a planned issue, disposal or acquisition."
    ),
}

#: Phrases that must never appear in generated banker-facing text. Checked
#: against every string this layer produces.
FORBIDDEN_PHRASES = (
    "fee pool",
    "fee wallet",
    "bank revenue",
    "revenue opportunity",
    "revenue wallet",
    "competitor-held",
    "competitor held",
    "held by competitors",
    "win back",
    "win-back",
    "lost revenue",
    "guaranteed revenue",
    "confirmed revenue",
    "total opportunity across",
    "lending share of wallet",
    "investment banking share of wallet",
)

#: Pillars whose text may use the phrase "share of wallet" at all.
SHARE_OF_WALLET_PILLARS = frozenset(assumptions.WALLET_PILLARS)


@dataclass(frozen=True)
class SelectionWeights:
    """The weights behind :mod:`.selection`. Declared, not tuned."""

    role: dict[str, float]
    confidence: dict[str, float]
    high_severity_penalty: float
    sensitivity_penalty: float

    def as_records(self) -> list[dict[str, Any]]:
        rows = [
            {"kind": "role_weight", "key": key, "value": value}
            for key, value in self.role.items()
        ]
        rows += [
            {"kind": "confidence_weight", "key": key, "value": value}
            for key, value in self.confidence.items()
        ]
        rows.append(
            {
                "kind": "penalty",
                "key": "high_severity_diagnostic",
                "value": self.high_severity_penalty,
            }
        )
        rows.append(
            {"kind": "penalty", "key": "benchmark_sensitive", "value": self.sensitivity_penalty}
        )
        return rows


SELECTION_WEIGHTS = SelectionWeights(
    role=dict(ROLE_WEIGHT),
    confidence=dict(CONFIDENCE_WEIGHT),
    high_severity_penalty=HIGH_SEVERITY_PENALTY,
    sensitivity_penalty=SENSITIVITY_PENALTY,
)


def registry() -> list[dict[str, Any]]:
    """Every configured rule as flat records, for the run report."""
    rows: list[dict[str, Any]] = SELECTION_WEIGHTS.as_records()
    rows += [
        {"kind": "status_threshold", "key": "priority_min_score", "value": PRIORITY_MIN_SCORE},
        {
            "kind": "status_threshold",
            "key": "investigate_min_score",
            "value": INVESTIGATE_MIN_SCORE,
        },
        {
            "kind": "status_threshold",
            "key": "min_headroom_fraction",
            "value": MIN_HEADROOM_FRACTION,
        },
        {
            "kind": "sensitivity_threshold",
            "key": "stable_range",
            "value": SENSITIVITY_STABLE_RANGE,
        },
        {
            "kind": "sensitivity_threshold",
            "key": "moderate_range",
            "value": SENSITIVITY_MODERATE_RANGE,
        },
        {"kind": "sensitivity_threshold", "key": "rank_stable_swing", "value": RANK_STABLE_SWING},
        {
            "kind": "sensitivity_threshold",
            "key": "rank_moderate_swing",
            "value": RANK_MODERATE_SWING,
        },
    ]
    return rows
