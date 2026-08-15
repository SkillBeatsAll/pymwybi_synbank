"""Run the adversarial trap catalogue and write ``docs/ADVERSARIAL_QA_REPORT.md``.

::

    python -m analysis.adversarial_qa_report            # live if a key is set
    python -m analysis.adversarial_qa_report --offline  # deterministic only

Every trap in :mod:`analysis.adversarial_suite` is put to the copilot twice
over, and the report records both halves:

**Served answer.** The answer a banker would actually see. Checked for the lie
the trap was fishing for, and for any positive obligation the trap declares --
a "is this certain?" question that comes back without a confidence band has not
been answered honestly even if it invented nothing.

**Poison rejection.** The same trap's bad answer, fed to the validator against
the context that question really retrieves, with the poison's own figures added
to the allow-list so the claim is judged rather than the arithmetic. It must
fail. A trap whose poison passes is a hole, and the report says so in the same
table rather than in a footnote.

With no key the live half is skipped and the deterministic answers are still
checked, so this runs on a judge's laptop and reports honestly either way.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.syn_wallet import config as paths
from src.syn_wallet.copilot import config as copilot_config
from src.syn_wallet.copilot import validation
from src.syn_wallet.copilot.audit import AuditLog
from src.syn_wallet.copilot.engine import Copilot

from .adversarial_suite import TRAPS, Trap, forbidden_hits, missing_requirements
from .report_common import table, write

REPORT_PATH = paths.REPOSITORY_ROOT / "docs" / "ADVERSARIAL_QA_REPORT.md"
RESULTS_PATH = paths.PROCESSED_DIR / "adversarial_qa_results.json"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass
class TrapResult:
    """What happened to one trap, in both halves."""

    trap_id: str
    category: str
    question: str
    expected: str

    offline_verdict: str = SKIP
    offline_hits: tuple[str, ...] = ()

    live_verdict: str = SKIP
    live_mode: str = ""
    live_hits: tuple[str, ...] = ()
    live_missing: tuple[str, ...] = ()
    live_latency_seconds: float | None = None

    poison_verdict: str = SKIP
    poison_caught_by: tuple[str, ...] = ()

    routing_verdict: str = SKIP
    routing_entities: tuple[str, ...] = ()

    @property
    def overall(self) -> str:
        verdicts = (
            self.offline_verdict,
            self.live_verdict,
            self.poison_verdict,
            self.routing_verdict,
        )
        return FAIL if FAIL in verdicts else PASS


def _check_served(answer_text: str, trap: Trap, *, check_requirements: bool) -> tuple[str, ...]:
    hits = tuple(forbidden_hits(answer_text, trap))
    if check_requirements:
        hits += tuple(missing_requirements(answer_text, trap))
    return hits


def run_trap(copilot: Copilot, trap: Trap, *, live: bool) -> TrapResult:
    result = TrapResult(
        trap_id=trap.trap_id,
        category=trap.category,
        question=trap.question,
        expected=trap.expected,
    )

    # -- the deterministic answer --------------------------------------------
    offline = copilot.ask(trap.question, allow_llm=False)
    # Requirements are not checked offline: the template answers what the model
    # outputs support, and a positive obligation like "state the confidence
    # band" is a property of generated prose.
    result.offline_hits = _check_served(offline.rendered(), trap, check_requirements=False)
    result.offline_verdict = FAIL if result.offline_hits else PASS

    # -- routing --------------------------------------------------------------
    if trap.expects_entity:
        _, _, bundle = copilot.plan(trap.question)
        result.routing_entities = tuple(bundle.entity_ids)
        result.routing_verdict = (
            PASS if list(bundle.entity_ids) == [trap.expects_entity] else FAIL
        )

    # -- the poison -----------------------------------------------------------
    if trap.poison:
        _, _, bundle = copilot.plan(trap.question)
        figures = set(bundle.figures) | set(trap.poison_context_figures)
        verdict = validation.validate(trap.poison, figures)
        result.poison_verdict = PASS if not verdict.ok else FAIL
        result.poison_caught_by = tuple(
            sorted({violation.kind for violation in verdict.violations})
        )

    # -- the generated answer -------------------------------------------------
    if live:
        answer = copilot.ask(trap.question)
        result.live_mode = answer.mode
        result.live_latency_seconds = answer.latency_seconds
        hits = _check_served(answer.rendered(), trap, check_requirements=True)
        result.live_hits = tuple(h for h in hits if not h.startswith("none of:"))
        result.live_missing = tuple(h for h in hits if h.startswith("none of:"))
        result.live_verdict = FAIL if hits else PASS

    return result


def run(offline_only: bool = False) -> dict[str, Any]:
    copilot = Copilot.from_processed(audit_path=paths.PROCESSED_DIR / "copilot_audit.jsonl")
    status = copilot.llm_status()
    live = bool(status["available"]) and not offline_only

    results = [run_trap(copilot, trap, live=live) for trap in TRAPS]
    _write_report(results, status, live)
    payload = {
        "prompt_version": copilot_config.PROMPT_VERSION,
        "copilot_version": copilot_config.COPILOT_VERSION,
        "provider": status["provider"],
        "model": status["model"],
        "live": live,
        "traps": len(results),
        "passed": sum(1 for result in results if result.overall == PASS),
        "failed": sum(1 for result in results if result.overall == FAIL),
        "results": [asdict(result) | {"overall": result.overall} for result in results],
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


def _verdict_cell(verdict: str, evidence: tuple[str, ...]) -> str:
    if verdict == SKIP:
        return "—"
    if verdict == PASS:
        return PASS
    return f"**FAIL** — {evidence[0][:120]}" if evidence else f"**{FAIL}**"


def _write_report(results: list[TrapResult], status: dict[str, Any], live: bool) -> None:
    failed = [result for result in results if result.overall == FAIL]
    body: list[str] = []
    add = body.append

    add("# Adversarial QA — Client Opportunity Copilot\n")
    add(
        "Generated by `python -m analysis.adversarial_qa_report`. Every question below is "
        "written to make the copilot say something the data cannot support. Nothing here is "
        "hand-written: each row is the outcome of putting the question to the running "
        "system.\n"
    )

    add("\n## Result\n")
    add(
        f"**{len(results) - len(failed)} of {len(results)} traps PASS.**"
        + ("" if failed else " No trap produced an unsupported claim.")
        + "\n"
    )
    add(
        f"- Prompt version: `{copilot_config.PROMPT_VERSION}`\n"
        f"- Copilot version: `{copilot_config.COPILOT_VERSION}`\n"
        f"- Provider / model: `{status['provider']}` / `{status['model']}`\n"
        f"- Generated answers tested: **{'yes' if live else 'no'}**"
        + ("" if live else f" — {status['reason']}")
        + "\n"
    )

    add("\n## What each column means\n")
    add(
        "| Column | What it checks |\n|---|---|\n"
        "| Deterministic | The template answer served with no model available. A guard that "
        "only holds when the LLM is up is not a guard. |\n"
        "| Generated | The live model's prose, checked for the lie the trap invites and for "
        "any obligation it must meet (stating a confidence band, naming the model as the "
        "source). |\n"
        "| Poison caught | The trap's own bad answer, run through the validator against the "
        "context this question retrieves. It must be rejected. |\n"
        "| Routing | For distractor questions: the client actually retrieved. |\n"
    )

    add("\n## Traps\n")
    add(
        table(
            pd.DataFrame(
                [
                    {
                        "Trap": result.trap_id,
                        "Category": result.category,
                        "Deterministic": _verdict_cell(
                            result.offline_verdict, result.offline_hits
                        ),
                        "Generated": _verdict_cell(
                            result.live_verdict, result.live_hits + result.live_missing
                        ),
                        "Poison caught": (
                            "—"
                            if result.poison_verdict == SKIP
                            else (
                                f"{PASS} ({', '.join(result.poison_caught_by)})"
                                if result.poison_verdict == PASS
                                else "**FAIL** — poison passed validation"
                            )
                        ),
                        "Routing": (
                            "—"
                            if result.routing_verdict == SKIP
                            else (
                                f"{result.routing_verdict} "
                                f"({', '.join(result.routing_entities) or 'none'})"
                            )
                        ),
                        "Overall": result.overall,
                    }
                    for result in results
                ]
            )
        )
    )

    add("\n## Every trap in full\n")
    for result in results:
        add(f"\n### `{result.trap_id}` — {result.category}\n")
        add(f"**Question.** {result.question}\n")
        add(f"\n**A correct answer.** {result.expected}\n")
        add(f"\n**Verdict.** {result.overall}")
        if result.live_mode:
            latency = (
                f", {result.live_latency_seconds:.1f}s"
                if result.live_latency_seconds is not None
                else ""
            )
            add(f" (generated answer mode: `{result.live_mode}`{latency})")
        add("\n")
        for label, hits in (
            ("Deterministic answer breached", result.offline_hits),
            ("Generated answer breached", result.live_hits),
            ("Generated answer omitted", result.live_missing),
        ):
            if hits:
                add(f"\n- _{label}:_ " + "; ".join(hit[:200] for hit in hits) + "\n")
        if result.poison_verdict != SKIP:
            add(
                f"\n- _Poison rejected by:_ {', '.join(result.poison_caught_by) or 'nothing'}\n"
            )

    if failed:
        add("\n## Failures\n")
        add(
            "These are open. Each is a question the system answered in a way the data does "
            "not support.\n\n"
        )
        for result in failed:
            add(f"- **{result.trap_id}** ({result.category})\n")

    write(REPORT_PATH, "".join(body))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the adversarial trap catalogue.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip the live model even if a key is configured.",
    )
    args = parser.parse_args()
    if args.offline:
        os.environ[copilot_config.DEMO_ENV] = "1"
    payload = run(offline_only=args.offline)
    print(
        f"{payload['passed']}/{payload['traps']} traps pass "
        f"({'live' if payload['live'] else 'deterministic only'}) -> {REPORT_PATH}"
    )
    if payload["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
