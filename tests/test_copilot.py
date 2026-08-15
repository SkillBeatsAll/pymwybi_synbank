"""The Client Opportunity Copilot: routing, retrieval, guards and fallback.

Every test here runs offline. The language model is replaced by a fake that
returns whatever a test tells it to, which is the only way to assert what happens
when a model misbehaves -- you cannot ask a real one to hallucinate on cue.

The tests that matter most are the ones that check what happens when the model
gets it *wrong*: an invented figure, a cross-pillar total, a competitor claim, a
share of wallet attached to lending. Each of those must be caught, discarded, and
recorded, not merely discouraged by the prompt.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.syn_wallet.copilot import config as copilot_config
from src.syn_wallet.copilot import (
    context as context_module,
)
from src.syn_wallet.copilot import demos as demos_module
from src.syn_wallet.copilot import fallback, prompts, router, validation
from src.syn_wallet.copilot.audit import AuditLog, AuditRecord
from src.syn_wallet.copilot.engine import Copilot
from src.syn_wallet.copilot.llm import Completion, LLMUnavailable
from src.syn_wallet.copilot.retrieval import load_tables
from src.syn_wallet.wallet import assumptions

from .conftest import requires_full_data

EXPECTED_CLIENTS = 20


# ---------------------------------------------------------------------------
# A fake model
# ---------------------------------------------------------------------------


class FakeLLM:
    """Stands in for NIM. Returns a scripted answer, or raises."""

    def __init__(self, reply: str = "", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[list[dict[str, str]]] = []

    def available(self) -> bool:
        return self.error is None or not isinstance(self.error, LLMUnavailable) or True

    def unavailable_reason(self) -> str | None:
        return None

    def complete(self, messages: list[dict[str, str]]) -> Completion:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return Completion(text=self.reply, model="fake-model", latency_seconds=0.01)


class UnavailableLLM:
    """Stands in for a missing key."""

    def available(self) -> bool:
        return False

    def unavailable_reason(self) -> str | None:
        return "no API key: set NVIDIA_API_KEY in the environment"

    def complete(self, messages):  # pragma: no cover - never reached
        raise AssertionError("complete() must not be called when unavailable")


@pytest.fixture(scope="module")
def tables() -> dict:
    from src.syn_wallet import config as paths

    return load_tables(paths.PROCESSED_DIR)


@pytest.fixture
def offline(tables, tmp_path: Path) -> Copilot:
    """A copilot with no model available, writing its audit to a temp file."""
    return Copilot(tables, llm=UnavailableLLM(), audit_log=AuditLog(tmp_path / "audit.jsonl"))


def _with_reply(tables, tmp_path: Path, reply: str) -> Copilot:
    return Copilot(tables, llm=FakeLLM(reply), audit_log=AuditLog(tmp_path / "audit.jsonl"))


pytestmark = requires_full_data


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

#: One labelled example per supported use case, in the brief's own wording.
ROUTING_CASES = (
    ("Prepare a briefing for Shoprite.", router.CLIENT_BRIEFING),
    ("Why is this client flagged for an FX opportunity? Vodacom", router.OPPORTUNITY_EXPLANATION),
    ("Which clients have the largest high-confidence opportunities?", router.PORTFOLIO_QUERY),
    (
        "Which mining clients have the strongest trade-finance opportunities?",
        router.PRODUCT_QUERY,
    ),
    ("How reliable is this FX opportunity for Vodacom?", router.SENSITIVITY_QUERY),
    ("What should the banker ask MTN about?", router.MEETING_PREPARATION),
    ("Summarize the top five opportunities in the portfolio.", router.EXECUTIVE_SUMMARY),
    ("How does the model calculate addressable cash flow?", router.METHODOLOGY_QUERY),
)


@pytest.mark.parametrize("question,expected", ROUTING_CASES)
def test_every_supported_use_case_routes_correctly(offline: Copilot, question, expected) -> None:
    assert offline.router.route(question).intent == expected


def test_client_names_resolve_however_they_are_typed(offline: Copilot) -> None:
    for text, expected in (
        ("Shoprite", "E09"),
        ("shoprite holdings", "E09"),
        ("E09", "E09"),
        ("MTN", "E16"),
        ("mtn group", "E16"),
        ("The Bidvest Group", "E18"),
        ("Bidvest", "E18"),
        ("Shaftesbury Capital plc", "E20"),
    ):
        found, _ = offline.router.find_clients(f"tell me about {text}")
        assert found and found[0] == expected, text


def test_an_article_never_stands_in_for_a_client_name(offline: Copilot) -> None:
    """'The Bidvest Group' must not match on 'the' and contaminate every query."""
    for question in (
        "Which clients have the largest opportunities?",
        "What is the strongest trade-finance position in the portfolio?",
        "Summarize the top five opportunities in the portfolio.",
    ):
        found, _ = offline.router.find_clients(question)
        assert found == [], (question, found)


def test_products_resolve_from_banker_vocabulary(offline: Copilot) -> None:
    for text, expected in (
        ("cross-border flows", assumptions.FX),
        ("foreign exchange", assumptions.FX),
        ("letters of credit", assumptions.TRADE),
        ("trade finance", assumptions.TRADE),
        ("refinancing", assumptions.LENDING),
        ("capital markets", assumptions.IB),
        ("cash management", assumptions.CASH),
    ):
        route = offline.router.route(f"which clients have {text} opportunities?")
        assert expected in route.products, text


def test_sectors_resolve_from_banker_vocabulary(offline: Copilot) -> None:
    for text, expected in (
        ("mining", "mining"),
        ("retail", "consumer"),
        ("property", "real_estate"),
        ("telcos", "telecoms"),
        ("pharma", "industrials_pharma"),
    ):
        route = offline.router.route(f"which {text} clients have opportunities?")
        assert expected in route.sectors, text


def test_an_empty_question_is_rejected(offline: Copilot) -> None:
    for bad in ("", "   ", "\n"):
        with pytest.raises(ValueError):
            offline.router.route(bad)
        with pytest.raises(ValueError):
            offline.ask(bad)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def test_a_client_question_retrieves_only_that_client(offline: Copilot) -> None:
    _, retrieved, bundle = offline.plan("Prepare a briefing for Shoprite Holdings.")
    assert retrieved.entity_ids == ["E09"]
    assert bundle.entity_ids == ["E09"]
    # ...and no other client's name reaches the model.
    for entity_id, name in offline.clients.items():
        if entity_id == "E09":
            continue
        assert name not in bundle.text, name


def test_a_product_question_retrieves_only_that_product(offline: Copilot) -> None:
    _, retrieved, _ = offline.plan(
        "Which mining clients have the strongest trade-finance opportunities?"
    )
    assert retrieved.products == [assumptions.TRADE]
    assert set(retrieved.pillars["sector"]) == {"mining"}


def test_a_confidence_filter_is_applied_deterministically(offline: Copilot) -> None:
    _, retrieved, _ = offline.plan("Which clients have the largest high-confidence opportunities?")
    assert not retrieved.pillars.empty
    assert set(retrieved.pillars["confidence_band"]) == {"HIGH"}


def test_ranking_comes_from_the_model_not_the_llm(offline: Copilot) -> None:
    """Retrieved rows must already be in selection-score order."""
    _, retrieved, _ = offline.plan("Which clients have the largest opportunities?")
    scores = list(retrieved.pillars["selection_score"])
    assert scores == sorted(scores, reverse=True)


def test_sensitivity_retrieval_matches_the_sweep(offline: Copilot) -> None:
    _, retrieved, _ = offline.plan("How reliable is the Vodacom Group FX opportunity?")
    assert not retrieved.sensitivity.empty
    row = retrieved.sensitivity.iloc[0]
    source = offline.tables["opportunity_sensitivity_summary"]
    expected = source[
        (source["entity_id"] == row["entity_id"]) & (source["product"] == row["product"])
    ].iloc[0]
    assert row["estimate_low"] == pytest.approx(expected["estimate_low"])
    assert row["estimate_high"] == pytest.approx(expected["estimate_high"])
    assert row["sensitivity_flag"] == expected["sensitivity_flag"]


def test_confidence_retrieval_matches_the_intelligence_layer(offline: Copilot) -> None:
    _, retrieved, _ = offline.plan("Prepare a briefing for Glencore.")
    source = offline.tables["opportunity_selection_detail"].set_index(["entity_id", "product"])
    for _, row in retrieved.pillars.iterrows():
        expected = source.loc[(row["entity_id"], row["product"])]
        assert row["confidence"] == pytest.approx(expected["confidence"])
        assert row["confidence_band"] == expected["confidence_band"]


def test_the_whole_database_is_never_sent(offline: Copilot) -> None:
    """A portfolio question must not drag in all 100 client-product rows."""
    _, retrieved, bundle = offline.plan("Which clients have the largest opportunities?")
    assert len(retrieved.pillars) <= copilot_config.MAX_PRODUCT_ROWS
    assert len(bundle.entity_ids) <= copilot_config.MAX_CLIENTS_IN_CONTEXT
    assert bundle.token_estimate <= copilot_config.MAX_CONTEXT_TOKENS


@pytest.mark.parametrize("question,_intent", ROUTING_CASES)
def test_no_context_exceeds_the_token_budget(offline: Copilot, question, _intent) -> None:
    _, _, bundle = offline.plan(question)
    assert bundle.token_estimate <= copilot_config.MAX_CONTEXT_TOKENS


# ---------------------------------------------------------------------------
# Missing and invalid input
# ---------------------------------------------------------------------------


def test_a_client_outside_the_portfolio_is_reported_not_guessed(offline: Copilot) -> None:
    answer = offline.ask("Prepare a briefing for Sasol.")
    assert "Sasol" in answer.text
    assert "does not" in answer.text.lower() or "no client named" in answer.text.lower()
    assert answer.entity_ids == []


@pytest.mark.parametrize(
    "question",
    (
        "What is the weather in Johannesburg?",
        "Write me a poem.",
        "asdfgh qwerty",
    ),
)
def test_an_off_topic_question_is_declined_not_answered(offline: Copilot, question) -> None:
    """The worst failure would be a confident answer to a question nobody asked."""
    route = offline.router.route(question)
    assert route.off_topic, question
    answer = offline.ask(question)
    assert "not about this portfolio" in answer.text
    assert answer.entity_ids == []


def test_asking_for_an_unknown_client_by_name_raises(offline: Copilot) -> None:
    with pytest.raises(KeyError, match="unknown client"):
        offline.brief("Definitely Not A Client")


def test_a_known_client_can_be_briefed_by_id_or_name(offline: Copilot) -> None:
    for handle in ("E09", "Shoprite Holdings", "Shoprite"):
        answer = offline.brief(handle)
        assert answer.entity_ids == ["E09"]


# ---------------------------------------------------------------------------
# Hallucination resistance
# ---------------------------------------------------------------------------


def test_an_invented_figure_is_caught_and_the_answer_discarded(tables, tmp_path) -> None:
    copilot = _with_reply(
        tables,
        tmp_path,
        "Shoprite has an FX opportunity of R999.99bn, which is a substantial position.",
    )
    answer = copilot.ask("Prepare a briefing for Shoprite Holdings.")
    assert answer.mode == copilot_config.FALLBACK_VALIDATION
    assert "R999.99bn" not in answer.text
    assert "r999.99bn" in " ".join(answer.validation["unsupported_figures"])


def test_a_cross_pillar_total_is_caught(tables, tmp_path) -> None:
    copilot = _with_reply(
        tables,
        tmp_path,
        "Adding the pillars gives a total opportunity across the relationship of R1.00tn.",
    )
    answer = copilot.ask("Prepare a briefing for Shoprite Holdings.")
    assert answer.mode == copilot_config.FALLBACK_VALIDATION
    kinds = {violation["kind"] for violation in answer.validation["violations"]}
    assert kinds & {"forbidden_phrase", "unsupported_figure"}


def test_a_competitor_claim_is_caught(tables, tmp_path) -> None:
    copilot = _with_reply(
        tables,
        tmp_path,
        "The remaining flow is competitor-held business that Syn Bank should win back.",
    )
    answer = copilot.ask("Prepare a briefing for Shoprite Holdings.")
    assert answer.mode == copilot_config.FALLBACK_VALIDATION
    kinds = {violation["kind"] for violation in answer.validation["violations"]}
    assert "forbidden_phrase" in kinds


def test_calling_addressable_cash_flow_bank_revenue_is_caught(tables, tmp_path) -> None:
    copilot = _with_reply(
        tables, tmp_path, "This represents a substantial fee pool for the bank."
    )
    answer = copilot.ask("Prepare a briefing for Shoprite Holdings.")
    assert answer.mode == copilot_config.FALLBACK_VALIDATION


def test_giving_lending_a_share_of_wallet_is_caught(tables, tmp_path) -> None:
    copilot = _with_reply(
        tables, tmp_path, "Syn Bank's lending share of wallet with this client is modest."
    )
    answer = copilot.ask("Prepare a briefing for Glencore.")
    assert answer.mode == copilot_config.FALLBACK_VALIDATION


def test_a_rand_figure_attributed_to_investment_banking_is_rejected() -> None:
    """Investment banking has no rand figure, so attributing one is discarded.

    Note the figure IS in the allow-list -- it is a real number from the
    deterministic layer. The fault is the pillar it was hung on, and the guard
    has to catch that on its own rather than relying on the figure check.
    """
    verdict = validation.validate(
        "The investment banking opportunity is worth R42.00bn to the franchise.",
        {"r42.00bn"},
    )
    assert not verdict.ok
    assert "ib_rand_attribution" in {violation.kind for violation in verdict.violations}


#: The attribution rule must reject a figure *given to* investment banking
#: without firing on a sentence that names it beside another pillar's figure.
IB_ATTRIBUTION_CASES = (
    ("The investment banking wallet is R14.20bn.", False),
    ("The investment banking opportunity is worth R42.00bn.", False),
    ("For the stress test, the investment banking wallet is approximately R9.00bn.", False),
    ("Investment banking is a ranked signal with no rand figure.", True),
    ("The FX opportunity is R8.75bn; investment banking is a ranked signal only.", True),
    ("Investment banking carries no rand figure, unlike the trade estimate of R1.20bn.", True),
)


@pytest.mark.parametrize("sentence,is_clean", IB_ATTRIBUTION_CASES)
def test_the_ib_attribution_rule_discriminates(sentence, is_clean) -> None:
    verdict = validation.validate(sentence, context_module.extract_figures(sentence))
    flagged = "ib_rand_attribution" in {violation.kind for violation in verdict.violations}
    assert (not flagged) is is_clean, f"{sentence!r}: {verdict.summary()}"


def test_a_faithful_answer_passes_validation(tables, tmp_path) -> None:
    copilot = Copilot(tables, llm=UnavailableLLM(), audit_log=AuditLog(tmp_path / "a.jsonl"))
    _, _, bundle = copilot.plan("Prepare a briefing for Shoprite Holdings.")
    figure = sorted(bundle.figures)[0]
    faithful = f"Syn Bank's position for this client includes a figure of {figure}."
    verdict = validation.validate(faithful, bundle.figures)
    assert verdict.ok, verdict.summary()


def test_every_figure_in_a_fallback_answer_comes_from_the_context(offline: Copilot) -> None:
    """The deterministic answer must satisfy the same rule the model must."""
    for question, _ in ROUTING_CASES:
        _, _, bundle = offline.plan(question)
        answer = offline.ask(question)
        verdict = validation.validate(answer.text, bundle.figures)
        assert verdict.ok, f"{question}: {verdict.summary()}"


def test_figure_extraction_normalises_spacing_and_case() -> None:
    assert context_module.extract_figures("R8.75bn") == {"r8.75bn"}
    assert context_module.extract_figures("R8.75 BN") == {"r8.75bn"}
    assert context_module.extract_figures("28.13%") == {"28.13%"}
    # ...but never rounds two different figures together.
    assert context_module.extract_figures("R8.75bn and R8.8bn") == {"r8.75bn", "r8.8bn"}


# ---------------------------------------------------------------------------
# Terminology
# ---------------------------------------------------------------------------


def test_no_forbidden_phrase_appears_in_any_context(offline: Copilot) -> None:
    for question, _ in ROUTING_CASES:
        _, _, bundle = offline.plan(question)
        lowered = bundle.text.lower()
        for phrase in validation.FORBIDDEN_PHRASES:
            assert phrase not in lowered, f"{phrase!r} in context for {question!r}"


def test_no_forbidden_phrase_appears_in_any_fallback_answer(offline: Copilot) -> None:
    for question, _ in ROUTING_CASES:
        lowered = offline.ask(question).text.lower()
        for phrase in validation.FORBIDDEN_PHRASES:
            assert phrase not in lowered, f"{phrase!r} in answer for {question!r}"


def test_the_system_prompt_states_every_required_rule() -> None:
    prompt = prompts.SYSTEM_PROMPT.lower()
    for requirement in (
        "never invent",
        "never total figures across products",
        "never claim a competitor holds business",
        "addressable cash flow",
        "peer benchmark",
        "no share of wallet",
        "observed",
        "confidence",
        "sensitive",
        "answer only from the context",
    ):
        assert requirement in prompt, requirement


def test_the_prompt_covers_every_intent() -> None:
    assert set(prompts.INSTRUCTIONS) == set(router.INTENTS)


def test_the_briefing_instruction_specifies_the_required_sections() -> None:
    instruction = prompts.BRIEFING_INSTRUCTION
    for heading in (
        "Executive Summary",
        "Relationship Snapshot",
        "Priority Opportunities",
        "Banker Questions",
        "Model Caveats",
    ):
        assert heading in instruction


def test_the_context_labels_peer_benchmarks_as_such(offline: Copilot) -> None:
    _, _, bundle = offline.plan("Prepare a briefing for Glencore.")
    assert "PEER BENCHMARK" in bundle.text
    assert "accounting identity" in bundle.text
    assert "NO share of wallet" in bundle.text


# ---------------------------------------------------------------------------
# Fallback operation
# ---------------------------------------------------------------------------


def test_the_copilot_works_with_no_api_key(offline: Copilot) -> None:
    answer = offline.ask("Prepare a briefing for Shoprite Holdings.")
    assert answer.mode == copilot_config.FALLBACK_NO_KEY
    assert "Demo / AI unavailable" in answer.notice
    assert "Executive Summary" in answer.text
    assert "Priority Opportunities" in answer.text


def test_a_service_error_falls_back_and_records_why(tables, tmp_path) -> None:
    copilot = Copilot(
        tables,
        llm=FakeLLM(error=LLMUnavailable("connection reset")),
        audit_log=AuditLog(tmp_path / "audit.jsonl"),
    )
    answer = copilot.ask("Prepare a briefing for Shoprite Holdings.")
    assert answer.mode == copilot_config.FALLBACK_ERROR
    assert "connection reset" in (answer.error or "")
    assert answer.text


def test_every_fallback_answer_is_non_trivial(offline: Copilot) -> None:
    for question, _ in ROUTING_CASES:
        answer = offline.ask(question)
        assert len(answer.text) > 200, question


def test_a_stored_demo_is_served_when_the_model_is_unavailable(tables, tmp_path) -> None:
    library = demos_module.DemoLibrary(
        [
            demos_module.DemoAnswer(
                question="Prepare a briefing for Shoprite Holdings.",
                intent=router.CLIENT_BRIEFING,
                answer="A stored briefing generated earlier from the same outputs.",
            )
        ]
    )
    copilot = Copilot(
        tables,
        llm=UnavailableLLM(),
        audit_log=AuditLog(tmp_path / "audit.jsonl"),
        demos=library,
    )
    answer = copilot.ask("Prepare a briefing for Shoprite Holdings.")
    assert answer.mode == copilot_config.DEMO
    assert answer.text.startswith("A stored briefing")
    assert "Demo response" in answer.notice


def test_demo_lookup_ignores_punctuation_and_case() -> None:
    library = demos_module.DemoLibrary(
        [demos_module.DemoAnswer(question="Prepare a briefing for Glencore.", intent="x", answer="y")]
    )
    assert "prepare a briefing for glencore" in library
    assert library.lookup("PREPARE A BRIEFING FOR GLENCORE!") is not None
    assert library.lookup("something else entirely") is None


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_two_identical_questions_produce_identical_context(offline: Copilot) -> None:
    first = offline.plan("Prepare a briefing for Glencore.")[2]
    second = offline.plan("Prepare a briefing for Glencore.")[2]
    assert first.text == second.text
    assert first.figures == second.figures


def test_two_identical_questions_produce_identical_fallback_answers(offline: Copilot) -> None:
    first = offline.ask("Which clients have the largest opportunities?")
    second = offline.ask("Which clients have the largest opportunities?")
    assert first.text == second.text


# ---------------------------------------------------------------------------
# Auditability
# ---------------------------------------------------------------------------

REQUIRED_AUDIT_FIELDS = (
    "query",
    "entity_ids",
    "products",
    "context",
    "settings",
    "answer",
    "timestamp_utc",
    "record_id",
    "intent",
    "retrieval",
)


def test_every_answer_is_audited_with_the_required_fields(tables, tmp_path) -> None:
    log_path = tmp_path / "audit.jsonl"
    copilot = Copilot(tables, llm=UnavailableLLM(), audit_log=AuditLog(log_path))
    copilot.ask("Prepare a briefing for Shoprite Holdings.")
    copilot.ask("Which mining clients have the strongest trade-finance opportunities?")

    records = AuditLog(log_path).read()
    assert len(records) == 2
    for record in records:
        for field in REQUIRED_AUDIT_FIELDS:
            assert field in record, field
        assert record["settings"]["prompt_version"] == copilot_config.PROMPT_VERSION
        assert record["settings"]["model"]
        assert record["timestamp_utc"]


def test_the_audit_log_records_the_retrieved_ids(tables, tmp_path) -> None:
    log_path = tmp_path / "audit.jsonl"
    copilot = Copilot(tables, llm=UnavailableLLM(), audit_log=AuditLog(log_path))
    copilot.ask("Prepare a briefing for Glencore.")
    record = AuditLog(log_path).read()[0]
    assert record["entity_ids"] == ["E02"]
    assert record["retrieval"]["entity_ids"] == ["E02"]
    assert record["retrieval"]["row_counts"]["pillars"] > 0


def test_a_rejected_answer_is_recorded_with_its_violations(tables, tmp_path) -> None:
    log_path = tmp_path / "audit.jsonl"
    copilot = Copilot(
        tables,
        llm=FakeLLM("The opportunity is R123.45tn, a fee pool for the bank."),
        audit_log=AuditLog(log_path),
    )
    copilot.ask("Prepare a briefing for Glencore.")
    record = AuditLog(log_path).read()[0]
    assert record["mode"] == copilot_config.FALLBACK_VALIDATION
    assert record["validation"]["violation_count"] > 0
    assert "R123.45tn" in record["validation"]["rejected_answer"]
    assert "R123.45tn" not in record["answer"]


def test_the_audit_log_refuses_to_store_a_secret(tmp_path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    with pytest.raises(ValueError, match="secret-shaped key"):
        log.write(AuditRecord(query="q", intent="x", settings={"api_key": "nvapi-abc"}))
    with pytest.raises(ValueError, match="NVIDIA-key-shaped"):
        log.write(AuditRecord(query="my key is nvapi-abc123", intent="x"))


def test_no_audit_record_contains_a_key_shaped_string(tables, tmp_path) -> None:
    log_path = tmp_path / "audit.jsonl"
    copilot = Copilot(tables, llm=UnavailableLLM(), audit_log=AuditLog(log_path))
    for question, _ in ROUTING_CASES:
        copilot.ask(question)
    raw = log_path.read_text(encoding="utf-8")
    assert "nvapi-" not in raw
    for record in AuditLog(log_path).read():
        # The literal key, and any field that looks like one. The audit MODE is
        # legitimately named `fallback_no_api_key`, so this checks for a
        # secret-shaped field rather than for the substring.
        assert '"api_key"' not in json.dumps(record)
        assert "nvapi" not in json.dumps(record).lower()


def test_the_settings_object_never_carries_the_key() -> None:
    settings = copilot_config.generation_settings().as_dict()
    assert "api_key" not in settings
    assert not any("nvapi" in str(value) for value in settings.values())


# ---------------------------------------------------------------------------
# The generated-answer path, with a fake model
# ---------------------------------------------------------------------------


def test_a_valid_generated_answer_is_returned_unchanged(tables, tmp_path) -> None:
    copilot = Copilot(tables, llm=UnavailableLLM(), audit_log=AuditLog(tmp_path / "a.jsonl"))
    _, _, bundle = copilot.plan("Prepare a briefing for Glencore.")
    figure = sorted(bundle.figures)[0]
    reply = f"Glencore's position includes {figure} against its addressable activity."

    live = _with_reply(tables, tmp_path, reply)
    answer = live.ask("Prepare a briefing for Glencore.")
    assert answer.mode == copilot_config.LLM
    assert answer.text == reply
    assert answer.validation["ok"]


def test_the_model_receives_the_system_prompt_and_the_context(tables, tmp_path) -> None:
    fake = FakeLLM("An answer with no figures at all.")
    copilot = Copilot(tables, llm=fake, audit_log=AuditLog(tmp_path / "a.jsonl"))
    copilot.ask("Prepare a briefing for Glencore.")
    messages = fake.calls[0]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == prompts.SYSTEM_PROMPT
    assert "END OF CONTEXT" in messages[1]["content"]
    assert "Glencore" in messages[1]["content"]


def test_the_model_is_never_asked_about_a_client_that_was_not_retrieved(
    tables, tmp_path
) -> None:
    fake = FakeLLM("No figures here.")
    copilot = Copilot(tables, llm=fake, audit_log=AuditLog(tmp_path / "a.jsonl"))
    copilot.ask("Prepare a briefing for Shoprite Holdings.")
    prompt = fake.calls[0][1]["content"]
    assert "Glencore" not in prompt
    assert "Vodacom" not in prompt


# ---------------------------------------------------------------------------
# The fallback renderers, directly
# ---------------------------------------------------------------------------


def test_the_briefing_fallback_has_every_required_heading(offline: Copilot) -> None:
    _, retrieved, _ = offline.plan("Prepare a briefing for Glencore.")
    text = fallback.render(retrieved)
    for heading in (
        "## Executive Summary",
        "## Relationship Snapshot",
        "## Priority Opportunities",
        "## Banker Questions",
        "## Model Caveats",
    ):
        assert heading in text, heading


def test_the_briefing_fallback_lists_at_most_three_opportunities(offline: Copilot) -> None:
    for entity_id in offline.clients:
        _, retrieved, _ = offline.plan(f"Prepare a briefing for {entity_id}.")
        text = fallback.render(retrieved)
        assert text.count("- **Opportunity:**") <= fallback.MAX_OPPORTUNITIES, entity_id


def test_every_client_can_be_briefed(offline: Copilot) -> None:
    assert len(offline.clients) == EXPECTED_CLIENTS
    for entity_id in offline.clients:
        answer = offline.brief(entity_id)
        assert answer.entity_ids == [entity_id]
        assert len(answer.text) > 400


def test_a_briefing_never_gives_lending_a_share(offline: Copilot) -> None:
    for entity_id in offline.clients:
        text = offline.brief(entity_id).text.lower()
        for sentence in text.split("."):
            if "lending" in sentence and "share of wallet" in sentence:
                assert "no share of wallet" in sentence, entity_id


def test_a_briefing_never_gives_investment_banking_a_rand_figure(offline: Copilot) -> None:
    for entity_id in offline.clients:
        text = offline.brief(entity_id).text
        for sentence in text.split("\n"):
            lowered = sentence.lower()
            if "investment banking" in lowered or "investment-banking" in lowered:
                figures = context_module.extract_figures(sentence)
                assert not any(token.startswith("r") for token in figures), (
                    entity_id,
                    sentence,
                )


# ---------------------------------------------------------------------------
# Stored demos, if they have been generated
# ---------------------------------------------------------------------------


def test_stored_demos_are_valid_against_the_current_context(offline: Copilot) -> None:
    from src.syn_wallet import config as paths

    library = demos_module.DemoLibrary.from_processed(paths.PROCESSED_DIR)
    if len(library) == 0:
        pytest.skip("no stored demos; run build_copilot_demos with a key")

    for demo in library.all():
        _, _, bundle = offline.plan(demo.question)
        verdict = validation.validate(demo.answer, bundle.figures)
        assert verdict.ok, f"{demo.question}: {verdict.summary()}"


def test_stored_demos_cover_the_three_required_examples(offline: Copilot) -> None:
    from src.syn_wallet import config as paths

    library = demos_module.DemoLibrary.from_processed(paths.PROCESSED_DIR)
    if len(library) == 0:
        pytest.skip("no stored demos; run build_copilot_demos with a key")

    briefings = [demo for demo in library.all() if demo.intent == router.CLIENT_BRIEFING]
    sectors = {
        offline.tables["client_opportunity_intelligence"]
        .set_index("entity_id")
        .at[demo.entity_ids[0], "sector"]
        for demo in briefings
        if demo.entity_ids
    }
    assert len(briefings) >= 3
    assert "mining" in sectors
    assert "consumer" in sectors


#: Sentences that must or must not raise the share-attribution warning. These
#: are the cases that separate a useful guard from one that cries wolf on the
#: model's own methodology note. ``True`` means "clean": no warning at all.
ATTRIBUTION_CASES = (
    (
        "Three Share of Wallet pillars (Cash Management, FX, Trade Finance) and two "
        "opportunity signals (Lending, Investment Banking).",
        True,
    ),
    ("Syn Bank's lending share of wallet with this client is modest.", False),
    ("Lending has no share of wallet because there is no loan book.", True),
    ("The investment banking share of wallet is 12%.", False),
    ("Cash management share of wallet is 0.41%.", True),
    ("For lending, the share of wallet is 22%.", False),
)


@pytest.mark.parametrize("sentence,is_clean", ATTRIBUTION_CASES)
def test_the_share_attribution_guard_discriminates(sentence, is_clean) -> None:
    """The guard must still tell the two apart -- it just warns instead of rejecting."""
    verdict = validation.validate(sentence, context_module.extract_figures(sentence))
    flagged = {
        warning.kind
        for warning in verdict.warnings
        if warning.kind in {"lending_share_of_wallet", "ib_share_of_wallet"}
    }
    assert (not flagged) is is_clean, f"{sentence!r}: {verdict.summary()}"
    # The proximity heuristic itself never discards an answer. (Two of these
    # sentences are still rejected, but by the exact banned-phrase rule --
    # "lending share of wallet" is a literal entry in FORBIDDEN_PHRASES, which
    # needs no heuristic and keeps its bite.)
    assert not any(
        violation.kind in {"lending_share_of_wallet", "ib_share_of_wallet"}
        for violation in verdict.violations
    ), f"{sentence!r}: the attribution heuristic must warn, not reject"


def test_a_stale_stored_demo_is_labelled_not_silently_shown(tables, tmp_path) -> None:
    """The context digest exists so drift can be said out loud."""
    library = demos_module.DemoLibrary(
        [
            demos_module.DemoAnswer(
                question="Prepare a briefing for Glencore.",
                intent=router.CLIENT_BRIEFING,
                answer="A stored briefing generated against older outputs.",
                context_digest="0000000000000000",
            )
        ]
    )
    copilot = Copilot(
        tables,
        llm=UnavailableLLM(),
        audit_log=AuditLog(tmp_path / "audit.jsonl"),
        demos=library,
    )
    answer = copilot.ask("Prepare a briefing for Glencore.")
    assert answer.mode == copilot_config.DEMO
    assert "earlier version of the analytical outputs" in answer.notice


def test_a_current_stored_demo_carries_no_staleness_warning(tables, tmp_path) -> None:
    copilot = Copilot(tables, llm=UnavailableLLM(), audit_log=AuditLog(tmp_path / "a.jsonl"))
    _, _, bundle = copilot.plan("Prepare a briefing for Glencore.")
    library = demos_module.DemoLibrary(
        [
            demos_module.DemoAnswer(
                question="Prepare a briefing for Glencore.",
                intent=router.CLIENT_BRIEFING,
                answer="A stored briefing generated against exactly these outputs.",
                context_digest=demos_module.context_digest(bundle.text),
            )
        ]
    )
    fresh = Copilot(
        tables,
        llm=UnavailableLLM(),
        audit_log=AuditLog(tmp_path / "b.jsonl"),
        demos=library,
    )
    answer = fresh.ask("Prepare a briefing for Glencore.")
    assert answer.mode == copilot_config.DEMO
    assert "earlier version" not in answer.notice


def test_briefing_by_id_finds_the_stored_demo_for_that_client(tables, tmp_path) -> None:
    """`--brief E09` and "Prepare a briefing for Shoprite Holdings." are one question."""
    library = demos_module.DemoLibrary(
        [
            demos_module.DemoAnswer(
                question="Prepare a briefing for Shoprite Holdings.",
                intent=router.CLIENT_BRIEFING,
                answer="The prepared Shoprite briefing.",
            )
        ]
    )
    copilot = Copilot(
        tables,
        llm=UnavailableLLM(),
        audit_log=AuditLog(tmp_path / "audit.jsonl"),
        demos=library,
    )
    for handle in ("E09", "Shoprite Holdings"):
        answer = copilot.brief(handle)
        assert answer.mode == copilot_config.DEMO, handle
        assert answer.text == "The prepared Shoprite briefing."


#: Strings the currency extractor must and must not treat as rand figures.
#: The negative cases are the ones that matter: an over-eager pattern makes the
#: whole validator cry wolf, and an answer containing the word "however" would
#: be rejected for an invented figure that was really an English adverb.
FIGURE_CASES = (
    ("R8.75tn", {"r8.75tn"}),
    ("R443.98bn", {"r443.98bn"}),
    ("-R9.25bn", {"-r9.25bn"}),
    ("R11,048", {"r11048"}),
    ("R61.4m", {"r61.4m"}),
    ("R8.75 BN", {"r8.75bn"}),
    ("28.13%", {"28.13%"}),
    ("a range of R3.09bn to R15.13bn", {"r3.09bn", "r15.13bn"}),
    # ...and the false positives.
    ("However, the figure moves.", set()),
    ("Over 300 clients, or thereabouts.", set()),
    ("Their revenue, broadly, is large.", set()),
    ("Further, no figure applies.", set()),
)


@pytest.mark.parametrize("text,expected", FIGURE_CASES)
def test_currency_extraction_is_exact(text, expected) -> None:
    assert context_module.extract_figures(text) == expected


def test_an_answer_containing_however_is_not_rejected(tables, tmp_path) -> None:
    """Regression: the adverb 'however' once read as an invented rand figure."""
    copilot = Copilot(tables, llm=UnavailableLLM(), audit_log=AuditLog(tmp_path / "a.jsonl"))
    _, _, bundle = copilot.plan("How reliable is the Vodacom Group FX opportunity?")
    reply = (
        "The estimate is sensitive to benchmark assumptions. However, the ranking is stable, "
        "and over 300 comparable observations were tested."
    )
    assert validation.validate(reply, bundle.figures).ok


# ---------------------------------------------------------------------------
# Providers and .env
# ---------------------------------------------------------------------------


def test_the_env_loader_reads_pairs_without_overriding_the_real_environment(
    tmp_path, monkeypatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n"
        "\n"
        "DEEPSEEK_API_KEY=from-the-file\n"
        'SYN_COPILOT_MODEL="deepseek-chat"\n'
        "export SYN_COPILOT_PROVIDER='deepseek'\n"
        "MALFORMED LINE WITH NO EQUALS\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SYN_COPILOT_MODEL", raising=False)
    monkeypatch.setenv("SYN_COPILOT_PROVIDER", "nvidia")  # already set: must win

    loaded = copilot_config.load_env_file(env_file)

    import os

    assert os.environ["DEEPSEEK_API_KEY"] == "from-the-file"
    assert os.environ["SYN_COPILOT_MODEL"] == "deepseek-chat"  # quotes stripped
    assert os.environ["SYN_COPILOT_PROVIDER"] == "nvidia"  # the real environment won
    assert set(loaded) == {"DEEPSEEK_API_KEY", "SYN_COPILOT_MODEL"}
    # The loader reports which names it set, never their values.
    assert "from-the-file" not in str(loaded)


def test_the_env_loader_is_silent_when_there_is_no_file(tmp_path) -> None:
    assert copilot_config.load_env_file(tmp_path / "nothing-here") == {}


def test_the_provider_is_auto_selected_by_which_key_is_present(monkeypatch) -> None:
    monkeypatch.delenv("SYN_COPILOT_PROVIDER", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "x")
    assert copilot_config.active_provider().name == "nvidia"

    monkeypatch.setenv("DEEPSEEK_API_KEY", "y")
    assert copilot_config.active_provider().name == "deepseek"


def test_an_explicit_provider_overrides_auto_selection(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "y")
    monkeypatch.setenv("SYN_COPILOT_PROVIDER", "nvidia")
    provider = copilot_config.active_provider()
    assert provider.name == "nvidia"
    assert provider.base_url == "https://integrate.api.nvidia.com/v1"


def test_an_unknown_provider_is_rejected_by_name(monkeypatch) -> None:
    monkeypatch.setenv("SYN_COPILOT_PROVIDER", "not-a-provider")
    with pytest.raises(ValueError, match="not a known provider"):
        copilot_config.active_provider()


def test_the_key_comes_from_the_active_provider(monkeypatch) -> None:
    monkeypatch.setenv("SYN_COPILOT_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-key")
    assert copilot_config.api_key() == "deepseek-key"

    monkeypatch.setenv("SYN_COPILOT_PROVIDER", "nvidia")
    assert copilot_config.api_key() == "nvidia-key"


def test_the_settings_record_the_provider_and_never_the_key(monkeypatch) -> None:
    monkeypatch.setenv("SYN_COPILOT_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "super-secret-value")
    settings = copilot_config.generation_settings().as_dict()
    assert settings["provider"] == "deepseek"
    assert settings["model"] == "deepseek-chat"
    assert "super-secret-value" not in str(settings)


def test_the_env_example_documents_every_variable_the_code_reads() -> None:
    """The committed template must not drift from the configuration."""
    from src.syn_wallet import config as paths

    template = (paths.REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")
    expected = {
        copilot_config.PROVIDER_ENV,
        copilot_config.MODEL_ENV,
        copilot_config.BASE_URL_ENV,
        *(provider.key_env for provider in copilot_config.PROVIDERS.values()),
    }
    for name in expected:
        assert name in template, name


def test_the_env_example_contains_no_real_key() -> None:
    from src.syn_wallet import config as paths

    template = (paths.REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "nvapi-" not in template
    assert "sk-" not in template
    for line in template.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        _, _, value = stripped.partition("=")
        # Only the two documented non-secret defaults may carry a value.
        if stripped.split("=")[0] in {"SYN_COPILOT_PROVIDER", "SYN_COPILOT_MODEL"}:
            continue
        assert value.strip() == "", f"{stripped!r} carries a value"


def test_dot_env_is_gitignored() -> None:
    from src.syn_wallet import config as paths

    ignored = (paths.REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignored.split()


#: The forbidden-phrase check judges the *claim*, not the substring. A model
#: correctly denying a banned claim writes exactly the sentence the system
#: prompt asks for, and a strict ban rejected it four times in five.
NEGATION_CASES = (
    ("These are unserved client flows, not bank revenue — no pricing exists.", True),
    ("These are client flow magnitudes, rather than bank revenue.", True),
    ("It is not a fee pool and no fee is estimated on it.", True),
    ("The gap is not competitor-held business.", True),
    ("It is never confirmed revenue.", True),
    ("This represents bank revenue for the franchise.", False),
    ("The addressable figure is a substantial fee pool.", False),
    ("The remaining flow is competitor-held business.", False),
    ("This is confirmed revenue for the year.", False),
    ("Syn Bank should win back this volume.", False),
)


@pytest.mark.parametrize("text,should_pass", NEGATION_CASES)
def test_forbidden_phrases_are_judged_on_the_claim_not_the_substring(
    text, should_pass
) -> None:
    verdict = validation.validate(text, context_module.extract_figures(text))
    assert verdict.ok is should_pass, f"{text!r}: {verdict.summary()}"


def test_analyst_voice_is_noted_but_does_not_discard_the_answer() -> None:
    """Voice is a warning; a computed figure is what actually gets rejected."""
    verdict = validation.validate("I calculate the total to be larger.", set())
    assert verdict.ok
    assert {warning.kind for warning in verdict.warnings} == {"analyst_voice"}


def test_a_cross_pillar_total_in_words_is_still_rejected() -> None:
    """Loosening the voice rule must not loosen the totalling rule."""
    verdict = validation.validate(
        "The combined opportunity across cash, FX and trade is substantial.", set()
    )
    assert not verdict.ok
    assert {violation.kind for violation in verdict.violations} == {"forbidden_phrase"}


#: ``(answer figure, context figure, is supported)``. The tolerance admits a
#: number written shorter or rescaled; it must never admit a different number.
ROUNDING_CASES = (
    ("r278.7bn", "r278.72bn", True),
    ("r278.72bn", "r278.72bn", True),
    ("r279bn", "r278.72bn", True),
    ("r0.28tn", "r278.72bn", True),
    ("r278.8bn", "r278.72bn", False),
    ("r5.79bn", "r1.23bn", False),
    ("0.4%", "0.41%", True),
    ("0.42%", "0.41%", False),
    ("r42.00bn", "r42bn", True),
)


@pytest.mark.parametrize("figure,context_figure,supported", ROUNDING_CASES)
def test_figure_support_tolerates_rounding_but_not_invention(
    figure, context_figure, supported
) -> None:
    unsupported = validation.unsupported_figures({figure}, {context_figure})
    assert (not unsupported) is supported, f"{figure} vs {context_figure}: {unsupported}"


def test_a_summed_figure_is_still_rejected_by_the_figure_check() -> None:
    """The guarantee that matters: cross-pillar arithmetic cannot slip through."""
    verdict = validation.validate(
        "Cash is R1.23bn and FX is R4.56bn, giving R5.79bn.",
        {"r1.23bn", "r4.56bn"},
    )
    assert not verdict.ok
    assert verdict.unsupported_figures == ["r5.79bn"]


def test_the_deterministic_layer_is_still_held_to_the_strict_ban(offline: Copilot) -> None:
    """The looser rule applies only to generated prose, never to our own text."""
    for question, _ in ROUTING_CASES:
        lowered = offline.ask(question).text.lower()
        for phrase in validation.FORBIDDEN_PHRASES:
            assert phrase not in lowered, f"{phrase!r} in the deterministic answer for {question!r}"


def test_the_sensitivity_context_supplies_the_opportunity_range(offline: Copilot) -> None:
    """So the model never has to subtract one figure from another to get it."""
    _, _, bundle = offline.plan("How reliable is the Vodacom Group FX opportunity?")
    assert "Sensitivity of the OPPORTUNITY figure specifically" in bundle.text
    assert "Do not compute this range yourself" in bundle.text


def test_demo_mode_disables_the_model_even_with_a_key(monkeypatch) -> None:
    """`serve --demo` must hold even though .env repopulates the key at import."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "a-real-looking-key")
    monkeypatch.delenv(copilot_config.DEMO_ENV, raising=False)
    assert copilot_config.llm_available() is True

    monkeypatch.setenv(copilot_config.DEMO_ENV, "1")
    assert copilot_config.demo_mode() is True
    assert copilot_config.llm_available() is False

    from src.syn_wallet.copilot.llm import ChatClient

    assert ChatClient.available() is False
    assert "demo mode" in ChatClient.unavailable_reason()


# ---------------------------------------------------------------------------
# Demo mode must never reach the network, however it is entered
# ---------------------------------------------------------------------------


def test_demo_mode_blocks_the_client_even_with_a_real_key(monkeypatch) -> None:
    """`serve --demo` promises no outbound call. Two enforcement points keep it."""
    from src.syn_wallet.copilot.llm import ChatClient, LLMUnavailable

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-a-real-looking-key")
    monkeypatch.setenv(copilot_config.DEMO_ENV, "1")

    assert not ChatClient.available()
    assert "demo mode" in (ChatClient.unavailable_reason() or "")
    # And the low-level path refuses too, not just the polite pre-check.
    with pytest.raises(LLMUnavailable, match=copilot_config.DEMO_ENV):
        ChatClient()._client()


def test_demo_mode_is_off_by_default_so_a_key_is_used(monkeypatch) -> None:
    monkeypatch.delenv(copilot_config.DEMO_ENV, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-a-real-looking-key")
    assert copilot_config.llm_available()
