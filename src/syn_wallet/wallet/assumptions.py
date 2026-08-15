"""Every coefficient, threshold and applicability judgement the wallet engine uses.

Nothing numeric lives in the pillar models. If a number influences an estimate,
it is declared here with a rationale and a **basis** saying where its authority
comes from:

``accounting_identity``
    The coefficient is 1.0 because the cash must move. Revenue is collected into
    a bank account; cost of sales is paid out of one. These are not estimates.
``structural``
    The coefficient follows from a disclosed structure -- debt classified as
    current is contractually repayable within twelve months, undrawn committed
    facilities are by definition unused headroom another bank is providing.
``portfolio_benchmark``
    No identity exists, so the coefficient is measured from this portfolio at
    build time: the intensity a well-penetrated peer achieves. Computed in
    :mod:`.benchmarks`, recorded in the run report, and reproducible from the
    feature table. These carry a placeholder value here and are resolved at run
    time.
``judgement``
    A number that could not be derived. There are deliberately few; each is
    named in the model report and each has a diagnostic that fires when an
    estimate leans on it.

Sector applicability is declared here too, because "should an insurer be scored
for import letters of credit" is a modelling judgement, not a data fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

METHODOLOGY_VERSION = "wallet-1.0.0"

# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

CASH = "cash_management"
FX = "fx_global_markets"
TRADE = "trade_finance"
LENDING = "lending"
IB = "investment_banking"

PRODUCTS = (CASH, FX, TRADE, LENDING, IB)

PRODUCT_LABELS = {
    CASH: "Transactional / Cash Management",
    FX: "FX / Global Markets",
    TRADE: "Trade Finance",
    LENDING: "Lending",
    IB: "Investment Banking / Capital Markets",
}

#: What the ``estimate_zar`` column means for each product. These bases are not
#: interchangeable and must never be compared as one number across products.
ESTIMATE_BASIS = {
    CASH: "total_addressable_market",
    FX: "peer_benchmark_addressable",
    TRADE: "peer_benchmark_addressable",
    LENDING: "financing_opportunity",
    IB: "signal_only",
}

ESTIMATE_BASIS_NOTES = {
    "total_addressable_market": (
        "The client's whole disclosed flow for this activity, across all of its banks. "
        "Share is the fraction Syn Bank currently handles."
    ),
    "peer_benchmark_addressable": (
        "No disclosure states this client's total activity across all banks, so the wallet is "
        "the client's own economic driver scaled by the intensity a well-penetrated peer in this "
        "portfolio achieves. Share is penetration relative to that peer benchmark, not to a "
        "disclosed total. This understates true wallet where the whole portfolio is "
        "under-penetrated."
    ),
    "financing_opportunity": (
        "A financing need indicator built from disclosed debt structure, not a claim about total "
        "lending wallet or about business a competitor holds."
    ),
    "signal_only": (
        "A ranked signal of mandate likelihood. No rand amount is estimated because the data "
        "cannot support one."
    ),
}

SECTORS = (
    "consumer",
    "industrials_pharma",
    "insurance",
    "mining",
    "real_estate",
    "tech",
    "telecoms",
)

# ---------------------------------------------------------------------------
# Assumption registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Assumption:
    """One declared coefficient or threshold."""

    name: str
    value: float | None
    unit: str
    product: str
    sector: str | None  # None means "every sector"
    basis: str
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "product": self.product,
            "sector": self.sector or "all",
            "basis": self.basis,
            "rationale": self.rationale,
        }


ACCOUNTING_IDENTITY = "accounting_identity"
STRUCTURAL = "structural"
PORTFOLIO_BENCHMARK = "portfolio_benchmark"
JUDGEMENT = "judgement"

#: Assumptions with a fixed value. Portfolio benchmarks are declared with
#: ``value = None`` and filled in at run time by :mod:`.benchmarks`.
STATIC_ASSUMPTIONS: tuple[Assumption, ...] = (
    # --- Cash management -----------------------------------------------------
    Assumption(
        "collections_banked_share", 1.0, "fraction of revenue", CASH, None, ACCOUNTING_IDENTITY,
        "Revenue is ultimately received into a bank account, so the collections a corporate "
        "generates across all of its banks equals its revenue. This is an identity, not a "
        "coefficient to tune. Where reported revenue is not a cash measure (insurance), the "
        "sector applicability weight and a diagnostic flag carry the caveat instead of a "
        "fabricated haircut.",
    ),
    Assumption(
        "supplier_payment_share_of_cogs", 1.0, "fraction of cost of sales", CASH, None,
        ACCOUNTING_IDENTITY,
        "Cost of sales is settled in cash to suppliers through a bank account. Timing differences "
        "shift the payment between periods but not the annual total in a steady state.",
    ),
    Assumption(
        "payroll_rand_wallet", None, "excluded", CASH, None, JUDGEMENT,
        "DELIBERATELY EXCLUDED from the rand wallet. No employee-cost field exists in the "
        "external data, and observed payroll volume is a token R61m across the whole portfolio "
        "for a year at an average of R11k per transaction. Sizing a payroll wallet would require "
        "inventing a cost per head. Payroll is carried instead as a mandate signal (rows per "
        "employee) feeding confidence and the narrative.",
    ),
    Assumption(
        "tax_rand_wallet", None, "excluded", CASH, None, JUDGEMENT,
        "DELIBERATELY EXCLUDED. No tax charge is disclosed, and observed tax volume is R163m "
        "across the portfolio for a year on 5,395 instructions over three years. Retained as an "
        "engagement signal only.",
    ),
    Assumption(
        "intercompany_rand_wallet", None, "excluded", CASH, None, JUDGEMENT,
        "DELIBERATELY EXCLUDED from both numerator and denominator. Treasury sweep volume has no "
        "external anchor, and including observed sweeps in the numerator while the denominator "
        "cannot cover them would inflate share. Reported separately as out-of-scope observed "
        "activity so the excluded amount stays visible.",
    ),
    # --- FX ------------------------------------------------------------------
    Assumption(
        "fx_forward_rolls_per_year", 1.0, "rolls", FX, None, STRUCTURAL,
        "A disclosed forward book has to be executed at least once to exist. Corporates typically "
        "roll shorter-dated hedges several times a year, but no forward tenor is disclosed, so "
        "one roll is used as a deliberate floor.",
    ),
    # --- Trade finance -------------------------------------------------------
    Assumption(
        "trade_observed_scope", None, "all statuses, issued in the fiscal year", TRADE, None,
        STRUCTURAL,
        "The trade numerator is the value of instruments dated inside the fiscal year across all "
        "four statuses, because the denominator is annual issuance demand. This is the one place "
        "the four statuses are legitimately summed, and it is stated rather than assumed. The "
        "live book (active + issued) is reported alongside it, never inside it.",
    ),
    # --- Lending -------------------------------------------------------------
    Assumption(
        "refinancing_share_of_current_debt", 1.0, "fraction of current debt", LENDING, None,
        STRUCTURAL,
        "Debt classified as current is contractually repayable within twelve months. It must be "
        "repaid from cash or refinanced, so the whole balance is a financing decision inside the "
        "horizon. This is not a claim that Syn Bank could win it.",
    ),
    Assumption(
        "undrawn_facility_share", 1.0, "fraction of undrawn facilities", LENDING, None, STRUCTURAL,
        "Undrawn committed facilities are, by disclosure, committed capacity another lender is "
        "already providing and the client is not using. The full balance is a contestable "
        "facility at its next renewal.",
    ),
    Assumption(
        "capex_debt_funded_share", 0.30, "fraction of annual capex", LENDING, None, JUDGEMENT,
        "THE ONE UNDERIVED COEFFICIENT IN THE ENGINE. Capex is funded from a mix of operating "
        "cash flow and new debt, and no cash-flow statement field exists to split it. 0.30 is a "
        "deliberately conservative third. A diagnostic fires whenever this component exceeds "
        "half of a client's lending estimate, and the component breakdown lets a reviewer set it "
        "to zero.",
    ),
    # --- Investment banking --------------------------------------------------
    Assumption(
        "ib_near_term_maturity_threshold", 0.30, "fraction of gross debt", IB, None, JUDGEMENT,
        "A debt capital markets category is only assigned when at least 30% of gross debt is "
        "classified current. Below that, a near-term refinancing mandate is not supported by the "
        "disclosure.",
    ),
    Assumption(
        "ib_capex_intensity_threshold", 0.10, "capex / revenue", IB, None, JUDGEMENT,
        "A corporate finance / project funding category is only assigned above a 10% capex "
        "intensity, the level at which an investment programme is large enough to need external "
        "structuring.",
    ),
    Assumption(
        "ib_leverage_threshold", 0.50, "net debt / revenue", IB, None, JUDGEMENT,
        "A refinancing or restructuring category needs leverage above half of annual revenue "
        "alongside an elevated cost of debt; below it the balance sheet does not indicate stress.",
    ),
    Assumption(
        "ib_cost_of_debt_threshold", 0.09, "finance costs / gross debt", IB, None, JUDGEMENT,
        "An implied blended cost of debt above 9% is elevated against the South African policy "
        "rate over the reporting window and supports a refinancing conversation.",
    ),
    # --- Cross-cutting -------------------------------------------------------
    Assumption(
        "swift_overlap_treatment", None, "exclusion", CASH, None, STRUCTURAL,
        "SWIFT-channel transactional rows conceptually overlap the cross-border pillar and the "
        "overlap cannot be resolved from the supplied fields. They are excluded from the cash "
        "numerator and NOT added to the FX numerator, so no rand is counted twice in either "
        "direction. The excluded amount is published per client.",
    ),
    Assumption(
        "share_cap", 1.0, "share", "all", None, JUDGEMENT,
        "Reported share is capped at 100%. An uncapped share above 1.0 means observed activity "
        "exceeds the estimated wallet, which is a model finding, not a client fact; the uncapped "
        "value and a diagnostic flag are both retained.",
    ),
    Assumption(
        "benchmark_percentile", 0.75, "percentile", "all", None, JUDGEMENT,
        "Portfolio benchmarks are set at the 75th percentile of observed intensity, not the "
        "maximum. The maximum would let a single outlier define every client's wallet; the "
        "median would define the wallet as average performance and understate the opportunity. "
        "The upper quartile is 'what a well-penetrated peer achieves'.",
    ),
    Assumption(
        "opportunity_weight_gap", 0.45, "weight", "all", None, JUDGEMENT,
        "Weight on the within-product percentile rank of the rand gap. Percentile rather than "
        "raw rand so that a client with a trillion-rand revenue base cannot dominate on scale "
        "alone, and so products with different estimate bases stay comparable.",
    ),
    Assumption(
        "opportunity_weight_confidence", 0.30, "weight", "all", None, JUDGEMENT,
        "Weight on model confidence. Large opportunities resting on imputed denominators are "
        "pushed down the ranking rather than silently promoted.",
    ),
    Assumption(
        "opportunity_weight_headroom", 0.25, "weight", "all", None, JUDGEMENT,
        "Weight on commercial headroom (1 - share). A client where Syn Bank already handles most "
        "of the addressable activity is a retention conversation, not a growth opportunity.",
    ),
    Assumption(
        "confidence_band_high", 0.70, "confidence", "all", None, JUDGEMENT,
        "HIGH band floor: every driver disclosed, identity-anchored, sector-applicable.",
    ),
    Assumption(
        "confidence_band_medium", 0.45, "confidence", "all", None, JUDGEMENT,
        "MEDIUM band floor. Below it an estimate leans materially on imputation or on a proxy "
        "whose economic logic is weak for the sector.",
    ),
)

# Convenience lookups.
CAPEX_DEBT_FUNDED_SHARE = 0.30
FX_FORWARD_ROLLS_PER_YEAR = 1.0
BENCHMARK_PERCENTILE = 0.75
SHARE_CAP = 1.0
CONFIDENCE_BAND_HIGH = 0.70
CONFIDENCE_BAND_MEDIUM = 0.45
OPPORTUNITY_WEIGHTS = {"gap": 0.45, "confidence": 0.30, "headroom": 0.25}
IB_THRESHOLDS = {
    "near_term_maturity": 0.30,
    "capex_intensity": 0.10,
    "leverage": 0.50,
    "cost_of_debt": 0.09,
}

# ---------------------------------------------------------------------------
# Sector applicability
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SectorRule:
    """How well a product's economic logic fits a sector."""

    applicability: float
    note: str
    #: Sub-models switched off entirely because their driver is meaningless here.
    suppress_components: tuple[str, ...] = field(default=())


_FULL = "The model's economic drivers apply to this sector without adjustment."

#: product -> sector -> rule. A sector absent from a product's map gets
#: :data:`DEFAULT_SECTOR_RULE`.
SECTOR_RULES: dict[str, dict[str, SectorRule]] = {
    CASH: {
        "insurance": SectorRule(
            0.55,
            "Reported insurance revenue includes investment return and non-cash reserve "
            "movements, so it overstates the cash actually collected. The collections component "
            "is retained as an upper bound and flagged rather than reduced by a fabricated "
            "haircut.",
        ),
    },
    FX: {
        "real_estate": SectorRule(
            0.75,
            "Property income is earned and collected in-country. Cross-border need is limited to "
            "financing and distribution flows rather than trade settlement.",
        ),
    },
    TRADE: {
        "insurance": SectorRule(
            0.30,
            "An insurer buys no goods, so cost of sales and inventory are not trade-finance "
            "drivers. The import and export documentary sub-models are switched off; only the "
            "guarantee sub-model applies, since financial and performance guarantees are genuine "
            "for a financial services group.",
            suppress_components=("import_documentary", "export_documentary"),
        ),
        "real_estate": SectorRule(
            0.30,
            "A property group holds no tradeable inventory and imports no goods. Import and "
            "export documentary sub-models are switched off; guarantees remain, covering rental "
            "deposits, construction performance bonds and utility guarantees.",
            suppress_components=("import_documentary", "export_documentary"),
        ),
        "tech": SectorRule(
            0.60,
            "Digital and marketplace revenue settles electronically rather than under documentary "
            "credit, so the trade-finance drivers are weaker here than for a goods business, but "
            "device and hardware procurement keeps them relevant.",
        ),
        "telecoms": SectorRule(
            0.60,
            "Trade finance applies to network equipment and handset procurement, a minority of "
            "the cost base, and neither telecoms client discloses cost of sales at all.",
        ),
    },
    LENDING: {},
    IB: {},
}

DEFAULT_SECTOR_RULE = SectorRule(1.0, _FULL)


def sector_rule(product: str, sector: str) -> SectorRule:
    return SECTOR_RULES.get(product, {}).get(sector, DEFAULT_SECTOR_RULE)


#: Sectors where ``cost_of_sales`` is a procurement proxy comparable across
#: companies. Insurance and real estate report a cost line that is not a cost of
#: goods, so their cost of sales is used only when disclosed and is never
#: imputed from a peer ratio.
COGS_COMPARABLE_SECTORS = frozenset({"consumer", "industrials_pharma", "mining", "tech", "telecoms"})


def registry() -> list[dict[str, Any]]:
    """The static assumption registry as plain dicts, for the run report."""
    return [assumption.as_dict() for assumption in STATIC_ASSUMPTIONS]


def sector_rule_registry() -> list[dict[str, Any]]:
    """Every product x sector applicability decision, for the run report."""
    rows = []
    for product in PRODUCTS:
        for sector in SECTORS:
            rule = sector_rule(product, sector)
            rows.append(
                {
                    "product": product,
                    "sector": sector,
                    "applicability": rule.applicability,
                    "suppressed_components": ", ".join(rule.suppress_components),
                    "note": rule.note,
                }
            )
    return rows
