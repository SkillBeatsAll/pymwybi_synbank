"""Stage 4 -- the deterministic commercial intelligence layer.

Converts the analytical contract (``opportunity_engine.parquet`` and
``client_opportunity_profile.parquet``) into banker-oriented output: a primary
opportunity per client, a six-part explanation for every pillar, questions a
relationship manager can put to the client, and portfolio-level intelligence.

No model methodology lives here. No number is invented here. No LLM is called
here -- every sentence is a template filled from a published field, so the same
inputs always produce the same words.
"""

from __future__ import annotations

from . import config, engine, explanations, portfolio, profiles, questions, selection
from . import sensitivity_view

__all__ = [
    "config",
    "engine",
    "explanations",
    "portfolio",
    "profiles",
    "questions",
    "selection",
    "sensitivity_view",
]
