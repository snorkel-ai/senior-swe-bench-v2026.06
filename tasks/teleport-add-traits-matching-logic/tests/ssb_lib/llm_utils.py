"""Shared LLM client/creds/model/retry helpers for the reward judges.

All judge model calls go through this module's :func:`complete` /
:func:`complete_structured`, which wrap ``litellm.completion`` — so the judges
are model-agnostic (any popular frontier model litellm supports) and do not
depend on the Anthropic SDK. Routing is internal: when a gateway routing key is
set we talk to Portkey as an OpenAI-compatible endpoint (Portkey then reaches
Bedrock/Anthropic/etc.); otherwise litellm dispatches directly to the provider
implied by the model slug using that provider's native credentials.

``litellm`` is imported lazily inside the call helpers, so ``import llm_utils``
never fails on a missing SDK. Reached under both the ``src.python.ssb_lib``
(offline) and ``ssb_lib`` (in-container) namespaces, so intra-package imports
stay relative.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel

# Model defaults. Portkey/Bedrock form is the common case; the plain form is
# for direct provider access (litellm infers the provider from the slug).
JUDGE_MODEL_PORTKEY = "@bedrock/global.anthropic.claude-sonnet-4-6"
JUDGE_MODEL_DIRECT = "claude-sonnet-4-6"

CLASSIFIER_MODEL_PORTKEY = "@bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0"
CLASSIFIER_MODEL_DIRECT = "claude-haiku-4-5-20251001"

_RETRY_ATTEMPTS = 4

# gpt-5.x/o-series reject tools+reasoning on chat/completions; setting reasoning_effort
# makes litellm bridge them to the Responses API, which returns chat-shaped output.
_OPENAI_REASONING_EFFORT = "medium"

# Any model can return no tool call despite a forced tool_choice; re-issue up to this many times.
_FORCED_TOOL_RETRIES = 3

# Substrings marking a transient (retryable) error, matched case-insensitively
# against ``str(exc)``. Kept specific ("rate limit", not bare "rate") and free of
# bare numbers — a digit run like "500" matches far too much error text (paths,
# token counts, line numbers). HTTP status codes are classified separately,
# against the exception's structured ``status_code``, never by substring.
_TRANSIENT_MARKERS = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "overloaded",
    "timeout",
    "timed out",
    "connection",
    "temporarily",
)

# Transient/retryable HTTP status codes, matched against the exception's
# ``status_code`` attribute (litellm exposes it) — not its message text.
_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 529})


_PORTKEY_BASE_URL = "https://api.portkey.ai"

# Gateway routing key. We standardize on PORTKEY_API_KEY for everything — judges
# and validation agent alike — so there is a single key to set and forward. The
# old per-role names (VAL_AGENT_PORTKEY_KEY / CC_VAL_PORTKEY_KEY) are no longer
# consulted.
_ROUTER_KEY_VARS = ("PORTKEY_API_KEY",)

# Per-role model overrides (settable via verifier env / harbor ``--ve``). Each
# replaces its role's default model with the given slug — a Portkey
# ``@provider/model`` catalog slug when a gateway key is set, else a litellm
# provider slug like ``openai/gpt-5.5``. The judge override covers the rubric,
# taste, and validation-review judges (one model for all three); the classifier
# override is separate so the cheap structural classifier can stay independent.
JUDGE_MODEL_OVERRIDE_VAR = "SSB_OVERRIDE_ALL_JUDGE_MODEL"
CLASSIFIER_MODEL_OVERRIDE_VAR = "SSB_OVERRIDE_CLASSIFIER_MODEL"


def resolve_portkey_key() -> str | None:
    """Return the gateway routing key from the first env var that is set."""
    for var in _ROUTER_KEY_VARS:
        if os.environ.get(var):
            return os.environ[var]
    return None


def have_credentials() -> bool:
    """True if any usable credential is present: gateway (Portkey), or a direct
    provider key (Anthropic or OpenAI). Direct OpenAI applies when the SSB_OVERRIDE_*
    model slugs point at ``openai/...`` and only OPENAI_API_KEY is set."""
    return bool(
        resolve_portkey_key()
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


def _is_routed() -> bool:
    """True when a gateway routing key is set (vs. direct provider access)."""
    return bool(resolve_portkey_key())


def judge_model() -> str:
    """Resolve the judge model name (shared by rubric, taste, validation review).

    ``SSB_OVERRIDE_ALL_JUDGE_MODEL`` wins if set; otherwise the gateway-native
    or direct default is chosen internally based on whether a routing key is set.
    """
    override = os.environ.get(JUDGE_MODEL_OVERRIDE_VAR)
    if override:
        return override
    return JUDGE_MODEL_PORTKEY if _is_routed() else JUDGE_MODEL_DIRECT


def classifier_model() -> str:
    """Resolve the patch-classifier model name.

    ``SSB_OVERRIDE_CLASSIFIER_MODEL`` wins if set; otherwise the gateway-native
    or direct default is chosen internally.
    """
    override = os.environ.get(CLASSIFIER_MODEL_OVERRIDE_VAR)
    if override:
        return override
    return CLASSIFIER_MODEL_PORTKEY if _is_routed() else CLASSIFIER_MODEL_DIRECT


# ───────────────────────────────────────────────────────────────────────────
# litellm routing + call helpers
# ───────────────────────────────────────────────────────────────────────────


def _litellm() -> Any:
    """Import litellm lazily; tolerate provider-unsupported params (e.g. thinking)."""
    import litellm

    litellm.drop_params = True
    return litellm


@dataclasses.dataclass(frozen=True)
class Routing:
    """litellm.completion kwargs for one call, resolved from the environment."""

    model: str
    custom_llm_provider: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    extra_headers: dict[str, str] | None = None

    def completion_kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = {"model": self.model}
        if self.custom_llm_provider:
            kw["custom_llm_provider"] = self.custom_llm_provider
        if self.api_base:
            kw["api_base"] = self.api_base
        if self.api_key:
            kw["api_key"] = self.api_key
        if self.extra_headers:
            kw["extra_headers"] = dict(self.extra_headers)
        return kw


def resolve_routing(model: str) -> Routing:
    """Resolve litellm routing for ``model`` (model overrides are applied upstream
    in ``judge_model`` / ``classifier_model``, not here).

    With a gateway key set, route through Portkey's OpenAI-compatible endpoint:
    ``custom_llm_provider="openai"`` + Portkey ``/v1`` base + the
    ``x-portkey-api-key`` header (a placeholder ``api_key`` satisfies litellm's
    OpenAI client, which authenticates via the header). The model slug is passed
    verbatim — Portkey's model catalog accepts the ``@provider/model`` form.

    Without a gateway key, litellm dispatches directly to the provider implied
    by the slug using that provider's native env credentials.
    """
    key = resolve_portkey_key()
    if key:
        return Routing(
            model=model,
            custom_llm_provider="openai",
            api_base=f"{_PORTKEY_BASE_URL}/v1",
            api_key="portkey",
            extra_headers={"x-portkey-api-key": key},
        )
    return Routing(model=model)


def _is_openai_reasoning(model: str) -> bool:
    """True for OpenAI reasoning slugs (``openai/gpt-5.x``, ``openai/o<N>``); matches run_validate.py."""
    leaf = model.split("/")[-1].lower()
    return model.startswith("openai/") and (
        leaf.startswith("gpt-5") or (leaf[:1] == "o" and leaf[1:2].isdigit())
    )


def openai_tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Build an OpenAI-format tool entry from a JSON-schema ``parameters`` dict."""
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


def tool_call_args(response: Any, name: str | None = None) -> dict[str, Any] | None:
    """Parsed JSON arguments of the first tool call (optionally matching ``name``).

    Returns ``None`` when no matching tool call is present or its arguments
    aren't valid JSON.
    """
    try:
        message = response.choices[0].message
    except (AttributeError, IndexError):
        return None
    for call in getattr(message, "tool_calls", None) or []:
        fn = getattr(call, "function", None)
        if fn is None:
            continue
        if name is None or fn.name == name:
            try:
                return json.loads(fn.arguments)
            except (TypeError, json.JSONDecodeError):
                return None
    return None


def complete(  # noqa: PLR0913
    *,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
    system: str | None = None,
    thinking_budget: int = 0,
    attempts: int = _RETRY_ATTEMPTS,
    **kwargs: Any,
) -> Any:
    """One model call via ``litellm.completion`` with bounded transient retry.

    ``system`` is prepended as an OpenAI ``system`` message. ``thinking_budget``
    enables extended thinking where the provider supports it (dropped otherwise,
    via ``litellm.drop_params``).
    """
    litellm = _litellm()
    routing = resolve_routing(model)

    msgs = list(messages)
    if system is not None:
        msgs = [{"role": "system", "content": system}, *msgs]

    call_kwargs: dict[str, Any] = {**routing.completion_kwargs(), "messages": msgs, "max_tokens": max_tokens}
    if tools is not None:
        call_kwargs["tools"] = tools
    if tool_choice is not None:
        call_kwargs["tool_choice"] = tool_choice
    if thinking_budget > 0:
        call_kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
    call_kwargs.update(kwargs)

    # gpt-5.x: set reasoning_effort so litellm bridges to the Responses API (chat-shaped
    # output, so callers are unchanged). Other models untouched.
    if _is_openai_reasoning(routing.model) and "reasoning_effort" not in call_kwargs:
        call_kwargs["reasoning_effort"] = _OPENAI_REASONING_EFFORT

    resp = _call_with_retry(litellm.completion, attempts, call_kwargs)
    # Re-issue a forced-tool call that returned no parseable tool — a miss any model can
    # produce. Bounded; a no-op when the first call already returns the tool.
    forced = tool_choice.get("function", {}).get("name") if isinstance(tool_choice, dict) else None
    if forced:
        tries = 0
        while tool_call_args(resp, forced) is None and tries < _FORCED_TOOL_RETRIES:
            tries += 1
            resp = _call_with_retry(litellm.completion, attempts, call_kwargs)
    return resp


def complete_structured(  # noqa: PLR0913
    *,
    model: str,
    messages: list[dict[str, Any]],
    schema: type[BaseModel],
    max_tokens: int = 2048,
    system: str | None = None,
    thinking_budget: int = 0,
    tool_name: str = "emit_result",
    tool_description: str = "Emit the result as structured data.",
    attempts: int = _RETRY_ATTEMPTS,
) -> Any | None:
    """Forced structured output: one forced tool call parsed into ``schema``.

    Returns a validated ``schema`` instance, or ``None`` if the model produced
    no parseable tool call (caller decides the fallback). Forced-tool is used
    rather than ``response_format`` for the broadest cross-provider support.
    """
    tool = openai_tool(tool_name, tool_description, schema.model_json_schema())
    response = complete(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": tool_name}},
        system=system,
        thinking_budget=thinking_budget,
        attempts=attempts,
    )
    args = tool_call_args(response, tool_name)
    if args is None:
        return None
    return schema.model_validate(args)


# The validation agent is a subprocess (Claude Code CLI or mini-swe-agent), not
# an in-process litellm call, so it is routed via environment variables rather
# than resolve_routing(). agent_routing() is the single place that knows how each
# harness reaches the gateway; callers apply the returned env + model fields blindly.


@dataclasses.dataclass
class AgentRouting:
    """Routing config for a validation-agent subprocess.

    ``env`` is merged into the subprocess environment; ``model`` is the slug to
    invoke with; ``model_class`` / ``provider`` are litellm hints (mini-swe-agent
    only). ``label`` is a human tag for logs ("gateway" / "direct").
    """

    model: str
    env: dict[str, str]
    model_class: str = ""
    provider: str = ""
    label: str = "direct"


def _provider_for_model(model: str) -> str:
    """Infer the gateway provider from a gateway-native model slug (``@prov/...``)."""
    if model.startswith("@") and "/" in model:
        return model[1:].split("/", 1)[0]
    return ""


def agent_routing(harness: str, model: str) -> AgentRouting:
    """Resolve subprocess routing for a validation-agent ``harness`` + ``model``.

    ``harness`` is ``"claude_code"`` (routed via ANTHROPIC_* env vars) or
    ``"miniswe"`` (routed via PORTKEY_API_KEY + litellm model_class/provider).
    With no routing key set, returns direct config (empty routing env).
    """
    key = resolve_portkey_key()
    if not key:
        return AgentRouting(model=model, env={})

    if harness == "claude_code":
        return AgentRouting(
            model=model,
            env={
                "ANTHROPIC_BASE_URL": _PORTKEY_BASE_URL,
                "ANTHROPIC_AUTH_TOKEN": key,
                "ANTHROPIC_CUSTOM_HEADERS": f"x-portkey-api-key: {key}",
            },
            label="gateway",
        )

    # mini-swe-agent (litellm): pass the key through and, for gateway-native
    # slugs, set the portkey model_class + provider.
    model_class = "portkey" if model.startswith("@") else ""
    return AgentRouting(
        model=model,
        env={"PORTKEY_API_KEY": key},
        model_class=model_class,
        provider=_provider_for_model(model) if model_class else "",
        label="gateway",
    )


def _is_transient(exc: Exception) -> bool:
    code = getattr(exc, "status_code", None)
    if code is not None:
        try:
            if int(code) in _TRANSIENT_STATUS_CODES:
                return True
        except (TypeError, ValueError):
            pass
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


def _call_with_retry(fn: Any, attempts: int, kwargs: dict) -> Any:
    """Call ``fn(**kwargs)`` with bounded retry on transient errors.

    Retries up to ``attempts`` times with exponential backoff (1, 2, 4, …
    capped at 30s); non-transient errors raise immediately. If every attempt
    fails the exception propagates.
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn(**kwargs)
        except Exception as e:  # noqa: BLE001 — classify by message; SDK type is Any
            last_exc = e
            if attempt == attempts - 1 or not _is_transient(e):
                raise
            time.sleep(min(2**attempt, 30))
    # Unreachable: the loop returns on success or raises on the final attempt. The
    # fallback keeps the type non-None (mypy) without asserting (bandit S101).
    raise last_exc or RuntimeError("retry loop exited without a result")  # pragma: no cover
