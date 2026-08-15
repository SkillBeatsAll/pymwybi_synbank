"""Grounded client briefing-note generator (Layer 5, item 3).

Builds a strictly whitelisted JSON context from already-computed wallet tables and calls
the LLM to draft a 5-6 sentence call-prep note. The model is never given anything beyond
that JSON block, so it structurally cannot hallucinate a fact the computed tables don't
support — see ``prompts/briefing_note_prompt.md`` for the full design rationale, which this
module's system prompt is read from directly (single source of truth).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPT_PATH = REPO_ROOT / "prompts" / "briefing_note_prompt.md"
MODEL = "claude-sonnet-5"

PILLAR_FIELDS = ("addressable_p50", "observed", "share_p50", "unaddressed_p50", "confidence", "opportunity_score", "rank")


def load_system_prompt(prompt_path: Path = PROMPT_PATH) -> str:
    text = prompt_path.read_text(encoding="utf-8")
    match = re.search(r"## System prompt\s*\n```\n(.*?)\n```", text, re.DOTALL)
    if not match:
        raise ValueError(f"Could not find a '## System prompt' code block in {prompt_path}")
    return match.group(1).strip()


def _clean(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, float):
        return round(value, 2)
    return value


def build_grounding_context(
    entity_id: str,
    wallet_results: pd.DataFrame,
    entity_name: str,
    sector: str,
    competitor_evidence: pd.DataFrame | None = None,
) -> dict:
    """Assemble the ONLY facts the model is allowed to use for this client's note.

    Every field here is deliberately whitelisted - nothing from the wider internal
    feature tables (raw transaction detail, unconfirmed bank mentions, etc.) reaches
    this dict, by construction rather than by prompt instruction alone.
    """
    pillars: dict[str, dict] = {}
    client_rows = wallet_results[wallet_results["entity_id"] == entity_id]
    for _, row in client_rows.iterrows():
        pillars[row["pillar"]] = {field: _clean(row.get(field)) for field in PILLAR_FIELDS}

    confirmed_lenders: list[str] = []
    if competitor_evidence is not None and len(competitor_evidence):
        mask = (competitor_evidence["entity_id"] == entity_id) & (competitor_evidence["is_confirmed_lender"] == True)  # noqa: E712
        confirmed_lenders = sorted(competitor_evidence.loc[mask, "bank_name"].dropna().unique().tolist())

    return {
        "entity_id": entity_id,
        "entity_name": entity_name,
        "sector": sector,
        "pillars": pillars,
        "confirmed_competitor_lenders": confirmed_lenders,
    }


def render_user_message(context: dict) -> str:
    context_json = json.dumps(context, indent=2, default=str)
    return f"Client context (the ONLY source of fact for this note):\n\n{context_json}\n\nWrite the briefing note now."


def generate_briefing_note(context: dict, client, model: str = MODEL) -> str:
    system_prompt = load_system_prompt()
    response = client.messages.create(
        model=model,
        max_tokens=512,
        temperature=0,
        system=system_prompt,
        messages=[{"role": "user", "content": render_user_message(context)}],
    )
    return response.content[0].text.strip()
