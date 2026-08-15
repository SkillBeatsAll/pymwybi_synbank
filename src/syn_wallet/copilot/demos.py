"""Stored answers, so a demo without an API key is still a demo.

Judging happens on someone else's laptop, on conference wifi, possibly with no
key at all. A copilot that shows an error in that moment has failed regardless
of how good it is when the network works.

So the polished answers are generated once, checked, and committed as JSON. When
no key is configured and the question matches a stored one, that answer is
served and labelled as a stored demo. When the question does not match, the
deterministic fallback answers it. Either way the banker sees real figures.

A stored answer records the context digest it was generated against, so a demo
that has drifted from the current model outputs can be detected rather than
quietly shown.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config

DEMO_FILE_NAME = "copilot_demos.json"


def normalise_question(question: str) -> str:
    """A stable lookup key: lowercase, punctuation stripped, spaces collapsed."""
    lowered = question.lower().strip()
    stripped = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return re.sub(r"\s+", " ", stripped).strip()


def context_digest(context: str) -> str:
    """A short hash of the context an answer was generated from."""
    return hashlib.sha256(context.encode("utf-8")).hexdigest()[:16]


@dataclass
class DemoAnswer:
    """One stored answer plus the provenance needed to trust it."""

    question: str
    intent: str
    answer: str
    entity_ids: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    context_digest: str = ""
    model: str = ""
    prompt_version: str = ""
    generated_at_utc: str = ""
    validation: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "intent": self.intent,
            "answer": self.answer,
            "entity_ids": list(self.entity_ids),
            "products": list(self.products),
            "context_digest": self.context_digest,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "generated_at_utc": self.generated_at_utc,
            "validation": self.validation,
            "title": self.title,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DemoAnswer":
        return cls(
            question=payload["question"],
            intent=payload.get("intent", ""),
            answer=payload["answer"],
            entity_ids=list(payload.get("entity_ids", [])),
            products=list(payload.get("products", [])),
            context_digest=payload.get("context_digest", ""),
            model=payload.get("model", ""),
            prompt_version=payload.get("prompt_version", ""),
            generated_at_utc=payload.get("generated_at_utc", ""),
            validation=payload.get("validation", {}),
            title=payload.get("title", ""),
            note=payload.get("note", ""),
        )


class DemoLibrary:
    """Stored demo answers, looked up by normalised question."""

    def __init__(self, answers: list[DemoAnswer] | None = None) -> None:
        self._answers: dict[str, DemoAnswer] = {}
        for answer in answers or []:
            self._answers[normalise_question(answer.question)] = answer

    def __len__(self) -> int:
        return len(self._answers)

    def __contains__(self, question: str) -> bool:
        return normalise_question(question) in self._answers

    def lookup(self, question: str) -> DemoAnswer | None:
        return self._answers.get(normalise_question(question))

    def add(self, answer: DemoAnswer) -> None:
        self._answers[normalise_question(answer.question)] = answer

    def all(self) -> list[DemoAnswer]:
        return list(self._answers.values())

    def stale_against(self, digests: dict[str, str]) -> list[str]:
        """Questions whose stored context no longer matches the live one."""
        stale = []
        for key, answer in self._answers.items():
            current = digests.get(key)
            if current is not None and answer.context_digest and current != answer.context_digest:
                stale.append(answer.question)
        return sorted(stale)

    # -- persistence -------------------------------------------------------

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "copilot_version": config.COPILOT_VERSION,
            "prompt_version": config.PROMPT_VERSION,
            "answers": [answer.as_dict() for answer in self._answers.values()],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "DemoLibrary":
        if not path.is_file():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls([DemoAnswer.from_dict(row) for row in payload.get("answers", [])])

    @classmethod
    def from_processed(cls, processed_dir: Path) -> "DemoLibrary":
        return cls.load(processed_dir / DEMO_FILE_NAME)
