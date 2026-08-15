"""Ask the Client Opportunity Copilot a question from the terminal.

::

    python -m src.syn_wallet.ask "Which mining clients have the strongest trade-finance opportunities?"
    python -m src.syn_wallet.ask --brief E09
    python -m src.syn_wallet.ask --context "Prepare a briefing for Glencore."
    python -m src.syn_wallet.ask                       # interactive
    python -m src.syn_wallet.ask --list-models         # what this key can reach

Not a dashboard -- a way to exercise the copilot without one. Works with no API
key, answering deterministically and saying so.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as paths
from .copilot import config as copilot_config
from .copilot.engine import Answer, Copilot

BANNER = "Syn Bank Client Opportunity Copilot"

EXAMPLES = (
    "Prepare a briefing for Shoprite.",
    "Why is Vodacom flagged for an FX opportunity?",
    "Which clients have the largest high-confidence opportunities?",
    "Which mining clients have the strongest trade-finance opportunities?",
    "How reliable is the Vodacom FX opportunity?",
    "What should the banker ask MTN about?",
    "Summarize the top five opportunities in the portfolio.",
)


def _render(answer: Answer, show_context: bool) -> None:
    print()
    if answer.notice:
        print(answer.notice)
        print()
    print(answer.text)
    print()
    detail = [
        f"intent={answer.intent}",
        f"mode={answer.mode}",
        f"clients={','.join(answer.entity_ids) or '-'}",
        f"products={','.join(answer.products) or '-'}",
    ]
    if answer.latency_seconds is not None:
        detail.append(f"{answer.latency_seconds:.2f}s")
    if answer.validation:
        detail.append(
            "validation="
            + ("passed" if answer.validation.get("ok") else "FAILED")
            + f" ({answer.validation.get('checked_figures', 0)} figures checked)"
        )
    print("  [" + "  ".join(detail) + "]")
    if show_context:
        print()
        print("--- CONTEXT SENT TO THE MODEL " + "-" * 50)
        print(answer.context)
        print("-" * 80)


def _list_models() -> int:
    """Print the models the configured key can actually reach.

    Model names move faster than documentation, so this asks the provider rather
    than trusting a constant. Use it to confirm the exact name before pinning
    ``SYN_COPILOT_MODEL``.
    """
    provider = copilot_config.active_provider()
    if not copilot_config.llm_available():
        print(f"No key configured for {provider.name}. Set {provider.key_env} in .env")
        return 1
    try:
        from openai import OpenAI
    except ImportError:
        print("The optional `openai` package is not installed: pip install openai")
        return 1

    client = OpenAI(base_url=copilot_config.base_url(), api_key=copilot_config.api_key())
    try:
        models = sorted(model.id for model in client.models.list().data)
    except Exception as error:  # noqa: BLE001 - this is a diagnostic command
        print(f"Could not list models from {provider.base_url}: {type(error).__name__}: {error}")
        return 1

    print(f"{provider.name} ({provider.base_url}) — {len(models)} models reachable:\n")
    for name in models:
        marker = "  <- current default" if name == copilot_config.model_name() else ""
        print(f"  {name}{marker}")
    print(f"\nPin one with SYN_COPILOT_MODEL=<name> in .env")
    return 0


def _interactive(copilot: Copilot, show_context: bool) -> None:
    print(f"\n{BANNER}")
    status = copilot.llm_status()
    if status["available"]:
        print(f"Generated answers enabled: {status['model']} via {status['provider']}")
    else:
        print(f"Demo / AI unavailable: {status['reason']}")
        print("Answers will be deterministic. Every figure is still real.")
    print("\nTry one of these, or type your own. Ctrl-D or 'quit' to exit.\n")
    for example in EXAMPLES:
        print(f"  - {example}")

    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not question:
            continue
        if question.lower() in {"quit", "exit", "q"}:
            return
        _render(copilot.ask(question), show_context)


def main() -> None:
    parser = argparse.ArgumentParser(description=BANNER)
    parser.add_argument("question", nargs="*", help="The question to ask.")
    parser.add_argument("--brief", metavar="CLIENT", help="Full briefing for one client.")
    parser.add_argument(
        "--context", action="store_true", help="Also print the context sent to the model."
    )
    parser.add_argument(
        "--no-llm", action="store_true", help="Force the deterministic answer."
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List the models the configured key can reach, then exit.",
    )
    parser.add_argument("--processed-dir", type=Path, default=paths.PROCESSED_DIR)
    parser.add_argument(
        "--audit-log",
        type=Path,
        default=None,
        help=f"Append the audit record here (default: none). Try ./{copilot_config.COPILOT_VERSION}.jsonl",
    )
    args = parser.parse_args()

    if args.list_models:
        raise SystemExit(_list_models())

    copilot = Copilot.from_processed(args.processed_dir, audit_path=args.audit_log)
    allow_llm = not args.no_llm

    if args.brief:
        try:
            _render(copilot.brief(args.brief, allow_llm=allow_llm), args.context)
        except KeyError as error:
            print(error, file=sys.stderr)
            raise SystemExit(1) from error
        return

    if args.question:
        _render(copilot.ask(" ".join(args.question), allow_llm=allow_llm), args.context)
        return

    _interactive(copilot, args.context)


if __name__ == "__main__":
    main()
