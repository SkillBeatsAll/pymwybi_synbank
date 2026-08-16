"""The Client Opportunity Copilot: question in, banker-ready answer out.

The pipeline, in order, with the model reached only at step five::

    question
      -> router      classify intent, resolve client / product / sector
      -> retrieval   deterministic filter and rank over the intelligence tables
      -> context     render the selected rows, enumerate every figure in them
      -> LLM         write prose over that context, and nothing else
      -> validation  reject any answer containing an unsupported figure or claim
      -> audit       record the whole chain
      -> answer

Every step before the model is deterministic, and every step after it is a
check. If the model is unavailable, or its answer fails validation, step five is
replaced by :mod:`.fallback` and the banker is told which happened.

The model never sees a raw dataset, never performs arithmetic, and never decides
a ranking. It is the last mile of presentation over numbers that were already
correct.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .. import config as paths
from . import config, fallback, prompts, validation
from .audit import AuditLog, AuditRecord
from .context import ContextBuilder, ContextBundle
from .demos import DemoLibrary, context_digest
from .llm import LLMTruncated, LLMUnavailable, NimClient
from .retrieval import Retrieved, Retriever, client_roster, load_tables
from .router import Route, Router


@dataclass
class Answer:
    """What the banker gets, and everything behind it."""

    question: str
    text: str
    mode: str
    intent: str
    entity_ids: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    notice: str = ""
    context: str = ""
    context_summary: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    record_id: str = ""
    latency_seconds: float | None = None
    error: str | None = None

    @property
    def used_llm(self) -> bool:
        return self.mode == config.LLM

    def rendered(self) -> str:
        """The answer as it should be displayed, notice included."""
        return f"{self.notice}\n\n{self.text}".strip() if self.notice else self.text

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.text,
            "mode": self.mode,
            "notice": self.notice,
            "intent": self.intent,
            "entity_ids": list(self.entity_ids),
            "products": list(self.products),
            "context_summary": self.context_summary,
            "validation": self.validation,
            "settings": self.settings,
            "record_id": self.record_id,
            "latency_seconds": self.latency_seconds,
            "error": self.error,
        }


class Copilot:
    """The assembled copilot. Construct once, ask many times."""

    def __init__(
        self,
        tables: dict[str, pd.DataFrame],
        llm: NimClient | None = None,
        audit_log: AuditLog | None = None,
        context_builder: ContextBuilder | None = None,
        demos: DemoLibrary | None = None,
    ) -> None:
        self.tables = tables
        self.router = Router(client_roster(tables))
        self.retriever = Retriever(tables)
        self.context_builder = context_builder or ContextBuilder()
        self.llm = llm if llm is not None else NimClient()
        self.audit = audit_log or AuditLog()
        self.demos = demos or DemoLibrary()

    # -- construction ------------------------------------------------------

    @classmethod
    def from_processed(
        cls,
        processed_dir: Path | None = None,
        audit_path: Path | None = None,
        **kwargs: Any,
    ) -> "Copilot":
        """Build from ``data/processed``, the normal entry point."""
        processed_dir = processed_dir or paths.PROCESSED_DIR
        log = AuditLog(audit_path) if audit_path is not None else AuditLog()
        kwargs.setdefault("demos", DemoLibrary.from_processed(processed_dir))
        return cls(load_tables(processed_dir), audit_log=log, **kwargs)

    @property
    def clients(self) -> dict[str, str]:
        return dict(self.router._clients)  # noqa: SLF001 - the roster is public data

    def llm_status(self) -> dict[str, Any]:
        """Whether generated answers are available, and why not if they are not."""
        reason = self.llm.unavailable_reason()
        return {
            "available": reason is None,
            "reason": reason,
            "provider": config.provider_name(),
            "model": config.model_name(),
            "base_url": config.base_url(),
            "prompt_version": config.PROMPT_VERSION,
        }

    # -- the pipeline ------------------------------------------------------

    def plan(self, question: str) -> tuple[Route, Retrieved, ContextBundle]:
        """Everything up to but not including generation.

        Exposed separately because it is the whole deterministic half of the
        system, and a test -- or a reviewer -- should be able to inspect exactly
        what the model would have been shown without spending a token.
        """
        route = self.router.route(question)
        retrieved = self.retriever.retrieve(route)
        bundle = self.context_builder.build(retrieved)
        return route, retrieved, bundle

    def ask(self, question: str, allow_llm: bool = True) -> Answer:
        """Answer one question, falling back deterministically where needed."""
        started = time.monotonic()
        if not question or not question.strip():
            raise ValueError("question must not be empty")

        route, retrieved, bundle = self.plan(question)
        deterministic = fallback.render(retrieved)

        record = AuditRecord(
            query=question.strip(),
            intent=route.intent,
            entity_ids=bundle.entity_ids,
            products=bundle.products,
            route=route.as_dict(),
            retrieval=retrieved.trail(),
            context=bundle.text,
            context_summary=bundle.as_dict(),
            settings=config.generation_settings().as_dict(),
        )

        # Nothing was retrieved: there is no context to write prose over, and a
        # generated answer could only fill the gap with invention.
        if retrieved.is_empty:
            return self._finish(
                record, deterministic, config.FALLBACK_NO_KEY, started, question, route, bundle,
                notice_override=(
                    "**No matching data** — this question could not be resolved against the "
                    "portfolio."
                ),
            )

        if not allow_llm or not self.llm.available():
            reason = self.llm.unavailable_reason() or "generation disabled for this call"
            record.error = reason
            # A stored demo answer beats a templated one when the question is a
            # question we prepared for, which is what keeps an offline demo
            # looking like the product rather than like a degraded mode.
            stored = self.demos.lookup(question)
            if stored is not None:
                notice = config.MODE_NOTICE[config.DEMO]
                if stored.context_digest and stored.context_digest != context_digest(
                    bundle.text
                ):
                    notice += config.STALE_DEMO_NOTICE
                    record.error = "stored demo predates the current analytical outputs"
                return self._finish(
                    record,
                    stored.answer,
                    config.DEMO,
                    started,
                    question,
                    route,
                    bundle,
                    notice_override=notice,
                )
            return self._finish(
                record, deterministic, config.FALLBACK_NO_KEY, started, question, route, bundle
            )

        messages = prompts.build_messages(question, route.intent, bundle.text)
        try:
            completion = self.llm.complete(messages)
        except LLMTruncated as error:
            # The service answered. Saying it "could not be reached" would be a
            # false statement about the system's own health, in front of the one
            # audience most likely to ask why.
            record.error = str(error)
            return self._finish(
                record, deterministic, config.FALLBACK_TRUNCATED, started, question, route, bundle
            )
        except LLMUnavailable as error:
            record.error = str(error)
            return self._finish(
                record, deterministic, config.FALLBACK_ERROR, started, question, route, bundle
            )

        verdict = validation.validate(completion.text, bundle.figures)
        record.validation = verdict.as_dict()
        record.settings = {**record.settings, **completion.as_dict()}

        if not verdict.ok:
            record.error = f"validation failed — {verdict.summary()}"
            return self._finish(
                record,
                deterministic,
                config.FALLBACK_VALIDATION,
                started,
                question,
                route,
                bundle,
                rejected=completion.text,
            )

        return self._finish(
            record, completion.text, config.LLM, started, question, route, bundle
        )

    def _finish(
        self,
        record: AuditRecord,
        text: str,
        mode: str,
        started: float,
        question: str,
        route: Route,
        bundle: ContextBundle,
        notice_override: str | None = None,
        rejected: str | None = None,
    ) -> Answer:
        record.answer = text
        record.mode = mode
        record.latency_seconds = round(time.monotonic() - started, 3)
        if rejected is not None:
            record.validation = {**record.validation, "rejected_answer": rejected}
        self.audit.write(record)

        notice = notice_override if notice_override is not None else config.MODE_NOTICE[mode]
        return Answer(
            question=question.strip(),
            text=text,
            mode=mode,
            intent=route.intent,
            entity_ids=bundle.entity_ids,
            products=bundle.products,
            notice=notice,
            context=bundle.text,
            context_summary=bundle.as_dict(),
            validation=record.validation,
            settings=record.settings,
            record_id=record.record_id,
            latency_seconds=record.latency_seconds,
            error=record.error,
        )

    # -- convenience -------------------------------------------------------

    def brief(self, entity: str, allow_llm: bool = True) -> Answer:
        """A full briefing for one client, by id or by name."""
        entity_id = entity if entity in self.clients else None
        if entity_id is None:
            found, _ = self.router.find_clients(entity)
            entity_id = found[0] if found else None
        if entity_id is None:
            raise KeyError(
                f"unknown client {entity!r}. Known clients: "
                + ", ".join(f"{key} {name}" for key, name in sorted(self.clients.items()))
            )
        # Phrased exactly as the stored demo questions are, so that briefing a
        # client by id offline still finds its prepared answer. Appending the
        # entity id here would produce a different lookup key and quietly drop
        # the polished demo in favour of the templated fallback.
        return self.ask(
            f"Prepare a briefing for {self.clients[entity_id]}.", allow_llm=allow_llm
        )
