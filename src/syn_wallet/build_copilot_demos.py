"""Stage 5 helper: generate and store the copilot's demo answers.

::

    NVIDIA_API_KEY=... python -m src.syn_wallet.build_copilot_demos --overwrite

Runs each demo question through the live copilot once, validates the answer, and
stores it in ``data/processed/copilot_demos.json``. The stored answers are what
the copilot serves when no API key is configured, so the demo works offline.

An answer that fails validation is **not stored**. It is reported and the
question is left for the deterministic fallback, because a stored answer is one
a reviewer will read closely and quote.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config as paths
from .copilot import config as copilot_config
from .copilot.demos import DemoAnswer, DemoLibrary, context_digest, normalise_question
from .copilot.engine import Copilot
from .copilot.llm import NimClient

#: The three polished briefings the brief asks for, plus every supported use
#: case, so the offline demo can walk the whole product.
DEMO_QUESTIONS: tuple[dict[str, str], ...] = (
    {
        "question": "Prepare a briefing for Glencore.",
        "title": "Client briefing — major mining client",
        "note": (
            "The largest and best-evidenced position in the book: R8.75tn of Addressable Cash "
            "Flow against a 0.03% share, with a R596.26bn financing opportunity behind it."
        ),
    },
    {
        "question": "Prepare a briefing for Shoprite Holdings.",
        "title": "Client briefing — consumer / retail client",
        "note": (
            "A domestic retailer with the second-largest cash-management opportunity in the "
            "portfolio and the highest observed transactional volume of any client."
        ),
    },
    {
        "question": "Prepare a briefing for MTN Group.",
        "title": "Client briefing — the lending-led client",
        "note": (
            "The only client in the portfolio whose primary opportunity is not cash management. "
            "Its cash confidence falls to MEDIUM while its financing opportunity is HIGH "
            "confidence, so the selection logic promotes lending."
        ),
    },
    {
        "question": "Why is Vodacom Group flagged for an FX opportunity?",
        "title": "Opportunity explanation",
        "note": "A LOW-confidence, benchmark-sensitive estimate explained with its caveats.",
    },
    {
        "question": "Which clients have the largest high-confidence opportunities?",
        "title": "Portfolio query",
        "note": "Deterministic filter on confidence band, ranked by selection score.",
    },
    {
        "question": "Which mining clients have the strongest trade-finance opportunities?",
        "title": "Product query",
        "note": "Deterministic filter on sector and product before any prose is generated.",
    },
    {
        "question": "How reliable is the Vodacom Group FX opportunity?",
        "title": "Sensitivity question",
        "note": "Reads the 36-scenario sweep and gives the range, not a point estimate.",
    },
    {
        "question": "What should the banker ask MTN Group about?",
        "title": "Meeting preparation",
        "note": "Questions come from the deterministic layer; the model only frames them.",
    },
    {
        "question": "Summarize the top five opportunities in the portfolio.",
        "title": "Executive summary",
        "note": "Portfolio-level answer that must not produce a cross-pillar total.",
    },
)

DEMO_REPORT_NAME = "copilot_demo_report.json"

#: This script makes nine calls back to back. DeepSeek handles that comfortably;
#: the free NIM tier rate-limits aggressively and sometimes refuses a single
#: model for many minutes. A short courtesy pause plus escalating retries covers
#: both without making the DeepSeek path slow.
PAUSE_BETWEEN_CALLS_SECONDS = 3.0
MAX_ATTEMPTS = 5
RETRY_BACKOFF_SECONDS = 60.0


def _ask_with_retry(copilot: Copilot, question: str, attempts: int = MAX_ATTEMPTS):
    """Ask once, retrying on a transient failure rather than storing a fallback."""
    last = None
    for attempt in range(1, attempts + 1):
        answer = copilot.ask(question)
        if answer.mode == copilot_config.LLM:
            return answer
        last = answer
        # A validation failure is not transient: the model said something wrong
        # and would probably say it again. Only service errors are worth a retry
        # -- and a truncation, which varies with how long the model thinks and
        # so can succeed on a second pass.
        if answer.mode not in (
            copilot_config.FALLBACK_ERROR,
            copilot_config.FALLBACK_TRUNCATED,
        ):
            return answer
        if attempt < attempts:
            wait = RETRY_BACKOFF_SECONDS * attempt
            print(f"           attempt {attempt} failed ({answer.error}); retrying in {wait:.0f}s")
            time.sleep(wait)
    return last


def run(
    processed_dir: Path | None = None,
    output_dir: Path | None = None,
    overwrite: bool = False,
    questions: tuple[dict[str, str], ...] = DEMO_QUESTIONS,
    only_missing: bool = True,
) -> dict[str, Any]:
    """Generate every demo answer and store the ones that validate.

    ``only_missing`` keeps answers that are already stored and still match the
    current context, so a run interrupted by a rate limit can be resumed without
    paying for the answers that already succeeded.
    """
    processed_dir = (processed_dir or paths.PROCESSED_DIR).resolve()
    output_dir = (output_dir or paths.PROCESSED_DIR).resolve()
    target = output_dir / "copilot_demos.json"
    if target.exists() and not overwrite:
        raise FileExistsError(f"Output already exists ({target.name}). Re-run with --overwrite.")

    if not NimClient.available():
        raise RuntimeError(
            "Demo answers must be generated with a live model. "
            f"{NimClient.unavailable_reason()}"
        )

    copilot = Copilot.from_processed(processed_dir)
    library = DemoLibrary.load(target) if only_missing else DemoLibrary()
    generated: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    calls = 0

    for entry in questions:
        question = entry["question"]
        _, _, bundle = copilot.plan(question)

        existing = library.lookup(question)
        if (
            only_missing
            and existing is not None
            and existing.context_digest == context_digest(bundle.text)
        ):
            print(f"  kept     {question}")
            generated.append(
                {"question": question, "title": entry.get("title", ""), "reused": True}
            )
            continue

        if calls:
            time.sleep(PAUSE_BETWEEN_CALLS_SECONDS)
        calls += 1
        answer = _ask_with_retry(copilot, question)
        row = {
            "question": question,
            "title": entry.get("title", ""),
            "intent": answer.intent,
            "mode": answer.mode,
            "entity_ids": answer.entity_ids,
            "products": answer.products,
            "context_tokens": bundle.token_estimate,
            "validation": answer.validation,
        }
        if answer.mode != copilot_config.LLM:
            row["error"] = answer.error
            rejected.append(row)
            print(f"  SKIPPED  {question}\n           {answer.error}")
            continue

        library.add(
            DemoAnswer(
                question=question,
                intent=answer.intent,
                answer=answer.text,
                entity_ids=answer.entity_ids,
                products=answer.products,
                context_digest=context_digest(bundle.text),
                model=copilot_config.model_name(),
                prompt_version=copilot_config.PROMPT_VERSION,
                generated_at_utc=datetime.now(UTC).isoformat(),
                validation=answer.validation,
                title=entry.get("title", ""),
                note=entry.get("note", ""),
            )
        )
        generated.append(row)
        print(f"  stored   {question}  ({bundle.token_estimate} context tokens)")

    # Never let a rate-limited run destroy answers that already exist. The free
    # NIM tier can refuse every call for minutes at a time, and overwriting a
    # good demo set with an empty one because of that would be the worst
    # possible moment to discover it.
    if len(library) == 0 and target.exists():
        print(
            "\nNothing was generated and demo answers already exist on disk. "
            "Leaving them untouched."
        )
    else:
        library.save(target)

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "copilot_version": copilot_config.COPILOT_VERSION,
        "prompt_version": copilot_config.PROMPT_VERSION,
        "model": copilot_config.model_name(),
        "stored": len(generated),
        "rejected": len(rejected),
        "answers": generated,
        "rejections": rejected,
        "path": str(target),
    }
    (output_dir / DEMO_REPORT_NAME).write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return report


def current_digests(copilot: Copilot, library: DemoLibrary) -> dict[str, str]:
    """The context digest each stored question would produce right now."""
    digests = {}
    for answer in library.all():
        _, _, bundle = copilot.plan(answer.question)
        digests[normalise_question(answer.question)] = context_digest(bundle.text)
    return digests


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the copilot's stored demo answers.")
    parser.add_argument("--processed-dir", type=Path, default=paths.PROCESSED_DIR)
    parser.add_argument("--output-dir", type=Path, default=paths.PROCESSED_DIR)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Regenerate every answer instead of keeping the ones already stored.",
    )
    args = parser.parse_args()
    report = run(
        processed_dir=args.processed_dir,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
        only_missing=not args.regenerate,
    )
    print(
        f"\n{report['stored']} demo answers stored, {report['rejected']} rejected "
        f"({report['model']}, {report['prompt_version']}) -> {report['path']}"
    )


if __name__ == "__main__":
    main()
