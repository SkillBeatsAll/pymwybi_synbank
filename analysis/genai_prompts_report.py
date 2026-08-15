"""Generate ``docs/GENAI_PROMPTS.md`` from :mod:`src.syn_wallet.copilot.prompts`.

    .venv/bin/python -m analysis.genai_prompts_report

The prompts in the document are the prompts the system sends, because they are
read out of the module rather than transcribed. A prompt file that has drifted
from the code is worse than no prompt file: it is a confident description of
something that is not happening.
"""

from __future__ import annotations

from src.syn_wallet import config as paths
from src.syn_wallet.copilot import config, prompts, router
from src.syn_wallet.copilot.retrieval import METHODOLOGY_BY_INTENT, METHODOLOGY_NOTES

from .report_common import write

REPORT_PATH = paths.REPOSITORY_ROOT / "docs" / "GENAI_PROMPTS.md"

#: A worked example question per intent, so a reader can see which instruction
#: fires for which kind of question.
EXAMPLE_QUESTIONS = {
    router.CLIENT_BRIEFING: "Prepare a briefing for Shoprite.",
    router.OPPORTUNITY_EXPLANATION: "Why is Vodacom flagged for an FX opportunity?",
    router.PORTFOLIO_QUERY: "Which clients have the largest high-confidence opportunities?",
    router.PRODUCT_QUERY: "Which mining clients have the strongest trade-finance opportunities?",
    router.SENSITIVITY_QUERY: "How reliable is this FX opportunity?",
    router.MEETING_PREPARATION: "What should the banker ask this client about?",
    router.METHODOLOGY_QUERY: "How does the model calculate addressable cash flow?",
    router.EXECUTIVE_SUMMARY: "Summarize the top five opportunities in the portfolio.",
}


def build_report() -> str:
    sections: list[str] = []
    add = sections.append

    add("# Syn Bank Client Opportunity Copilot — Prompts\n")
    add(
        f"Prompt version `{config.PROMPT_VERSION}`, copilot `{config.COPILOT_VERSION}`. "
        f"Default provider `{config.PROVIDERS['deepseek'].name}`, default model "
        f"`{config.DEFAULT_MODEL}`.\n"
    )
    add(
        "**This file is generated from `src/syn_wallet/copilot/prompts.py`.** The text below is "
        "the text the system sends, character for character, because it is read out of the "
        "module rather than transcribed. Regenerate with "
        "`python -m analysis.genai_prompts_report`.\n"
    )

    add("## 1. Message structure\n")
    add(
        "Every call is exactly two messages. There is no conversation history, no tool calling "
        "and no retrieval performed by the model: retrieval has already happened "
        "deterministically before the model is reached.\n"
    )
    add(
        "```\n"
        "[0] role=system   the system prompt below, identical on every call\n"
        "[1] role=user     the user template below, filled with:\n"
        "                    {question}    the banker's question, verbatim\n"
        "                    {instruction} the per-intent instruction for the routed intent\n"
        "                    {context}     the rendered, token-budgeted structured context\n"
        "```\n"
    )

    add("## 2. Decoding settings\n")
    add(
        "| Setting | Value | Why |\n"
        "|---|---|---|\n"
        f"| `model` | `{config.DEFAULT_MODEL}` | Configurable. Both supported providers speak "
        "the OpenAI chat-completions protocol, so only the endpoint, key and model name differ. |\n"
        f"| `temperature` | {config.TEMPERATURE} | Low, not zero. A banker asking the same "
        "question twice must get the same answer. |\n"
        f"| `top_p` | {config.TOP_P} | |\n"
        f"| `seed` | {config.SEED} | Pinned, so an answer can be reproduced during review. |\n"
        f"| `max_tokens` | {config.MAX_OUTPUT_TOKENS} | A briefing runs to roughly 700 tokens; "
        "the rest is headroom. |\n"
        f"| `stream` | `false` | Validation needs the whole answer before any of it is shown. "
        "Streaming a paragraph and then retracting it would be worse than waiting. |\n"
    )

    add("### Providers\n")
    add(
        "| Provider | Endpoint | Key variable | Default model |\n|---|---|---|---|\n"
        + "".join(
            f"| `{provider.name}` | `{provider.base_url}` | `{provider.key_env}` | "
            f"`{provider.default_model}` |\n"
            for provider in config.PROVIDERS.values()
        )
    )
    add(
        "Selected by `SYN_COPILOT_PROVIDER`, or automatically as the first one whose key is "
        "present. Copy `.env.example` to `.env` and set one key. The prompts below are identical "
        "whichever provider answers.\n"
    )

    add("## 3. Context budget\n")
    add(
        f"| Limit | Value |\n|---|---|\n"
        f"| Maximum context tokens | {config.MAX_CONTEXT_TOKENS:,} |\n"
        f"| Characters per token (estimate) | {config.CHARS_PER_TOKEN} |\n"
        f"| Maximum clients in context | {config.MAX_CLIENTS_IN_CONTEXT} |\n"
        f"| Maximum product rows | {config.MAX_PRODUCT_ROWS} |\n"
        f"| Maximum banker questions | {config.MAX_QUESTIONS} |\n"
        f"| Maximum diagnostics | {config.MAX_DIAGNOSTICS} |\n"
        f"| Maximum portfolio rows | {config.MAX_PORTFOLIO_ROWS} |\n"
    )
    add(
        "The token estimate is deliberately conservative — over-estimating costs a few dropped "
        "background rows, under-estimating costs a failed request in front of a judge. Sections "
        "are dropped from the *end* of a fixed priority order, so client figures survive and "
        "methodology background goes first.\n"
    )

    add("## 4. System prompt\n")
    add("```text\n" + prompts.SYSTEM_PROMPT.strip() + "\n```\n")

    add("## 5. User template\n")
    add("```text\n" + prompts.USER_TEMPLATE.strip() + "\n```\n")

    add("## 6. Per-intent instructions\n")
    add(
        "The router picks exactly one of these, deterministically, before the model is called. "
        "See `docs/GENAI_DESIGN.md` §3 for the routing rules.\n"
    )
    for intent in router.INTENTS:
        instruction = prompts.INSTRUCTIONS[intent]
        example = EXAMPLE_QUESTIONS.get(intent, "")
        add(f"### `{intent}`\n")
        if example:
            add(f"*Example question:* “{example}”\n")
        add("```text\n" + instruction.strip() + "\n```\n")

    add("## 7. Methodology notes available to the context\n")
    add(
        "These are the only pieces of prose the retriever holds. They describe how the model "
        "works, and each is a restatement of something `docs/MODEL_FINAL_REPORT.md` already says. "
        "They are selected by intent so the context does not spend a third of its budget on "
        "background the answer will not use.\n"
    )
    add("| Note | Included for these intents |\n|---|---|\n" + "".join(
        f"| `{key}` | "
        + (
            ", ".join(
                f"`{intent}`"
                for intent, keys in METHODOLOGY_BY_INTENT.items()
                if key in keys
            )
            or "only when its pillar is in play"
        )
        + " |\n"
        for key in METHODOLOGY_NOTES
    ))
    for key, note in METHODOLOGY_NOTES.items():
        add(f"**`{key}`** — {note}\n")

    add("## 8. Post-generation validation\n")
    add(
        "The prompt is one half of the guard; the other half runs afterwards, because an "
        "instruction is a request and a check is a guarantee. Every answer is inspected before a "
        "banker sees it, and a failing answer is **discarded**, not patched:\n"
    )
    add(
        "1. **Unsupported figures** — every rand amount and percentage in the answer must appear "
        "in the context, matched against an allow-list built while the context was rendered.\n"
        "2. **Forbidden phrases** — the vocabulary bans inherited verbatim from the commercial "
        "intelligence layer, plus generative-only ones such as `i calculate` and "
        "`total across all pillars`.\n"
        "3. **Share attribution** — a share of wallet attached to lending or investment banking, "
        "checked by proximity with negation awareness so that a sentence *distinguishing* the "
        "pillars is not falsely flagged.\n"
        "4. **Investment-banking rand** — any sentence attaching a rand amount to the "
        "signal-only pillar.\n"
    )
    add(
        "On failure the banker gets the deterministic answer, a notice saying the AI answer was "
        "rejected, and the violation is written to the audit log — so a reviewer can measure how "
        "often the model misbehaved rather than trusting that it did not.\n"
    )

    return "\n".join(sections)


def main() -> None:
    write(REPORT_PATH, build_report())


if __name__ == "__main__":
    main()
