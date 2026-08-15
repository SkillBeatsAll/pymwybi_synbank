"""Deterministic retrieval over the commercial intelligence tables.

Filtering, ranking and selection all happen here, in pandas, before any prose is
generated. The language model receives a small, already-correct set of rows and
is asked only to describe them.

This is the difference between a system that can be checked and one that cannot.
"Which mining clients have the strongest trade-finance opportunities" is a filter
on sector, a filter on product, and a sort on the model's own selection score --
not a question of judgement. If the model were asked to do the ranking, the
ranking would be unverifiable, and it would silently disagree with the numbers
printed next to it.

Every retrieval records the entity and product IDs it touched so the audit log
can show exactly what the answer was allowed to see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..wallet import assumptions
from . import config
from .router import Route

#: The tables this layer reads. Nothing else is loaded, and no raw dataset is
#: reachable from here at all.
REQUIRED_TABLES = (
    "client_opportunity_intelligence",
    "opportunity_explanations",
    "banker_questions",
    "portfolio_opportunity_intelligence",
    "opportunity_selection_detail",
    "opportunity_sensitivity_summary",
    "model_diagnostics",
)

#: Per-pillar column prefixes on the client profile.
PILLAR_PREFIX = {
    assumptions.CASH: "cash",
    assumptions.FX: "fx",
    assumptions.TRADE: "trade",
    assumptions.LENDING: "lending",
    assumptions.IB: "ib",
}


@dataclass
class Retrieved:
    """Everything retrieval selected, plus the trail of what it touched."""

    route: Route
    clients: pd.DataFrame = field(default_factory=pd.DataFrame)
    pillars: pd.DataFrame = field(default_factory=pd.DataFrame)
    explanations: pd.DataFrame = field(default_factory=pd.DataFrame)
    questions: pd.DataFrame = field(default_factory=pd.DataFrame)
    sensitivity: pd.DataFrame = field(default_factory=pd.DataFrame)
    diagnostics: pd.DataFrame = field(default_factory=pd.DataFrame)
    portfolio: pd.DataFrame = field(default_factory=pd.DataFrame)
    methodology: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def entity_ids(self) -> list[str]:
        ids: list[str] = []
        for frame in (self.clients, self.pillars, self.explanations):
            if not frame.empty and "entity_id" in frame.columns:
                ids.extend(str(value) for value in frame["entity_id"])
        return sorted(set(ids))

    @property
    def products(self) -> list[str]:
        products: list[str] = []
        for frame in (self.pillars, self.explanations):
            if not frame.empty and "product" in frame.columns:
                products.extend(str(value) for value in frame["product"])
        return sorted(set(products))

    @property
    def is_empty(self) -> bool:
        return all(
            frame.empty
            for frame in (
                self.clients,
                self.pillars,
                self.explanations,
                self.portfolio,
            )
        ) and not self.methodology

    def trail(self) -> dict[str, Any]:
        """The audit trail: what was retrieved, not what it said."""
        return {
            "entity_ids": self.entity_ids,
            "products": self.products,
            "row_counts": {
                "clients": int(len(self.clients)),
                "pillars": int(len(self.pillars)),
                "explanations": int(len(self.explanations)),
                "questions": int(len(self.questions)),
                "sensitivity": int(len(self.sensitivity)),
                "diagnostics": int(len(self.diagnostics)),
                "portfolio": int(len(self.portfolio)),
            },
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Methodology notes -- the only prose the retriever holds
# ---------------------------------------------------------------------------
#
# These describe how the model works. They are text rather than data because
# they are explanations of method, not measurements; each is a statement the
# model report already makes, restated at the length a banker needs.

METHODOLOGY_NOTES = {
    "pillars": (
        "Three Share of Wallet pillars (Cash Management, FX, Trade Finance) and two opportunity "
        "signals (Lending, Investment Banking). Only the first three have a defensible "
        "denominator, so only they carry a share."
    ),
    "cash_basis": (
        "Addressable Cash Flow = revenue + cost of sales. Both coefficients are accounting "
        "identities: revenue is collected into a bank account and cost of sales is paid out of "
        "one. It is the client's own operating turnover, never bank income, and no fee figure is "
        "estimated on it because Syn Bank discloses no pricing."
    ),
    "peer_benchmark": (
        "FX and Trade Finance have no disclosed total, so the addressable figure is the client's "
        "own disclosed exposure scaled by the upper-quartile intensity of its peers. The client "
        "is always excluded from the peer population that sets its own coefficient, and a sector "
        "population is used only where at least three peers remain after that exclusion."
    ),
    "lending_basis": (
        "Lending publishes a financing opportunity built from disclosed debt structure: debt "
        "classified current, undrawn committed facilities, the working-capital cycle and capex. "
        "Syn Bank's data contains no loan book, so no share of wallet exists for lending."
    ),
    "ib_basis": (
        "Investment Banking is a ranked mandate-likelihood signal built from five percentile-"
        "ranked balance-sheet facts. No rand amount is estimated because nothing in the data "
        "indicates a planned transaction."
    ),
    "confidence": (
        "Confidence combines four input-quality factors additively, then multiplies by how direct "
        "the method is. An accounting identity scores 1.00, a structural fact 0.90, a peer "
        "benchmark 0.60 and a judgement threshold 0.35. Bands: HIGH at 0.70, MEDIUM at 0.45, LOW "
        "below."
    ),
    "sensitivity": (
        "Every rand estimate is rebuilt under 36 model configurations varying the benchmark "
        "percentile, leave-one-out versus self-inclusive peer populations, sector versus "
        "portfolio scope, and the capex debt-funded share. Cash Management is untouched by all of "
        "them; FX and Trade Finance move by several times."
    ),
    "no_totals": (
        "The five pillars are never added. Two of them overlap on the SWIFT channel by an amount "
        "the supplied data cannot resolve, and the five rand figures are measured on incomparable "
        "bases. There is no portfolio total and none can be constructed."
    ),
    "gap_meaning": (
        "An opportunity is addressable activity NOT OBSERVED in Syn Bank's supplied data. It is "
        "not evidence that another bank holds it, and it is never business Syn Bank has booked."
    ),
}

#: Which notes each intent needs. Sending all eight every time would waste a
#: third of the context budget on text the answer does not use.
METHODOLOGY_BY_INTENT = {
    "client_briefing": ("pillars", "gap_meaning", "no_totals"),
    "opportunity_explanation": ("gap_meaning", "confidence"),
    "portfolio_query": ("no_totals", "gap_meaning"),
    "product_query": ("gap_meaning",),
    "sensitivity_query": ("sensitivity", "peer_benchmark"),
    "meeting_preparation": ("gap_meaning",),
    "methodology_query": tuple(METHODOLOGY_NOTES),
    "executive_summary": ("pillars", "no_totals", "gap_meaning"),
}

#: Extra notes pulled in when a particular pillar is in play.
METHODOLOGY_BY_PRODUCT = {
    assumptions.CASH: ("cash_basis",),
    assumptions.FX: ("peer_benchmark",),
    assumptions.TRADE: ("peer_benchmark",),
    assumptions.LENDING: ("lending_basis",),
    assumptions.IB: ("ib_basis",),
}


class Retriever:
    """Deterministic retrieval over the loaded intelligence tables."""

    def __init__(self, tables: dict[str, pd.DataFrame]) -> None:
        missing = [name for name in REQUIRED_TABLES if name not in tables]
        if missing:
            raise KeyError(f"missing intelligence tables: {missing}")
        self._tables = tables

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self._tables[name]

    # -- helpers -----------------------------------------------------------

    def _pillar_rows(self, entity_ids: list[str] | None = None) -> pd.DataFrame:
        rows = self["opportunity_selection_detail"]
        if entity_ids:
            rows = rows[rows["entity_id"].isin(entity_ids)]
        return rows

    def _apply_filters(self, rows: pd.DataFrame, route: Route) -> pd.DataFrame:
        if route.products:
            rows = rows[rows["product"].isin(route.products)]
        if route.sectors:
            rows = rows[rows["sector"].isin(route.sectors)]
        if route.confidence_filter:
            rows = rows[rows["confidence_band"] == route.confidence_filter]
        return rows

    def _rank(self, rows: pd.DataFrame, route: Route, column: str = "selection_score"):
        ascending = route.order == "asc"
        return rows.sort_values(
            [column, "entity_id"], ascending=[ascending, True], kind="stable"
        )

    def _sensitivity_for(self, keys: list[tuple[str, str]]) -> pd.DataFrame:
        sensitivity = self["opportunity_sensitivity_summary"]
        if not keys:
            return sensitivity.iloc[0:0]
        index = pd.MultiIndex.from_tuples(keys, names=["entity_id", "product"])
        return sensitivity[
            pd.MultiIndex.from_frame(sensitivity[["entity_id", "product"]]).isin(index)
        ]

    def _diagnostics_for(
        self, entity_ids: list[str], products: list[str] | None = None
    ) -> pd.DataFrame:
        findings = self["model_diagnostics"]
        rows = findings[findings["entity_id"].isin(entity_ids)] if entity_ids else findings
        if products:
            rows = rows[rows["product"].isin(products)]
        # HIGH first: a banker reading six diagnostics needs the blocking ones.
        order = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}
        return rows.assign(_order=rows["severity"].map(order)).sort_values(
            ["_order", "entity_id"], kind="stable"
        ).drop(columns="_order").head(config.MAX_DIAGNOSTICS)

    def _methodology(self, route: Route, products: list[str]) -> list[str]:
        keys = list(METHODOLOGY_BY_INTENT.get(route.intent, ("gap_meaning",)))
        for product in products:
            keys.extend(METHODOLOGY_BY_PRODUCT.get(product, ()))
        seen: list[str] = []
        for key in keys:
            if key not in seen:
                seen.append(key)
        return [METHODOLOGY_NOTES[key] for key in seen if key in METHODOLOGY_NOTES]

    # -- per-intent retrieval ----------------------------------------------

    def _retrieve_client(self, route: Route, slots_only: bool) -> Retrieved:
        entity_ids = list(route.entity_ids)
        clients = self["client_opportunity_intelligence"]
        clients = clients[clients["entity_id"].isin(entity_ids)]

        pillars = self._pillar_rows(entity_ids)
        if route.products:
            pillars = pillars[pillars["product"].isin(route.products)]
        elif slots_only:
            # A briefing carries at most three opportunities, so retrieve the
            # three the selection layer already chose rather than all five.
            pillars = pillars[pillars["selection_slot"].notna()]
        pillars = pillars.sort_values(
            ["selection_score", "product"], ascending=[False, True], kind="stable"
        )

        keys = list(zip(pillars["entity_id"], pillars["product"]))
        explanations = self["opportunity_explanations"]
        explanations = explanations[
            pd.MultiIndex.from_frame(explanations[["entity_id", "product"]]).isin(
                pd.MultiIndex.from_tuples(keys) if keys else []
            )
        ]

        questions = self["banker_questions"]
        questions = questions[questions["entity_id"].isin(entity_ids)]
        if route.products:
            questions = questions[questions["product"].isin(route.products)]
        questions = questions.head(config.MAX_QUESTIONS)

        return Retrieved(
            route=route,
            clients=clients,
            pillars=pillars,
            explanations=explanations,
            questions=questions,
            sensitivity=self._sensitivity_for(keys),
            diagnostics=self._diagnostics_for(entity_ids, list(route.products) or None),
            methodology=self._methodology(route, list(pillars["product"].unique())),
        )

    def _retrieve_ranked(self, route: Route) -> Retrieved:
        rows = self._apply_filters(self._pillar_rows(), route)
        notes: list[str] = []

        # A ranking answer is about live opportunities. A pillar where the model
        # could not demonstrate headroom is not a smaller opportunity, it is a
        # different statement, and mixing the two would mislead.
        with_headroom = rows[rows["opportunity_status"] != "NO_HEADROOM_DEMONSTRATED"]
        if not with_headroom.empty:
            rows = with_headroom
        elif not rows.empty:
            notes.append(
                "No pillar matching this question demonstrated headroom; the rows below are "
                "included so the question can still be answered honestly."
            )

        if rows.empty:
            notes.append("No rows matched the filters in this question.")
            return Retrieved(route=route, notes=notes, methodology=self._methodology(route, []))

        ranked = self._rank(rows, route).head(min(route.limit, config.MAX_PRODUCT_ROWS))
        keys = list(zip(ranked["entity_id"], ranked["product"]))
        entity_ids = sorted(set(ranked["entity_id"]))[: config.MAX_CLIENTS_IN_CONTEXT]

        explanations = self["opportunity_explanations"]
        explanations = explanations[
            pd.MultiIndex.from_frame(explanations[["entity_id", "product"]]).isin(
                pd.MultiIndex.from_tuples(keys)
            )
        ]

        clients = self["client_opportunity_intelligence"]
        clients = clients[clients["entity_id"].isin(entity_ids)]

        return Retrieved(
            route=route,
            clients=clients,
            pillars=ranked,
            explanations=explanations,
            sensitivity=self._sensitivity_for(keys),
            diagnostics=self._diagnostics_for(entity_ids, list(ranked["product"].unique())),
            methodology=self._methodology(route, list(ranked["product"].unique())),
            notes=notes,
        )

    def _retrieve_sensitivity(self, route: Route) -> Retrieved:
        entity_ids = list(route.entity_ids)
        products = list(route.products)
        sensitivity = self["opportunity_sensitivity_summary"]
        rows = self._pillar_rows(entity_ids or None)
        if products:
            rows = rows[rows["product"].isin(products)]
        if route.sectors:
            rows = rows[rows["sector"].isin(route.sectors)]

        if not entity_ids and not products:
            # "How reliable are these numbers?" with nothing named: answer for
            # the pillars whose estimates actually move.
            rows = rows[rows["sensitivity_flag"].isin(["SENSITIVE", "MODERATE"])]
            rows = self._rank(rows, route).head(config.MAX_PRODUCT_ROWS)
        elif not entity_ids:
            rows = self._rank(rows, route).head(config.MAX_PRODUCT_ROWS)

        keys = list(zip(rows["entity_id"], rows["product"]))
        selected = self._sensitivity_for(keys)

        explanations = self["opportunity_explanations"]
        explanations = explanations[
            pd.MultiIndex.from_frame(explanations[["entity_id", "product"]]).isin(
                pd.MultiIndex.from_tuples(keys) if keys else []
            )
        ]

        clients = self["client_opportunity_intelligence"]
        clients = clients[clients["entity_id"].isin(sorted(set(rows["entity_id"])))]

        del sensitivity
        return Retrieved(
            route=route,
            clients=clients.head(config.MAX_CLIENTS_IN_CONTEXT),
            pillars=rows,
            explanations=explanations,
            sensitivity=selected,
            methodology=self._methodology(route, list(rows["product"].unique())),
        )

    def _retrieve_portfolio_sections(
        self, route: Route, sections: tuple[str, ...]
    ) -> pd.DataFrame:
        portfolio = self["portfolio_opportunity_intelligence"]
        rows = portfolio[portfolio["section"].isin(sections)]
        if route.products:
            rows = rows[rows["product"].isin(route.products) | rows["product"].isna()]
        return rows.head(config.MAX_PORTFOLIO_ROWS)

    def _retrieve_executive(self, route: Route) -> Retrieved:
        ranked = self._retrieve_ranked(
            Route(
                intent=route.intent,
                question=route.question,
                products=route.products,
                sectors=route.sectors,
                order=route.order,
                limit=max(route.limit, 5),
            )
        )
        ranked.portfolio = self._retrieve_portfolio_sections(
            route, ("portfolio_position", "primary_concentration")
        )
        ranked.methodology = self._methodology(route, ranked.products)
        return ranked

    def _retrieve_methodology(self, route: Route) -> Retrieved:
        return Retrieved(
            route=route,
            methodology=self._methodology(route, list(route.products)),
            portfolio=self._retrieve_portfolio_sections(route, ("product_metrics",)),
        )

    # -- entry point -------------------------------------------------------

    def retrieve(self, route: Route) -> Retrieved:
        """Select exactly the rows this question needs."""
        from . import router as router_module

        if route.off_topic:
            return Retrieved(
                route=route,
                notes=[
                    "That question is not about this portfolio. The copilot only answers "
                    "questions about 20 JSE-listed clients across five product pillars: "
                    "cash management, FX, trade finance, lending and investment banking."
                ],
            )

        if route.unresolved_client:
            return Retrieved(
                route=route,
                notes=[
                    f"No client named '{route.unresolved_client}' exists in this portfolio. The "
                    "portfolio covers 20 JSE-listed clients; nothing outside it can be answered."
                ],
            )

        if route.intent == router_module.METHODOLOGY_QUERY:
            return self._retrieve_methodology(route)
        if route.intent == router_module.EXECUTIVE_SUMMARY:
            return self._retrieve_executive(route)
        if route.intent == router_module.SENSITIVITY_QUERY:
            return self._retrieve_sensitivity(route)
        if route.entity_ids:
            slots_only = route.intent in (
                router_module.CLIENT_BRIEFING,
                router_module.MEETING_PREPARATION,
            )
            return self._retrieve_client(route, slots_only=slots_only)
        return self._retrieve_ranked(route)


def load_tables(processed_dir) -> dict[str, pd.DataFrame]:
    """Read the intelligence tables from Parquet. The only I/O in this layer."""
    import duckdb

    connection = duckdb.connect(":memory:")
    try:
        tables = {}
        for name in REQUIRED_TABLES:
            path = processed_dir / f"{name}.parquet"
            if not path.is_file():
                raise FileNotFoundError(
                    f"Missing {path}. Build stages 3 and 4 first: "
                    "`python -m src.syn_wallet.build_wallet --overwrite --sensitivity` then "
                    "`python -m src.syn_wallet.build_intelligence --overwrite`."
                )
            tables[name] = connection.execute(
                f"SELECT * FROM read_parquet('{path}')"
            ).df()
        return tables
    finally:
        connection.close()


def client_roster(tables: dict[str, pd.DataFrame]) -> dict[str, str]:
    """``entity_id -> entity_name`` for the router's vocabulary."""
    clients = tables["client_opportunity_intelligence"]
    return dict(zip(clients["entity_id"].astype(str), clients["entity_name"].astype(str)))


def _numeric(value: Any) -> float:
    return float(value) if value is not None and pd.notna(value) else np.nan
