"""Stage 5 -- the Syn Bank Client Opportunity Copilot.

A generative layer over the deterministic model, built so the model can only
write, never calculate::

    analytical contract -> commercial intelligence -> router -> retrieval
      -> context -> LLM -> validation -> banker-facing answer

No raw dataset is reachable from here. No figure is computed here. The copilot
works with no API key at all, answering deterministically and saying so.
"""

from __future__ import annotations

from . import (
    audit,
    config,
    context,
    engine,
    fallback,
    llm,
    prompts,
    retrieval,
    router,
    validation,
)
from .engine import Answer, Copilot

__all__ = [
    "Answer",
    "Copilot",
    "audit",
    "config",
    "context",
    "engine",
    "fallback",
    "llm",
    "prompts",
    "retrieval",
    "router",
    "validation",
]
