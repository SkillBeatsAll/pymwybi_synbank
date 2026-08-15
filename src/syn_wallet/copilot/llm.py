"""The only place this repository talks to a language model.

DeepSeek or NVIDIA NIM, both spoken to through the OpenAI-compatible client --
which is why one class covers both and only the endpoint, key and model name
differ. The surface is deliberately tiny: one method that takes messages and
returns text or raises.
Everything about *what* to send is decided upstream in
:mod:`.prompts` and :mod:`.context`; everything about whether to trust the reply
is decided downstream in :mod:`.validation`.

Three things this module is careful about.

**The key never travels anywhere but the request.** It is read from the
environment at call time, is not stored on the instance in any form that gets
serialised, and never appears in a returned object or an audit record.

**Unavailable is a normal state.** No key, no network, a timeout, a rate limit
and a malformed response are all reported the same way -- as
:class:`LLMUnavailable` -- because the caller does the same thing in every case:
fall back to a deterministic answer and say so.

**The import is lazy.** ``openai`` is an optional dependency. The copilot must
run, and its tests must pass, on a machine that has never installed it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterator

from . import config


class LLMUnavailable(RuntimeError):
    """The model could not be reached, or returned nothing usable."""


class LLMTruncated(LLMUnavailable):
    """The service answered, but the answer did not fit in the output budget.

    A subclass, so existing ``except LLMUnavailable`` handlers keep working, but
    a distinct type so the caller can tell a *reachable* service apart from an
    unreachable one. That distinction is the whole point: a reasoning model that
    spends its allowance thinking returns HTTP 200 with empty content and
    ``finish_reason="length"``, which is not a connectivity failure and must not
    be reported as one.
    """


@dataclass
class Completion:
    """One generated answer plus what it cost, with no secret in sight."""

    text: str
    model: str
    latency_seconds: float
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    max_output_tokens: int | None = None
    attempts: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "latency_seconds": round(self.latency_seconds, 3),
            "finish_reason": self.finish_reason,
            "usage": self.usage,
            "characters": len(self.text),
            "max_output_tokens": self.max_output_tokens,
            "attempts": self.attempts,
        }


def _usage_dict(usage: Any) -> dict[str, Any] | None:
    """The token counts, flattened. Reasoning tokens included where reported.

    ``reasoning_tokens`` is the field that explains a truncation after the fact,
    so it belongs in the audit record rather than only in a debugging session.
    """
    if usage is None:
        return None
    details = getattr(usage, "completion_tokens_details", None)
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        "reasoning_tokens": getattr(details, "reasoning_tokens", None) if details else None,
    }


def openai_available() -> bool:
    """Whether the optional ``openai`` package is importable."""
    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    return True


class ChatClient:
    """A thin, deterministic wrapper over an OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = config.REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.model = model or config.model_name()
        self.base_url = base_url or config.base_url()
        self.timeout = timeout

    # -- availability ------------------------------------------------------

    @staticmethod
    def available() -> bool:
        return config.llm_available() and openai_available()

    @staticmethod
    def unavailable_reason() -> str | None:
        if config.demo_mode():
            return (
                "demo mode is on: answers are assembled deterministically from the model "
                f"outputs. Unset {config.DEMO_ENV} to enable generated prose."
            )
        if not config.llm_available():
            provider = config.active_provider()
            return (
                f"no API key: set {provider.key_env} in .env or the environment to enable "
                f"generated answers from {provider.name}"
            )
        if not openai_available():
            return "the optional `openai` package is not installed"
        return None

    def _client(self):
        # Demo mode is checked here as well as in `available()`, so that it
        # holds even if something calls `complete()` without asking first. The
        # promise `serve --demo` makes to a judge is "this laptop will not talk
        # to the internet", and a promise with one enforcement point is a
        # promise one refactor away from being false.
        if config.demo_mode():
            raise LLMUnavailable(
                f"{config.DEMO_ENV} is set: demo mode never contacts the language-model service, "
                "even when a key is configured"
            )
        key = config.api_key()
        if key is None:
            raise LLMUnavailable(
                f"no API key: set {config.active_provider().key_env} in .env or the environment"
            )
        try:
            from openai import OpenAI
        except ImportError as error:  # pragma: no cover - exercised by openai_available
            raise LLMUnavailable("the optional `openai` package is not installed") from error
        return OpenAI(base_url=self.base_url, api_key=key, timeout=self.timeout)

    # -- generation --------------------------------------------------------

    def complete(self, messages: list[dict[str, str]]) -> Completion:
        """Generate one answer.

        Raises :class:`LLMTruncated` if the service answered but the answer did
        not fit, and :class:`LLMUnavailable` if it could not be reached at all.
        The caller falls back either way, but tells the banker a different --
        and true -- story about which happened.
        """
        client = self._client()
        settings = config.generation_settings()
        started = time.monotonic()

        # Budgets to try, in order. The retry exists because a reasoning model's
        # thinking length varies with the question: the same prompt that needs
        # 1,400 tokens for one client needs 13,000 for another, and no single
        # ceiling is both safe and cheap. Try the measured-sufficient budget,
        # then once more with room to spare before giving up.
        budgets = [settings.max_output_tokens]
        if config.RETRY_OUTPUT_TOKENS > settings.max_output_tokens:
            budgets.append(config.RETRY_OUTPUT_TOKENS)

        truncation: str | None = None
        for attempt, budget in enumerate(budgets, start=1):
            try:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=settings.temperature,
                    top_p=settings.top_p,
                    seed=settings.seed,
                    max_tokens=budget,
                    stream=False,
                )
            except Exception as error:  # noqa: BLE001 - a real connectivity failure
                raise LLMUnavailable(f"{type(error).__name__}: {error}") from error

            choices = getattr(response, "choices", None)
            if not choices:
                raise LLMUnavailable("the service returned no choices")
            message = getattr(choices[0], "message", None)
            text = (getattr(message, "content", None) or "").strip()
            finish = getattr(choices[0], "finish_reason", None)

            if text and finish != "length":
                usage = getattr(response, "usage", None)
                return Completion(
                    text=text,
                    model=self.model,
                    latency_seconds=time.monotonic() - started,
                    finish_reason=finish,
                    usage=_usage_dict(usage),
                    max_output_tokens=budget,
                    attempts=attempt,
                )

            if finish != "length":
                # Answered, not truncated, and still empty. Nothing a bigger
                # budget would fix.
                raise LLMUnavailable(
                    f"the service returned an empty answer (finish_reason={finish!r})"
                )

            # Truncated. A reasoning model that spent the whole budget thinking
            # returns no prose at all; one that ran out mid-sentence returns a
            # partial answer. Neither is servable, and both are worth retrying
            # once with more room.
            reasoning_chars = len(getattr(message, "reasoning_content", None) or "")
            truncation = (
                f"{self.model} hit its {budget:,}-token output limit "
                f"(reasoning: {reasoning_chars:,} characters, prose: {len(text):,} characters)"
            )

        raise LLMTruncated(
            f"{truncation}. The service was reached and responded normally; the answer simply did "
            f"not fit. Raise MAX_OUTPUT_TOKENS / RETRY_OUTPUT_TOKENS, or select a model that "
            f"spends less of its budget on reasoning."
        )

    def stream(self, messages: list[dict[str, str]]) -> Iterator[str]:
        """Yield answer chunks as they arrive.

        Used by an interactive front end. The non-streaming :meth:`complete` is
        what the engine uses, because validation needs the whole answer before
        any of it is shown -- streaming a paragraph and then retracting it would
        be worse than waiting.
        """
        client = self._client()
        settings = config.generation_settings()
        try:
            completion = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=settings.temperature,
                top_p=settings.top_p,
                seed=settings.seed,
                max_tokens=settings.max_output_tokens,
                stream=True,
            )
            for chunk in completion:
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue
                content = getattr(delta, "content", None)
                if content:
                    yield content
        except Exception as error:  # noqa: BLE001
            raise LLMUnavailable(f"{type(error).__name__}: {error}") from error


#: The original name, from when NVIDIA NIM was the only provider. Kept so
#: anything written against it keeps importing.
NimClient = ChatClient
