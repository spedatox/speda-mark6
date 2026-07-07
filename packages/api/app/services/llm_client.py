"""
Unified multi-provider LLM client.

Routing is driven by the model ref string: "provider:model"
(e.g. "openai:gpt-4o", "gemini:gemini-2.5-flash", "ollama:llama3.1:8b").
A bare model name routes to Anthropic, so all existing refs keep working.

Internally the entire backend speaks Anthropic content-block format
exclusively (CLAUDE.md Rule 8) — tool_use / tool_result blocks, stop reasons
end_turn / tool_use / max_tokens / pause_turn. This module translates to and
from each provider's wire format at the request boundary ONLY, and returns
objects with the exact attribute surface of the Anthropic SDK types that
callers already consume (.content blocks, .stop_reason, .usage). Anthropic
calls pass through the existing AnthropicClient untouched, including prompt
caching — zero degradation on the primary path.

OpenAI, Gemini, z.ai (GLM), DeepSeek and Ollama all share one adapter: OpenAI's
own API, Gemini's official OpenAI-compatibility endpoint, z.ai's paas/v4
endpoint, DeepSeek's api.deepseek.com endpoint, and Ollama's /v1 endpoint all
speak the same chat-completions dialect, so a single translation layer covers
them. GLM and DeepSeek-V4 both default to "thinking" mode on and both disable it
via the same extra_body toggle; DeepSeek additionally forces non-thinking
whenever tools are present, because V4 thinking mode is incompatible with the
tool loop (see _to_openai_params).

Fallback: LLM_FALLBACK_CHAIN in .env lists "provider:model" refs tried in
order when a provider call fails (auth, rate limit, connection, 5xx). For
streaming, fallback applies while opening the stream — once tokens are
flowing the response cannot be restarted on another provider.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.config import settings
from app.services.anthropic_client import AnthropicClient

logger = logging.getLogger(__name__)

# Provider name → AsyncOpenAI constructor kwargs. Evaluated lazily so .env
# changes are picked up at first use, not import time.
_OPENAI_COMPAT = {
    "openai": lambda: {"api_key": settings.openai_api_key},
    "gemini": lambda: {
        "api_key": settings.gemini_api_key,
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
    },
    "zai": lambda: {
        "api_key": settings.zai_api_key,
        "base_url": "https://api.z.ai/api/paas/v4/",
    },
    "deepseek": lambda: {
        "api_key": settings.deepseek_api_key,
        "base_url": "https://api.deepseek.com",
    },
    "ollama": lambda: {"api_key": "ollama", "base_url": settings.ollama_base_url},
}
_PROVIDERS = {"anthropic", *_OPENAI_COMPAT}

# OpenAI finish_reason → Anthropic stop_reason. There is no chat-completions
# analogue of pause_turn (that is Anthropic server-tools only).
_FINISH_TO_STOP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
}


def parse_model_ref(ref: str) -> tuple[str, str]:
    """Split "provider:model" → (provider, model). Bare names are Anthropic.
    Only the first segment is checked, so Ollama tags like "llama3.1:8b"
    survive inside "ollama:llama3.1:8b"."""
    provider, sep, rest = ref.partition(":")
    if sep and provider in _PROVIDERS:
        return provider, rest
    return "anthropic", ref


# ── Normalized response types ────────────────────────────────────────────────
# Attribute-compatible with the Anthropic SDK objects callers already consume:
# orchestrator/_blocks_to_dicts (block.type/.text/.id/.name/.input),
# registry._execute_task, memory/history_indexer (response.content[0].text),
# and the orchestrator's usage logging (getattr with defaults).


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class LLMMessage:
    content: list
    stop_reason: str
    usage: Usage = field(default_factory=Usage)


class LLMClient:
    """
    Single entry point for every model call in the backend. Injected into the
    orchestrator and registry at startup via app.state; background services
    construct their own (provider clients are created lazily, so this is cheap).

    Same call signature as the old AnthropicClient — kwargs are Anthropic
    Messages API kwargs (model, system, messages, tools, max_tokens) — so call
    sites only swap the class name.
    """

    def __init__(self) -> None:
        self._anthropic = AnthropicClient()
        self._compat_clients: dict[str, Any] = {}

    def _compat_client(self, provider: str):
        if provider not in self._compat_clients:
            from openai import AsyncOpenAI

            self._compat_clients[provider] = AsyncOpenAI(**_OPENAI_COMPAT[provider]())
        return self._compat_clients[provider]

    def _chain(self, model_ref: str) -> list[tuple[str, str]]:
        """Primary (provider, model) followed by the configured fallbacks."""
        chain = [parse_model_ref(model_ref)]
        for ref in settings.llm_fallback_chain.split(","):
            ref = ref.strip()
            if ref:
                entry = parse_model_ref(ref)
                if entry not in chain:
                    chain.append(entry)
        return chain

    async def create_message(self, **kwargs):
        """
        Non-streaming call. Returns an Anthropic SDK Message (anthropic) or an
        attribute-compatible LLMMessage (other providers).
        """
        last_exc: Exception | None = None
        for provider, model in self._chain(kwargs.get("model", "")):
            try:
                if provider == "anthropic":
                    # `reasoning_effort` is an OpenAI/Gemini hint; the Anthropic
                    # SDK rejects unknown kwargs, so strip it on this path.
                    anthro = {k: v for k, v in kwargs.items() if k != "reasoning_effort"}
                    return await self._anthropic.create_message(
                        **{**anthro, "model": model}
                    )
                return await self._openai_create(provider, model, kwargs)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "llm_provider_failed",
                    extra={"provider": provider, "model": model, "error": str(exc)},
                )
        raise last_exc  # chain exhausted

    def stream_message(self, **kwargs) -> "_StreamHandle":
        """
        Streaming call. Usage is unchanged from AnthropicClient:
            async with client.stream_message(...) as stream:
                async for text in stream.text_stream: ...
                final = await stream.get_final_message()
        """
        return _StreamHandle(self, kwargs)

    async def _openai_create(self, provider: str, model: str, kwargs: dict) -> LLMMessage:
        client = self._compat_client(provider)
        params = _to_openai_params(provider, model, kwargs)
        resp = await client.chat.completions.create(**params)
        choice = resp.choices[0]
        blocks: list = []
        if choice.message.content:
            blocks.append(TextBlock(text=choice.message.content))
        for tc in choice.message.tool_calls or []:
            blocks.append(
                ToolUseBlock(
                    id=tc.id or _gen_tool_id(),
                    name=tc.function.name,
                    input=_parse_tool_args(tc.function.arguments, tc.function.name),
                )
            )
        return LLMMessage(
            content=blocks,
            stop_reason=_FINISH_TO_STOP.get(choice.finish_reason, "end_turn"),
            usage=_usage_from(resp.usage),
        )


# ── Model catalog (GET /models) ──────────────────────────────────────────────
# One entry per selectable model; `id` is the routing ref the frontend sends
# back on /chat. Anthropic ids stay bare for backward compatibility with model
# choices already stored in the UI's localStorage.

_CATALOG = {
    "anthropic": [
        {
            "id": "claude-opus-4-7",
            "name": "Claude Opus 4.7",
            "description": "Most capable — complex reasoning & deep analysis",
            "tags": ["powerful"],
        },
        {
            "id": "claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6",
            "description": "Smart and efficient for most tasks",
            "tags": ["fast", "default"],
        },
        {
            "id": "claude-haiku-4-5-20251001",
            "name": "Claude Haiku 4.5",
            "description": "Fastest — great for simple, quick tasks",
            "tags": ["fastest"],
        },
    ],
    "openai": [
        {
            "id": "openai:gpt-5.1",
            "name": "GPT-5.1",
            "description": "OpenAI flagship — strong reasoning",
            "tags": ["powerful"],
        },
        {
            "id": "openai:gpt-5-mini",
            "name": "GPT-5 Mini",
            "description": "Fast and inexpensive for everyday tasks",
            "tags": ["fast"],
        },
    ],
    "gemini": [
        {
            "id": "gemini:gemini-2.5-pro",
            "name": "Gemini 2.5 Pro",
            "description": "Google's most capable — long context",
            "tags": ["powerful"],
        },
        {
            "id": "gemini:gemini-2.5-flash",
            "name": "Gemini 2.5 Flash",
            "description": "Fast and inexpensive for everyday tasks",
            "tags": ["fast"],
        },
    ],
    "zai": [
        {
            "id": "zai:glm-5.2",
            "name": "GLM-5.2",
            "description": "Zhipu flagship — long-horizon agentic coding, 256K context (glm-5.2[1m] for 1M)",
            "tags": ["powerful", "default"],
        },
        {
            "id": "zai:glm-4.6",
            "name": "GLM-4.6",
            "description": "Prior flagship — strong agentic coding & tool use, 200K context",
            "tags": ["powerful"],
        },
        {
            "id": "zai:glm-4.5-air",
            "name": "GLM-4.5 Air",
            "description": "Lightweight and inexpensive for everyday tasks",
            "tags": ["fast"],
        },
    ],
    "deepseek": [
        {
            "id": "deepseek:deepseek-v4-pro",
            "name": "DeepSeek V4 Pro",
            "description": "DeepSeek flagship — 1M context, agentic tool use (non-thinking for tools)",
            "tags": ["powerful", "default"],
        },
        {
            "id": "deepseek:deepseek-v4-flash",
            "name": "DeepSeek V4 Flash",
            "description": "Fast and very inexpensive — 1M context, aggressive context caching",
            "tags": ["fast"],
        },
    ],
}


# Anthropic model families, in display order (most → least capable). Each entry:
# (substring, tag, description). The live /v1/models list is mapped through this
# so a brand-new Claude id the SDK returns still gets a sensible label/tag/order
# without a code change — the catalog above is only the offline fallback.
_ANTHRO_FAMILY: list[tuple[str, str, str]] = [
    ("fable", "powerful", "Anthropic's most capable — deepest reasoning & long-horizon agentic work"),
    ("opus", "powerful", "Most capable Opus tier — complex reasoning & deep analysis"),
    ("sonnet", "fast", "Balanced speed and intelligence — great for most tasks"),
    ("haiku", "fastest", "Fastest and cheapest — simple, quick tasks"),
]


def _anthropic_meta(model_id: str) -> tuple[list[str], str, int]:
    """(tags, description, family_rank) for a Claude model id. Rank orders the
    picker; unknown families sort last with a generic label."""
    for rank, (key, tag, desc) in enumerate(_ANTHRO_FAMILY):
        if key in model_id:
            return [tag], desc, rank
    return [], "Claude model", len(_ANTHRO_FAMILY)


async def _anthropic_models() -> list[dict]:
    """Live Claude catalog from the Anthropic /v1/models endpoint (SDK
    client.models.list, auto-paginated). Ordered by family then newest-first,
    with the top Sonnet flagged as the default. Raises on failure so the caller
    can fall back to the static catalog."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    rows: list[dict] = []
    async for m in client.models.list():
        mid = m.id
        if not mid.startswith("claude-"):
            continue  # only Claude chat models belong in the picker
        tags, desc, rank = _anthropic_meta(mid)
        created = getattr(m, "created_at", None)
        rows.append({
            "id": mid,                                   # bare id → Anthropic routing
            "name": getattr(m, "display_name", None) or mid,
            "description": desc,
            "tags": tags,
            "provider": "anthropic",
            "_rank": rank,
            "_created": created.isoformat() if created else "",
        })
    # Family order, then newest first within a family.
    rows.sort(key=lambda r: (r["_rank"], r["_created"]), reverse=False)
    rows.sort(key=lambda r: r["_created"], reverse=True)
    rows.sort(key=lambda r: r["_rank"])
    # Mark the first Sonnet as the picker default (matches the profile policy).
    for r in rows:
        if "sonnet" in r["id"]:
            r["tags"] = [*r["tags"], "default"]
            break
    for r in rows:
        r.pop("_rank", None)
        r.pop("_created", None)
    return rows


async def available_models() -> list[dict]:
    """Selectable models across all CONFIGURED providers, for the UI's model
    picker. A provider appears only when usable: Anthropic/OpenAI/Gemini/z.ai/
    DeepSeek when their API key is set, Ollama when the local daemon answers —
    its installed models are listed live from /api/tags (dev/testing only).

    Anthropic and OpenAI are listed LIVE from each provider's models endpoint so
    a newly released model appears without a code change; the static _CATALOG is
    only the offline fallback when the endpoint can't be reached."""
    out: list[dict] = []
    if settings.anthropic_api_key not in ("", "not-set"):
        try:
            anthro = await _anthropic_models()
            out += anthro or [{**m, "provider": "anthropic"} for m in _CATALOG["anthropic"]]
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed_to_fetch_anthropic_models", extra={"error": str(exc)})
            out += [{**m, "provider": "anthropic"} for m in _CATALOG["anthropic"]]
    if settings.openai_api_key:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=settings.openai_api_key)
            models_res = await client.models.list()
            openai_models = []
            for m in models_res.data:
                mid = m.id
                # Filter for chat completion models (gpt-*, o1-*, o3-*, o4-*, etc.)
                is_chat = mid.startswith(("gpt-", "o1-", "o3-", "o4-")) or mid in ("gpt-4", "gpt-3.5-turbo")
                if is_chat and not any(x in mid for x in ("-moderation-", "-embedding-", "-realtime-", "-audio-")):
                    tag = "powerful" if ("gpt-4" in mid or "o1" in mid or "pro" in mid) else "fast"
                    openai_models.append({
                        "id": f"openai:{mid}",
                        "name": mid.replace("-", " ").title().replace("Gpt", "GPT"),
                        "description": f"OpenAI chat model: {mid}",
                        "tags": [tag],
                        "provider": "openai"
                    })
            if openai_models:
                out += sorted(openai_models, key=lambda x: x["name"])
            else:
                out += [{**m, "provider": "openai"} for m in _CATALOG["openai"]]
        except Exception as exc:
            logger.warning("failed_to_fetch_openai_models", extra={"error": str(exc)})
            out += [{**m, "provider": "openai"} for m in _CATALOG["openai"]]
    if settings.gemini_api_key:
        out += [{**m, "provider": "gemini"} for m in _CATALOG["gemini"]]
    if settings.zai_api_key:
        out += [{**m, "provider": "zai"} for m in _CATALOG["zai"]]
    if settings.deepseek_api_key:
        out += [{**m, "provider": "deepseek"} for m in _CATALOG["deepseek"]]

    # Ollama daemon not running is the normal case outside dev — just omit it.
    base = settings.ollama_base_url.rstrip("/").removesuffix("/v1")
    try:
        import httpx

        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f"{base}/api/tags")
            resp.raise_for_status()
            for m in resp.json().get("models", []):
                out.append(
                    {
                        "id": f"ollama:{m['name']}",
                        "name": m["name"],
                        "description": "Local weights — operational with zero uplink",
                        "tags": ["local"],
                        "provider": "ollama",
                    }
                )
    except Exception:
        pass

    return out


# ── Anthropic-format → chat-completions translation ─────────────────────────


def _to_openai_params(provider: str, model: str, kwargs: dict) -> dict:
    msgs: list[dict] = []

    system = kwargs.get("system")
    if isinstance(system, list):
        # Orchestrator system blocks. `_cache` markers are Anthropic prompt-cache
        # hints — meaningless here, just join the text.
        system = "\n\n".join(b.get("text", "") for b in system)
    if system:
        msgs.append({"role": "system", "content": system})

    for m in kwargs.get("messages", []):
        msgs.extend(_translate_message(m))

    params: dict = {"model": model, "messages": msgs}

    tools = kwargs.get("tools")
    if tools:
        params["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object"}),
                },
            }
            for t in tools
        ]

    max_tokens = kwargs.get("max_tokens")
    if max_tokens:
        # OpenAI deprecated max_tokens (reasoning models reject it); Gemini's
        # compat layer and Ollama still expect it.
        if provider == "openai":
            params["max_completion_tokens"] = max_tokens
        else:
            params["max_tokens"] = max_tokens

    # Reasoning-effort hint — lets short background tasks (e.g. title generation)
    # work on reasoning models. Without it, a GPT-5 / Gemini-2.5 model spends its
    # whole token budget on hidden reasoning and returns EMPTY visible content,
    # which silently broke conversation titles on every non-Anthropic provider.
    # Only openai and gemini document this param; Ollama (local, non-reasoning)
    # is left alone.
    reasoning_effort = kwargs.get("reasoning_effort")
    if reasoning_effort and provider in ("openai", "gemini"):
        params["reasoning_effort"] = reasoning_effort

    # z.ai GLM thinking control. GLM defaults thinking ON, so a short background
    # task (title generation caps output at ~512 tokens) burns the whole budget
    # on hidden reasoning and returns EMPTY visible content — the same failure
    # mode the hint above fixes for GPT-5/Gemini. Every GLM generation (4.x and
    # 5.2) accepts the explicit `thinking:{type}` toggle, so we route through it
    # rather than `reasoning_effort`: GLM-4.x has no reasoning_effort param at
    # all, and GLM-5.2's only accepts "high"/"max" (it would reject "minimal").
    # Map a low/minimal effort hint to disabled thinking; leave it default
    # otherwise so interactive chat keeps full reasoning quality. extra_body is
    # how the OpenAI SDK forwards a non-OpenAI request field.
    if provider == "zai" and reasoning_effort in ("minimal", "low", "none"):
        params["extra_body"] = {"thinking": {"type": "disabled"}}

    # DeepSeek-V4 thinking control. V4 enables thinking BY DEFAULT (effort
    # "high", auto-escalating to "max" for agent requests), and thinking mode is
    # fundamentally incompatible with our agentic tool loop: V4 rejects
    # tool_choice in thinking mode and makes `reasoning_content` a REQUIRED
    # protocol field once tool_use enters the history — which our multi-turn
    # loop does not round-trip (we intentionally drop chain-of-thought). So
    # whenever tools are in play — i.e. the entire main loop — force non-thinking
    # via the same extra_body toggle GLM uses; there tool calls, tool_choice and
    # JSON all work. Also force it for a low/minimal background hint so a short
    # task isn't starved by hidden reasoning. Only a genuine high/max hint on a
    # TOOL-FREE call (e.g. a pure reasoning sub-task) keeps thinking on; V4's
    # reasoning_effort accepts only "high"/"max", so a lesser value is never
    # forwarded as one.
    if provider == "deepseek":
        if tools or reasoning_effort in ("minimal", "low", "none"):
            params["extra_body"] = {"thinking": {"type": "disabled"}}
        elif reasoning_effort in ("high", "max"):
            params["reasoning_effort"] = reasoning_effort

    return params


def _translate_message(message: dict) -> list[dict]:
    """One Anthropic-format message → one or more chat-completions messages.
    tool_result blocks become role:"tool" messages, which must directly follow
    the assistant message carrying the matching tool_calls — Anthropic's
    history puts them in the very next user message, so emitting them first
    preserves that adjacency."""
    role = message["role"]
    content = message.get("content")
    if isinstance(content, str):
        return [{"role": role, "content": content}]

    tool_msgs: list[dict] = []
    user_parts: list[dict] = []
    assistant_text: list[str] = []
    tool_calls: list[dict] = []

    for block in content or []:
        btype = block.get("type")
        if btype == "text":
            if role == "assistant":
                assistant_text.append(block.get("text", ""))
            else:
                user_parts.append({"type": "text", "text": block.get("text", "")})
        elif btype == "image":
            src = block.get("source", {})
            if src.get("type") == "base64" and src.get("data"):
                user_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{src.get('media_type', 'image/png')};"
                            f"base64,{src['data']}"
                        },
                    }
                )
        elif btype == "tool_use":
            tool_calls.append(
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                }
            )
        elif btype == "tool_result":
            rc = block.get("content", "")
            if isinstance(rc, list):
                rc = "\n".join(
                    p.get("text", "") for p in rc if isinstance(p, dict)
                )
            tool_msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": rc if isinstance(rc, str) else str(rc),
                }
            )

    out: list[dict] = []
    if role == "assistant":
        text = "\n".join(assistant_text)
        if tool_calls:
            # Assistant tool-call message. `content` must be a STRING here, never
            # null: z.ai GLM rejects `content: null` with error 1214 ("messages
            # parameter is illegal"), which breaks the agent loop the moment any
            # tool is used. An empty string is valid for every OpenAI-compatible
            # provider when tool_calls are present, so normalize to "" here.
            out.append({"role": "assistant", "content": text, "tool_calls": tool_calls})
        elif text:
            out.append({"role": "assistant", "content": text})
    else:
        out.extend(tool_msgs)
        if user_parts:
            if all(p["type"] == "text" for p in user_parts):
                out.append(
                    {"role": role, "content": "\n\n".join(p["text"] for p in user_parts)}
                )
            else:
                out.append({"role": role, "content": user_parts})
    return out


def _parse_tool_args(raw: str | None, tool_name: str) -> dict:
    # The Anthropic SDK hands tool input pre-parsed; chat-completions providers
    # send a JSON string, which open-weight models occasionally truncate.
    try:
        parsed = json.loads(raw) if raw else {}
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, ValueError):
        logger.warning("llm_tool_args_unparseable", extra={"tool": tool_name})
        return {}


def _gen_tool_id() -> str:
    # Gemini's compat layer can omit tool-call ids; the internal format requires
    # one to pair tool_use with tool_result. Generated, never hardcoded.
    return f"call_{uuid.uuid4().hex[:24]}"


def _usage_from(u) -> Usage:
    if u is None:
        return Usage()
    details = getattr(u, "prompt_tokens_details", None)
    # OpenAI/Gemini report cache reads under prompt_tokens_details.cached_tokens;
    # DeepSeek (whose cache is a headline cost feature) reports them directly on
    # the usage object as prompt_cache_hit_tokens. Prefer whichever is present.
    cached = getattr(details, "cached_tokens", 0) or 0
    cached = cached or (getattr(u, "prompt_cache_hit_tokens", 0) or 0)
    return Usage(
        input_tokens=getattr(u, "prompt_tokens", 0) or 0,
        output_tokens=getattr(u, "completion_tokens", 0) or 0,
        cache_read_input_tokens=cached,
    )


# ── Streaming ────────────────────────────────────────────────────────────────


class _OpenAICompatStream:
    """Chat-completions stream exposing the Anthropic stream surface the
    orchestrator consumes: .text_stream and get_final_message()."""

    def __init__(self, client, params: dict, provider: str) -> None:
        self._client = client
        self._params = params
        self._provider = provider
        self._raw = None
        self._text: list[str] = []
        self._tool_calls: dict[int, dict] = {}
        self._finish: str | None = None
        self._usage = Usage()
        self._consumed = False

    async def open(self) -> None:
        params = dict(self._params, stream=True)
        if self._provider != "gemini":  # Gemini's compat layer rejects it
            params["stream_options"] = {"include_usage": True}
        self._raw = await self._client.chat.completions.create(**params)

    @property
    def text_stream(self) -> AsyncIterator[str]:
        return self._consume()

    async def _consume(self) -> AsyncIterator[str]:
        async for chunk in self._raw:
            if getattr(chunk, "usage", None):
                self._usage = _usage_from(chunk.usage)
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                self._finish = choice.finish_reason
            delta = choice.delta
            if delta is None:
                continue
            if delta.content:
                self._text.append(delta.content)
                yield delta.content
            for tc in delta.tool_calls or []:
                acc = self._tool_calls.setdefault(
                    tc.index, {"id": "", "name": "", "arguments": ""}
                )
                if tc.id:
                    acc["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        acc["name"] = tc.function.name
                    if tc.function.arguments:
                        acc["arguments"] += tc.function.arguments
        self._consumed = True

    async def get_final_message(self) -> LLMMessage:
        if not self._consumed:  # drain remainder if text_stream wasn't finished
            async for _ in self._consume():
                pass
        blocks: list = []
        text = "".join(self._text)
        if text:
            blocks.append(TextBlock(text=text))
        for idx in sorted(self._tool_calls):
            acc = self._tool_calls[idx]
            blocks.append(
                ToolUseBlock(
                    id=acc["id"] or _gen_tool_id(),
                    name=acc["name"],
                    input=_parse_tool_args(acc["arguments"], acc["name"]),
                )
            )
        return LLMMessage(
            content=blocks,
            stop_reason=_FINISH_TO_STOP.get(self._finish, "end_turn"),
            usage=self._usage,
        )

    async def aclose(self) -> None:
        if self._raw is not None:
            await self._raw.close()


class _StreamHandle:
    """Async context manager returned by LLMClient.stream_message().

    On enter, tries each (provider, model) in the chain until one opens a
    stream; opening sends the request, so auth/rate-limit/connection failures
    surface here and trigger fallback. Yields either the raw Anthropic SDK
    stream (pass-through) or an _OpenAICompatStream — same attribute surface.
    """

    def __init__(self, owner: LLMClient, kwargs: dict) -> None:
        self._owner = owner
        self._kwargs = kwargs
        self._anthropic_cm = None
        self._compat_stream: _OpenAICompatStream | None = None

    async def __aenter__(self):
        last_exc: Exception | None = None
        for provider, model in self._owner._chain(self._kwargs.get("model", "")):
            try:
                if provider == "anthropic":
                    cm = self._owner._anthropic.stream_message(
                        **{**self._kwargs, "model": model}
                    )
                    stream = await cm.__aenter__()
                    self._anthropic_cm = cm
                    return stream
                stream = _OpenAICompatStream(
                    self._owner._compat_client(provider),
                    _to_openai_params(provider, model, self._kwargs),
                    provider,
                )
                await stream.open()
                self._compat_stream = stream
                return stream
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "llm_provider_failed",
                    extra={"provider": provider, "model": model, "error": str(exc)},
                )
        raise last_exc  # chain exhausted

    async def __aexit__(self, exc_type, exc, tb):
        if self._anthropic_cm is not None:
            return await self._anthropic_cm.__aexit__(exc_type, exc, tb)
        if self._compat_stream is not None:
            await self._compat_stream.aclose()
        return False
