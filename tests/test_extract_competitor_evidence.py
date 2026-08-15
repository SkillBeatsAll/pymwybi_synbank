import json
from pathlib import Path
from types import SimpleNamespace

from src.syn_wallet.extract_competitor_evidence import (
    OUTPUT_COLUMNS,
    extract_all,
    load_candidates,
    load_system_prompt,
)

FINANCES_DIR = Path(__file__).resolve().parents[1] / "data" / "finances"


class FakeMessages:
    def __init__(self, responses_by_entity: dict[str, list[dict]]) -> None:
        self.responses_by_entity = responses_by_entity
        self.calls: list[dict] = []

    def create(self, *, model, max_tokens, temperature, system, messages):
        self.calls.append({"model": model, "system": system, "messages": messages})
        entity_id = messages[0]["content"].split("(")[1].split(")")[0]
        payload = self.responses_by_entity.get(entity_id, [])
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])


class FakeClient:
    def __init__(self, responses_by_entity: dict[str, list[dict]]) -> None:
        self.messages = FakeMessages(responses_by_entity)


def test_load_system_prompt_matches_documented_rules() -> None:
    prompt = load_system_prompt()
    assert "Only extract a bank as a CONFIRMED lender" in prompt
    assert "Output strict JSON only" in prompt


def test_not_disclosed_entities_never_reach_the_model() -> None:
    candidates = load_candidates(FINANCES_DIR)
    no_evidence_ids = set(candidates[~candidates["has_evidence"]]["entity_id"])
    assert "E01" in no_evidence_ids  # BHP - "Not disclosed"

    client = FakeClient(responses_by_entity={})
    result = extract_all(FINANCES_DIR, client, system_prompt="irrelevant for this test")

    called_entity_ids = {call["messages"][0]["content"].split("(")[1].split(")")[0] for call in client.messages.calls}
    assert called_entity_ids.isdisjoint(no_evidence_ids)
    assert result.empty or "E01" not in set(result["entity_id"])


def test_extraction_preserves_the_not_a_lender_distinction() -> None:
    # Mirrors the documented E07 worked example: a bank named in a non-lending capacity
    # must never be marked is_confirmed_lender = True.
    responses = {
        "E07": [
            {
                "bank_name": "Rand Merchant Bank",
                "is_confirmed_lender": False,
                "facility_amount_zar": None,
                "utilised_amount_zar": None,
                "source_excerpt": "listed ... as the company's JSE sponsor, not as an RCF lender.",
            }
        ],
        "E08": [
            {
                "bank_name": "Standard Bank",
                "is_confirmed_lender": True,
                "facility_amount_zar": None,
                "utilised_amount_zar": None,
                "source_excerpt": "Standard Bank",
            }
        ],
    }
    client = FakeClient(responses)
    result = extract_all(FINANCES_DIR, client, system_prompt="irrelevant for this test")

    assert list(result.columns) == list(OUTPUT_COLUMNS)
    rmb_row = result[(result["entity_id"] == "E07") & (result["bank_name"] == "Rand Merchant Bank")].iloc[0]
    assert bool(rmb_row["is_confirmed_lender"]) is False

    sbsa_row = result[(result["entity_id"] == "E08") & (result["bank_name"] == "Standard Bank")].iloc[0]
    assert bool(sbsa_row["is_confirmed_lender"]) is True
