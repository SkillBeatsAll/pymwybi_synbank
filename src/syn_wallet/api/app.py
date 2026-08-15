"""The dashboard's HTTP layer: JSON in, JSON out, plus the static front end.

Thin by design. Every endpoint is a projection of a published table, computed in
:mod:`.service`, and the front end holds no financial logic at all -- it renders
what it is given. That separation is what stops the dashboard and the model
disagreeing about a number.

Run it with::

    python -m src.syn_wallet.serve

The copilot is reached through ``POST /api/copilot/ask``. With no API key
configured it answers deterministically and says so in ``mode``, so the whole
dashboard works offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import config as paths
from ..copilot import config as copilot_config
from ..copilot.engine import Copilot
from ..wallet import assumptions
from . import service

#: Where the static front end lives.
DASHBOARD_DIR = paths.REPOSITORY_ROOT / "dashboard"

#: Questions offered in the copilot panel. Each exercises a different route.
EXAMPLE_QUESTIONS = (
    "Which clients have the largest high-confidence opportunities?",
    "Which mining clients have the strongest trade-finance opportunities?",
    "Summarize the top five opportunities in the portfolio.",
    "How reliable is the Vodacom Group FX opportunity?",
)

#: Offered on a client page, filled with that client's name.
CLIENT_QUESTIONS = (
    "Why is {name} a priority?",
    "Prepare a briefing for {name}.",
    "What should the banker ask {name} about?",
    "How reliable is the {name} FX opportunity?",
)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    allow_llm: bool = True


def create_app(processed_dir: Path | None = None) -> FastAPI:
    """Build the application. Tables and the copilot load once, at startup."""
    app = FastAPI(
        title="Syn Bank Coverage Desk",
        description="Share of wallet intelligence for Corporate & Investment Banking.",
        version=copilot_config.COPILOT_VERSION,
    )

    store = service.load(processed_dir)
    copilot = Copilot(
        tables={
            name: store[name]
            for name in (
                "client_opportunity_intelligence",
                "opportunity_explanations",
                "banker_questions",
                "portfolio_opportunity_intelligence",
                "opportunity_selection_detail",
                "opportunity_sensitivity_summary",
                "model_diagnostics",
            )
        },
        demos=__import__(
            "src.syn_wallet.copilot.demos", fromlist=["DemoLibrary"]
        ).DemoLibrary.from_processed(processed_dir or paths.PROCESSED_DIR),
    )

    # -- meta -------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        status = copilot.llm_status()
        return {
            "ok": True,
            "clients": int(len(store["client_opportunity_intelligence"])),
            "methodology_version": str(
                store["opportunity_engine"]["methodology_version"].iloc[0]
            ),
            "intelligence_version": str(
                store["client_opportunity_intelligence"]["intelligence_version"].iloc[0]
            ),
            "copilot_version": copilot_config.COPILOT_VERSION,
            "ai": {
                "available": status["available"],
                "reason": status["reason"],
                "provider": status["provider"],
                "model": status["model"],
                "demo_answers": len(copilot.demos),
            },
        }

    # -- pages ------------------------------------------------------------

    @app.get("/api/portfolio")
    def portfolio() -> dict[str, Any]:
        return service.portfolio_payload(store)

    @app.get("/api/heatmap")
    def heatmap() -> dict[str, Any]:
        return service.heatmap_payload(store)

    @app.get("/api/clients")
    def clients() -> list[dict[str, Any]]:
        return service.client_index(store)

    @app.get("/api/clients/{entity_id}")
    def client(entity_id: str) -> dict[str, Any]:
        try:
            return service.client_payload(entity_id.upper(), store)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail=f"No client {entity_id!r} in this portfolio."
            ) from error

    @app.get("/api/sensitivity")
    def sensitivity() -> dict[str, Any]:
        return service.sensitivity_payload(store)

    @app.get("/api/products")
    def products() -> list[dict[str, Any]]:
        return [
            {"product": product, **{key: meta[key] for key in ("key", "label", "role")}}
            for product, meta in service.PILLAR_META.items()
        ]

    @app.get("/api/products/{product}")
    def product(product: str) -> dict[str, Any]:
        resolved = product
        if product not in service.PILLAR_META:
            matches = [
                name
                for name, meta in service.PILLAR_META.items()
                if meta["key"] == product
            ]
            if not matches:
                raise HTTPException(
                    status_code=404,
                    detail=f"Unknown product {product!r}. Try one of: "
                    + ", ".join(assumptions.PRODUCTS),
                )
            resolved = matches[0]
        return service.product_payload(resolved, store)

    # -- copilot ----------------------------------------------------------

    @app.get("/api/copilot/examples")
    def copilot_examples(client: str | None = Query(default=None)) -> dict[str, Any]:
        if client:
            rows = store["client_opportunity_intelligence"]
            match = rows[rows["entity_id"] == client.upper()]
            if not match.empty:
                name = str(match.iloc[0]["entity_name"])
                return {
                    "questions": [text.format(name=name) for text in CLIENT_QUESTIONS]
                }
        return {"questions": list(EXAMPLE_QUESTIONS)}

    @app.post("/api/copilot/ask")
    def copilot_ask(request: AskRequest) -> dict[str, Any]:
        try:
            answer = copilot.ask(request.question, allow_llm=request.allow_llm)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "question": answer.question,
            "answer": answer.text,
            "notice": answer.notice,
            "mode": answer.mode,
            "used_llm": answer.used_llm,
            "intent": answer.intent,
            "entity_ids": answer.entity_ids,
            "products": answer.products,
            "validation": answer.validation,
            "latency_seconds": answer.latency_seconds,
        }

    # -- static front end -------------------------------------------------

    if DASHBOARD_DIR.is_dir():
        app.mount(
            "/assets", StaticFiles(directory=DASHBOARD_DIR / "assets"), name="assets"
        )

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(DASHBOARD_DIR / "index.html")

        @app.exception_handler(404)
        async def spa_fallback(request, exc):  # noqa: ANN001, ANN201
            """Client-side routes are served the shell; the API keeps its 404s."""
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": exc.detail}, status_code=404)
            return FileResponse(DASHBOARD_DIR / "index.html")

    return app


app = create_app() if (paths.PROCESSED_DIR / "opportunity_engine.parquet").is_file() else None
