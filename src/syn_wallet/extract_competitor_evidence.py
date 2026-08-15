"""Extract structured competitor-lender evidence from AFS borrowings-note text.

Turns the free-text `lenders_named` field in
``data/finances/external_financials_normalized.csv`` into a structured, traceable table:
one row per bank actually named in a client's own AFS, with a source excerpt and a
``is_confirmed_lender`` flag distinguishing "named as this client's lender" from "named in
some other capacity" (JSE sponsor, profit-share partner, etc.) or "not individually named".

This is what lets the wallet model label a gap "confirmed competitor-held" instead of just
assuming it — see CLAUDE.md hard rule: never treat a missing Syn Bank product as proof a
competitor holds it unless a lender is actually named.

The extraction prompt is documented in full, with its governing worked example, in
``prompts/competitor_evidence_prompt.md`` — this module reads the system prompt directly out
of that file so the two never drift apart.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPT_PATH = REPO_ROOT / "prompts" / "competitor_evidence_prompt.md"
MODEL = "claude-sonnet-5"

OUTPUT_COLUMNS = (
    "entity_id", "entity_name", "bank_name", "is_confirmed_lender",
    "facility_amount_zar", "utilised_amount_zar", "source_excerpt",
)

NO_EVIDENCE_TEXT = {"not disclosed", ""}


def load_system_prompt(prompt_path: Path = PROMPT_PATH) -> str:
    text = prompt_path.read_text(encoding="utf-8")
    match = re.search(r"## System prompt\s*\n```\n(.*?)\n```", text, re.DOTALL)
    if not match:
        raise ValueError(f"Could not find a '## System prompt' code block in {prompt_path}")
    return match.group(1).strip()


def _user_message(entity_name: str, entity_id: str, lenders_named_text: str) -> str:
    return (
        f"Client: {entity_name} ({entity_id})\n\n"
        f"Borrowings-note text:\n\"\"\"\n{lenders_named_text}\n\"\"\"\n\n"
        "Extract the JSON array per the system rules. Return [] if there is no usable bank-name "
        "evidence in the text above."
    )


def _parse_json_array(raw_text: str) -> list[dict]:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError(f"Expected a JSON array, got {type(parsed).__name__}")
    return parsed


def load_candidates(finances_dir: Path) -> pd.DataFrame:
    normalized = pd.read_csv(finances_dir / "external_financials_normalized.csv")
    rows = normalized[normalized["field"] == "lenders_named"].copy()
    rows["value_text"] = rows["value_text"].fillna("")
    rows["has_evidence"] = ~rows["value_text"].str.strip().str.lower().isin(NO_EVIDENCE_TEXT)
    return rows[["entity_id", "entity_name", "value_text", "has_evidence"]]


def extract_all(finances_dir: Path, client, system_prompt: str, model: str = MODEL) -> pd.DataFrame:
    candidates = load_candidates(finances_dir)
    records: list[dict] = []
    for _, row in candidates[candidates["has_evidence"]].iterrows():
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": _user_message(row["entity_name"], row["entity_id"], row["value_text"])}],
        )
        extracted = _parse_json_array(response.content[0].text)
        for item in extracted:
            records.append(
                {
                    "entity_id": row["entity_id"],
                    "entity_name": row["entity_name"],
                    "bank_name": item.get("bank_name"),
                    "is_confirmed_lender": bool(item.get("is_confirmed_lender", False)),
                    "facility_amount_zar": item.get("facility_amount_zar"),
                    "utilised_amount_zar": item.get("utilised_amount_zar"),
                    "source_excerpt": item.get("source_excerpt"),
                }
            )
    return pd.DataFrame(records, columns=list(OUTPUT_COLUMNS))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finances-dir", type=Path, default=REPO_ROOT / "data" / "finances")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "data" / "processed" / "competitor_evidence.csv")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()

    try:
        import anthropic
    except ImportError as exc:
        raise SystemExit("The 'anthropic' package is required: pip install anthropic") from exc

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    system_prompt = load_system_prompt()
    result = extract_all(args.finances_dir, client, system_prompt, model=args.model)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)
    confirmed = int(result["is_confirmed_lender"].sum()) if len(result) else 0
    print(f"competitor_evidence.csv: {len(result)} bank mentions across "
          f"{result['entity_id'].nunique() if len(result) else 0} clients, {confirmed} confirmed lenders")


if __name__ == "__main__":
    main()
