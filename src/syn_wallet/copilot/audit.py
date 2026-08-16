"""An append-only record of every answer the copilot produced.

In a bank, "the model said so" is not an answer to a compliance question. So
every call writes one JSON line holding the question, the route it resolved to,
the entity and product IDs retrieved, the exact context handed to the model, the
model name and prompt version, the answer, the validation verdict and a
timestamp.

**No secret is ever written.** The API key is not in the settings object, not in
the context, and :func:`_assert_no_secret` checks each record before it is
serialised -- so a future edit that starts passing the key through cannot quietly
land it in a log file.

JSONL rather than a table: an audit log is appended to under concurrency and read
back rarely, and a line-per-record file survives a crash mid-write with the loss
of one line rather than the file.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config

#: Where the log lives unless the caller says otherwise.
DEFAULT_LOG_NAME = "copilot_audit.jsonl"

#: Keys that must never appear anywhere in a record, at any depth.
SECRET_KEYS = frozenset({"api_key", "apikey", "authorization", "nvapi", "token", "secret"})


@dataclass
class AuditRecord:
    """One answered question, start to finish."""

    query: str
    intent: str
    entity_ids: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    route: dict[str, Any] = field(default_factory=dict)
    retrieval: dict[str, Any] = field(default_factory=dict)
    context: str = ""
    context_summary: dict[str, Any] = field(default_factory=dict)
    mode: str = config.LLM
    settings: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    answer: str = ""
    error: str | None = None
    latency_seconds: float | None = None
    record_id: str = ""
    timestamp_utc: str = ""
    copilot_version: str = config.COPILOT_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "timestamp_utc": self.timestamp_utc,
            "copilot_version": self.copilot_version,
            "query": self.query,
            "intent": self.intent,
            "entity_ids": list(self.entity_ids),
            "products": list(self.products),
            "route": self.route,
            "retrieval": self.retrieval,
            "context": self.context,
            "context_summary": self.context_summary,
            "mode": self.mode,
            "settings": self.settings,
            "validation": self.validation,
            "answer": self.answer,
            "error": self.error,
            "latency_seconds": self.latency_seconds,
        }


def _assert_no_secret(record: dict[str, Any]) -> None:
    """Refuse to write a record that carries a credential.

    Checks both the keys and, for the API key specifically, the values -- a
    secret pasted into a question by a user would otherwise be logged verbatim.
    """
    key = config.api_key()

    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            for name, value in node.items():
                if name.lower() in SECRET_KEYS:
                    raise ValueError(f"refusing to write a secret-shaped key at {path}.{name}")
                walk(value, f"{path}.{name}")
        elif isinstance(node, (list, tuple)):
            for position, value in enumerate(node):
                walk(value, f"{path}[{position}]")
        elif isinstance(node, str):
            if key and key in node:
                raise ValueError(f"refusing to write the API key found at {path}")
            if "nvapi-" in node:
                raise ValueError(f"refusing to write an NVIDIA-key-shaped string at {path}")

    walk(record)


class AuditLog:
    """Append-only JSONL audit log."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None

    def write(self, record: AuditRecord) -> AuditRecord:
        """Stamp, check and persist one record. Returns the stamped record."""
        record.record_id = record.record_id or uuid.uuid4().hex
        record.timestamp_utc = record.timestamp_utc or datetime.now(UTC).isoformat()
        payload = record.as_dict()
        _assert_no_secret(payload)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return record

    def read(self) -> list[dict[str, Any]]:
        """Every record written so far, oldest first."""
        if self.path is None or not self.path.is_file():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        return records
