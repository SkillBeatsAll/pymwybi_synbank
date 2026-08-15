"""Configuration for the Client Opportunity Copilot.

Everything the copilot is allowed to do, and every limit it works inside, is
declared here. Two things this file is strict about.

**No secret is ever stored.** The API key is read from the environment at call
time and never written to disk, never placed in the audit log, and never
returned in a result object. :func:`api_key` is the only place it is read.

**The model is a writer, not a calculator.** It receives figures that the
deterministic layers already computed and turns them into prose. It is never
asked to add, divide, rank, or estimate anything, and
:mod:`.validation` checks the answer for figures that were not in the context.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import config as paths

#: Bumped whenever a prompt changes. Recorded on every audit entry so an answer
#: can always be traced to the exact instructions that produced it.
PROMPT_VERSION = "copilot-prompt-1.1.0"
COPILOT_VERSION = "copilot-1.1.0"

# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------

ENV_FILE_NAME = ".env"


def load_env_file(path: Path | None = None, override: bool = False) -> dict[str, str]:
    """Read ``KEY=value`` pairs from ``.env`` into the process environment.

    Deliberately tiny and dependency-free. Two rules that matter:

    * **The real environment wins.** A variable already set is left alone unless
      ``override`` is passed, so ``DEEPSEEK_API_KEY=... python -m ...`` on the
      command line beats a stale value in the file.
    * **Nothing is echoed.** The returned dict names the keys that were loaded,
      never their values, so a caller can log "loaded 2 variables from .env"
      without logging a credential.

    ``.env`` is gitignored. ``.env.example`` is the committed template and holds
    no real values.
    """
    path = path or (paths.REPOSITORY_ROOT / ENV_FILE_NAME)
    loaded: dict[str, str] = {}
    if not path.is_file():
        return loaded

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if not name:
            continue
        if override or name not in os.environ:
            os.environ[name] = value
            loaded[name] = "set"
    return loaded


# Loaded once at import so every entry point -- CLI, tests, a future dashboard
# -- sees the same configuration without each having to remember to call it.
load_env_file()

# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
#
# Both providers speak the OpenAI chat-completions protocol, so the client code
# is identical and only the endpoint, key and model name differ. Keeping them in
# a table rather than in branching means adding a third is a data change.


@dataclass(frozen=True)
class Provider:
    """One OpenAI-compatible endpoint the copilot can speak to."""

    name: str
    base_url: str
    key_env: str
    default_model: str
    note: str


PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider(
        name="deepseek",
        base_url="https://api.deepseek.com",
        key_env="DEEPSEEK_API_KEY",
        default_model="deepseek-chat",
        note=(
            "DeepSeek's own API, OpenAI-compatible. `deepseek-chat` is the default because this "
            "stage only writes prose -- the reasoning was already done by the deterministic "
            "layers. Measured on the same briefing, `deepseek-v4-flash` is a REASONING model: it "
            "spends 16k characters thinking before writing, taking 38.5s and 5,545 tokens where "
            "`deepseek-chat` takes 10.9s and 1,717 for an answer of the same quality. Flash is "
            "fully supported -- set SYN_COPILOT_MODEL=deepseek-v4-flash -- it is simply paying "
            "for capacity this architecture deliberately does not need."
        ),
    ),
    "nvidia": Provider(
        name="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        key_env="NVIDIA_API_KEY",
        default_model="z-ai/glm-5.2",
        note="NVIDIA NIM. Free tier rate-limits per model, sometimes for long stretches.",
    ),
}

#: Order in which a provider is auto-selected when none is named: the first one
#: whose key is present. DeepSeek first because it is the configured primary.
PROVIDER_PRIORITY = ("deepseek", "nvidia")

#: Environment variables.
PROVIDER_ENV = "SYN_COPILOT_PROVIDER"
MODEL_ENV = "SYN_COPILOT_MODEL"
BASE_URL_ENV = "SYN_COPILOT_BASE_URL"

#: Retained so anything written against the NIM-only version keeps working.
API_KEY_ENV = "DEEPSEEK_API_KEY"

#: Force deterministic answers even when a key is configured. Set by
#: ``serve --demo``. It has to be an explicit flag rather than "unset the key",
#: because :func:`load_env_file` would simply put the key back at import time.
DEMO_ENV = "SYN_COPILOT_DEMO"


def demo_mode() -> bool:
    return os.environ.get(DEMO_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def active_provider() -> Provider:
    """The provider to use: named explicitly, or the first one with a key."""
    named = os.environ.get(PROVIDER_ENV, "").strip().lower()
    if named:
        if named not in PROVIDERS:
            raise ValueError(
                f"{PROVIDER_ENV}={named!r} is not a known provider. "
                f"Choose one of: {', '.join(sorted(PROVIDERS))}."
            )
        return PROVIDERS[named]
    for name in PROVIDER_PRIORITY:
        provider = PROVIDERS[name]
        if os.environ.get(provider.key_env, "").strip():
            return provider
    return PROVIDERS[PROVIDER_PRIORITY[0]]


#: Kept for the report generators, which name the default in prose.
DEFAULT_BASE_URL = PROVIDERS["deepseek"].base_url
DEFAULT_MODEL = PROVIDERS["deepseek"].default_model

#: Deterministic decoding. A banker asking the same question twice must get the
#: same answer, and a test that pins an answer must stay pinned. This is a
#: different setting from the provider's own sample for a reason.
TEMPERATURE = 0.2
TOP_P = 0.95
SEED = 42

#: Output ceiling. A briefing is roughly 1,600 tokens of prose -- everything
#: above that is headroom for a REASONING model, which spends its budget
#: thinking before it writes a word.
#:
#: This number was measured, not guessed. Running the ten demo questions through
#: `deepseek-v4-flash` with a 16,000-token allowance, reasoning ran to 1,360 -
#: 12,983 tokens and the worst total was **14,865** (the Vodacom briefing:
#: 12,983 reasoning + 1,882 of prose). At the old 2,048 and 8,192 ceilings that
#: briefing hit `finish_reason="length"` having emitted reasoning only, so the
#: API returned a **successful response with empty content** -- which the client
#: reported as "unavailable" and the dashboard rendered as "the language-model
#: service could not be reached". The service was reachable every time; the
#: answer simply never fit.
#:
#: Reasoning is kept, because it measurably improves adherence to the rules in
#: :mod:`.prompts`. The ceiling is set to fit it instead of cutting it off.
MAX_OUTPUT_TOKENS = 16384

#: Retry allowance when a first attempt is truncated mid-reasoning. One retry
#: only: if a question cannot be answered in 32k output tokens it is not going to
#: be, and a banker should not wait through a third round trip.
RETRY_OUTPUT_TOKENS = 32768

#: How long to wait before deciding the service really is unreachable. A
#: reasoning model generating 15k tokens takes well over a minute, so the old
#: 60s ceiling turned a slow-but-working answer into a timeout, and a timeout
#: into the same misleading "could not be reached" notice.
REQUEST_TIMEOUT_SECONDS = 240.0

# ---------------------------------------------------------------------------
# Context budget
# ---------------------------------------------------------------------------
#
# The context is built by deterministic retrieval, so its size is controlled at
# source rather than by truncating a blob. These caps are the backstop.

#: Rough characters-per-token for budgeting. Deliberately conservative: over-
#: estimating tokens costs a few dropped rows, under-estimating costs a failed
#: request in front of a judge.
CHARS_PER_TOKEN = 3.5

#: Hard ceiling on the structured context handed to the model.
MAX_CONTEXT_TOKENS = 9000

#: Row caps per retrieval kind. A portfolio question does not need twenty
#: clients x five pillars; it needs the top handful with their evidence.
MAX_CLIENTS_IN_CONTEXT = 8
MAX_PRODUCT_ROWS = 12
MAX_QUESTIONS = 4
MAX_DIAGNOSTICS = 6
MAX_PORTFOLIO_ROWS = 20


def api_key() -> str | None:
    """The active provider's key from the environment, or None.

    Returning None is a supported state, not an error: the copilot falls back to
    deterministic answers and says so.
    """
    value = os.environ.get(active_provider().key_env, "").strip()
    return value or None


def base_url() -> str:
    return os.environ.get(BASE_URL_ENV, "").strip() or active_provider().base_url


def model_name() -> str:
    return os.environ.get(MODEL_ENV, "").strip() or active_provider().default_model


def provider_name() -> str:
    return active_provider().name


def llm_available() -> bool:
    return not demo_mode() and api_key() is not None


def estimate_tokens(text: str) -> int:
    """A cheap, conservative token estimate. No tokeniser dependency."""
    return int(len(text) / CHARS_PER_TOKEN) + 1


@dataclass(frozen=True)
class GenerationSettings:
    """What was actually sent, for the audit record. Never carries the key."""

    provider: str
    model: str
    base_url: str
    temperature: float
    top_p: float
    seed: int
    max_output_tokens: int
    prompt_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "seed": self.seed,
            "max_output_tokens": self.max_output_tokens,
            "prompt_version": self.prompt_version,
        }


def generation_settings() -> GenerationSettings:
    return GenerationSettings(
        provider=provider_name(),
        model=model_name(),
        base_url=base_url(),
        temperature=TEMPERATURE,
        top_p=TOP_P,
        seed=SEED,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        prompt_version=PROMPT_VERSION,
    )


# ---------------------------------------------------------------------------
# Answer modes
# ---------------------------------------------------------------------------

LLM = "llm"
FALLBACK_NO_KEY = "fallback_no_api_key"
FALLBACK_ERROR = "fallback_service_error"
FALLBACK_TRUNCATED = "fallback_answer_truncated"
FALLBACK_VALIDATION = "fallback_validation_failed"
DEMO = "demo_stored_response"

FALLBACK_MODES = (
    FALLBACK_NO_KEY,
    FALLBACK_ERROR,
    FALLBACK_TRUNCATED,
    FALLBACK_VALIDATION,
    DEMO,
)

MODE_NOTICE = {
    LLM: "",
    FALLBACK_NO_KEY: (
        "**Demo / AI unavailable** — no language-model key is configured, so this answer was "
        "assembled deterministically from the analytical outputs. Every figure is real; only the "
        "prose is templated."
    ),
    # Reserved for a genuinely unreachable service: no network, DNS failure, a
    # refused key, a 5xx, a rate limit, a timeout. It must never be shown for a
    # request the service answered, which is why truncation has its own mode --
    # telling a judge the service was unreachable when it responded in 40
    # seconds is the kind of wrong that costs more than the missing prose.
    FALLBACK_ERROR: (
        "**Demo / AI unavailable** — the language-model service could not be reached, so this "
        "answer was assembled deterministically from the analytical outputs. Every figure is "
        "real; only the prose is templated."
    ),
    FALLBACK_TRUNCATED: (
        "**AI answer incomplete** — the language-model service responded, but the answer ran past "
        "its output limit before it was finished, so this deterministic answer was used instead. "
        "Every figure is real; only the prose is templated."
    ),
    FALLBACK_VALIDATION: (
        "**AI answer rejected** — the generated answer contained a figure or a phrase that is "
        "not supported by the retrieved context, so it was discarded and this deterministic "
        "answer was used instead. The rejection is recorded in the audit log."
    ),
    DEMO: (
        "**Demo response** — a stored answer generated from the same analytical outputs, shown "
        "because no live language-model key is configured."
    ),
}

#: Appended to the demo notice when the stored answer was generated against an
#: older version of the model outputs. The digest is recorded precisely so this
#: can be *said* rather than quietly glossed over: every figure in the answer is
#: still supported by the current context, but the context has since gained
#: detail the answer could not have known about.
STALE_DEMO_NOTICE = (
    " This answer was generated against an earlier version of the analytical outputs, so it may "
    "not reflect detail added since. Every figure in it is still supported by the current model "
    "outputs; regenerate with `build_copilot_demos --overwrite --regenerate` to refresh."
)
