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

One exception to that shared path: OpenAI's gpt-5.6+ family cannot carry
function tools on chat-completions at all, so those calls (and only those) are
translated to the Responses API dialect instead — see
_openai_tools_need_responses_api and _to_responses_params.

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
    "nvidia": lambda: {
        "api_key": settings.nvidia_api_key,
        "base_url": "https://integrate.api.nvidia.com/v1",
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


# OpenAI codenames of the gpt-5.6 reasoning family (gpt-5.6-luna/sol/terra).
_GPT56_CODENAMES = ("luna", "sol", "terra")


def _openai_tools_need_responses_api(model_l: str) -> bool:
    """True for OpenAI reasoning models (gpt-5.6 and newer) whose function tools
    must be sent to /v1/responses instead of /v1/chat/completions.

    History: during the 5.6 preview, chat-completions accepted tools as long as
    reasoning_effort was forced to 'none'. After the July 9 2026 GA that stopped
    being reliable — the same key/model/payload fails roughly every other call
    with a MISLEADING 401 "insufficient permissions" (luna) or a clean 400
    (sol/terra), then succeeds on retry. OpenAI's own error text names the fix:
    "To use function tools, use /v1/responses". So we route there and drop the
    reasoning_effort hack entirely — the Responses API carries tool definitions
    natively with no reasoning constraint.

    The older gpt-5 generation (gpt-5 / mini / nano) is unaffected and stays on
    chat-completions. Matched by version number so future releases (5.7, 6.x, …)
    are covered automatically."""
    import re

    if any(c in model_l for c in _GPT56_CODENAMES):
        return True
    m = re.search(r"gpt-(\d+(?:\.\d+)?)", model_l)
    if m:
        try:
            return float(m.group(1)) >= 5.6
        except ValueError:
            return False
    return False


def _use_responses_api(provider: str, model: str, kwargs: dict) -> bool:
    """Whether this specific call must go to OpenAI's /v1/responses endpoint.
    Only ever true for OpenAI + a 5.6-family model + tools in the request;
    tool-free calls (title/recap generation) work fine on chat-completions and
    stay there so the rest of the stack is untouched."""
    return (
        provider == "openai"
        and bool(kwargs.get("tools"))
        and _openai_tools_need_responses_api(model.lower())
    )


# ── Normalized response types ────────────────────────────────────────────────
# Attribute-compatible with the Anthropic SDK objects callers already consume:
# blocks_to_dicts (block.type/.text/.id/.name/.input), the Legion worker
# loop, memory/history_indexer (response.content[0].text),
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
    # Gemini 3.x thought signature — opaque, provider-specific, and MANDATORY to
    # echo back on the next request (see _thought_signature). None everywhere else.
    signature: str | None = None


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


def blocks_to_dicts(content_blocks) -> list[dict]:
    """Convert content blocks (Anthropic SDK objects or the dataclasses above —
    both expose .type/.text/.id/.name/.input) to plain dicts for message
    history. Shared by the orchestrator loop and the Legion worker loop."""
    result = []
    for block in content_blocks:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            entry = {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            }
            # Underscore-prefixed = internal marker, stripped at every wire
            # boundary (same convention as the `_cache` hint on system blocks).
            # Carries Gemini's thought signature across the loop's iterations;
            # absent for every other provider.
            signature = getattr(block, "signature", None)
            if signature:
                entry["_signature"] = signature
            result.append(entry)
        else:
            # Pass through unknown block types as-is
            try:
                result.append(block.model_dump())
            except Exception:
                result.append({"type": block.type})
    return result


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
                    # `reasoning_effort` is an OpenAI/Gemini hint and `cache_key`
                    # an OpenAI cache-routing hint; the Anthropic SDK rejects
                    # unknown kwargs, so strip both on this path (Anthropic does
                    # its caching through explicit cache_control breakpoints).
                    anthro = {
                        k: v for k, v in kwargs.items()
                        if k not in ("reasoning_effort", "cache_key")
                    }
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
        if _use_responses_api(provider, model, kwargs):
            resp = await client.responses.create(**_to_responses_params(model, kwargs))
            return _responses_to_message(resp)
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
                    signature=_thought_signature(tc),
                )
            )
        return LLMMessage(
            content=blocks,
            stop_reason=_stop_reason_for(choice.finish_reason, blocks),
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
            "id": "gemini:gemini-3.6-flash",
            "name": "Gemini 3.6 Flash",
            "description": "Google's current flagship Flash — long context",
            "tags": ["powerful"],
        },
        {
            "id": "gemini:gemini-3.5-flash-lite",
            "name": "Gemini 3.5 Flash-Lite",
            "description": "Fastest and cheapest — high-volume simple tasks",
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


# Substrings marking a listed model as NOT a chat model. Every OpenAI-compatible
# /models endpoint mixes embeddings, rerankers, media generation and speech in
# with the chat models; none of those belong in a chat model picker.
_NON_CHAT_MARKERS = (
    "embed", "rerank", "retriev", "moderation", "aqa",
    "imagen", "veo", "image-generation", "image-preview",
    "tts", "audio", "whisper", "-live-", "translation",
)

# Provider model families, in display order (most → least capable), same shape
# as _ANTHRO_FAMILY: (id substring, tag, description). A live id we've never
# seen still lands with a sensible label and sort position; more specific keys
# must come first ("flash-lite" before "flash").
_ZAI_FAMILY: list[tuple[str, str, str]] = [
    ("glm-5", "powerful", "Zhipu flagship — long-horizon agentic coding, very long context"),
    ("glm-4.6", "powerful", "Prior flagship — strong agentic coding & tool use"),
    ("air", "fast", "Lightweight and inexpensive for everyday tasks"),
    ("flash", "fast", "Fastest GLM tier — quick, cheap turns"),
    ("glm-4", "", "GLM chat model"),
]
_GEMINI_FAMILY: list[tuple[str, str, str]] = [
    ("pro", "powerful", "Google's most capable — long context"),
    ("flash-lite", "fastest", "Cheapest Gemini tier — high-volume simple tasks"),
    ("flash", "fast", "Fast and inexpensive for everyday tasks"),
    ("gemma", "fast", "Open-weight Google model"),
]


def _curated(provider: str, model_ref: str) -> dict | None:
    """The hand-written _CATALOG entry for a model ref, if there is one. A live
    listing reuses our copy rather than a machine-generated line."""
    for m in _CATALOG.get(provider, []):
        if m["id"] == model_ref:
            return m
    return None


async def _compat_models(
    provider: str, family: list[tuple[str, str, str]]
) -> list[dict]:
    """Live catalog from a provider's OpenAI-compatible /models endpoint.

    Everything the key can actually reach is listed, so a newly released model
    shows up in the picker with no code change; `family` only supplies the
    label/tag/sort for ids we don't have curated copy for. Raises on failure so
    the caller can fall back to the static catalog."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(**_OPENAI_COMPAT[provider]())
    res = await client.models.list()
    rows: list[dict] = []
    for m in res.data:
        # Gemini's OpenAI bridge returns "models/gemini-2.5-pro" — strip the path
        # so the ref matches what the chat client sends back.
        mid = m.id.split("/")[-1]
        low = mid.lower()
        if any(x in low for x in _NON_CHAT_MARKERS):
            continue
        ref = f"{provider}:{mid}"
        rank, tag, desc = len(family), "", f"{provider} chat model"
        for i, (key, t, d) in enumerate(family):
            if key in low:
                rank, tag, desc = i, t, d
                break
        known = _curated(provider, ref)
        rows.append({
            "id": ref,
            "name": known["name"] if known else mid,
            "description": (known["description"] if known else desc),
            "tags": list(known["tags"]) if known else ([tag] if tag else []),
            "provider": provider,
            "_rank": rank,
        })
    rows.sort(key=lambda r: (r["_rank"], r["id"]))
    for r in rows:
        r.pop("_rank", None)
    return rows


async def _live_or_static(provider: str, family: list[tuple[str, str, str]]) -> list[dict]:
    """Live listing for `provider`, falling back to the static catalog when the
    endpoint is unreachable, unsupported, or returns nothing usable."""
    try:
        rows = await _compat_models(provider, family)
        if rows:
            return rows
        logger.warning("provider_models_empty", extra={"provider": provider})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "failed_to_fetch_provider_models",
            extra={"provider": provider, "error": str(exc)},
        )
    return [{**m, "provider": provider} for m in _CATALOG[provider]]


async def _nvidia_models() -> list[dict]:
    """Live NVIDIA NIM catalog from its OpenAI-compatible /v1/models endpoint.
    Lists every model the key can reach (Llama, Nemotron, DeepSeek, Qwen, …),
    dropping non-chat entries (embeddings / rerankers). Raises on failure so the
    caller falls back to the static catalog. Model ids keep their vendor prefix
    (e.g. meta/llama-3.1-405b-instruct); the "nvidia:" ref routes them here."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(**_OPENAI_COMPAT["nvidia"]())
    rows: list[dict] = []
    res = await client.models.list()
    for m in res.data:
        mid = m.id
        low = mid.lower()
        if any(x in low for x in ("embed", "rerank", "retriev")):
            continue  # not chat-completion models
        # Light heuristic: large / reasoning models read as "powerful", the small
        # ones as "fast"; everything else stays untagged.
        if any(x in low for x in ("405b", "253b", "70b", "-r1", "nemotron", "reason", "70b")):
            tags = ["powerful"]
        elif any(x in low for x in ("8b", "9b", "mini", "small", "nano", "4b", "2b")):
            tags = ["fast"]
        else:
            tags = []
        rows.append({
            "id": f"nvidia:{mid}",
            "name": mid.split("/")[-1].replace("-", " ").title(),
            "description": f"NVIDIA NIM · {mid}",
            "tags": tags,
            "provider": "nvidia",
        })
    return sorted(rows, key=lambda r: r["name"])


async def available_models() -> list[dict]:
    """Selectable models across all CONFIGURED providers, for the UI's model
    picker. A provider appears only when usable: Anthropic/OpenAI/Gemini/z.ai/
    DeepSeek when their API key is set, Ollama when the local daemon answers —
    its installed models are listed live from /api/tags (dev/testing only).

    Anthropic, OpenAI, Gemini, z.ai and NVIDIA are all listed LIVE from each
    provider's models endpoint so a newly released model appears without a code
    change; the static _CATALOG is only the offline fallback for when an
    endpoint can't be reached (or doesn't offer a listing at all)."""
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
        out += await _live_or_static("gemini", _GEMINI_FAMILY)
    if settings.zai_api_key:
        out += await _live_or_static("zai", _ZAI_FAMILY)
    if settings.deepseek_api_key:
        out += [{**m, "provider": "deepseek"} for m in _CATALOG["deepseek"]]
    if settings.nvidia_api_key:
        try:
            out += await _nvidia_models()
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed_to_fetch_nvidia_models", extra={"error": str(exc)})

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
        # Orchestrator system blocks joined in order. There is no `cache_control`
        # equivalent to carry over: z.ai, DeepSeek, Gemini and OpenAI all cache
        # IMPLICITLY — they match the longest identical prefix themselves and
        # bill the hit at a fraction of input, with no request field to set. So
        # the `_cache` markers are dropped, and what actually earns the discount
        # on this path is that the joined bytes are stable turn to turn: the
        # blocks arrive in a fixed order, the clock lives in per-message stamps
        # (SessionManager.stamp_user_content), and resolved tools are appended
        # rather than spliced in. Reordering these blocks would silently cost
        # real money on every provider here.
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

    # OpenAI's one explicit caching control: a routing key that keeps requests
    # sharing a prefix on the same cache. Implicit matching alone spreads a
    # conversation across backends and misses; the key pins it. Scoped per
    # session (OpenAI's own recommendation is a session-shaped key) and sent
    # only where it exists — no other provider on this path accepts it.
    cache_key = kwargs.get("cache_key")
    if provider == "openai" and cache_key:
        params.setdefault("extra_body", {})["prompt_cache_key"] = str(cache_key)

    # Reasoning-effort hint — lets short background tasks (e.g. title generation)
    # work on reasoning models. Without it, a GPT-5 / Gemini-2.5 model spends its
    # whole token budget on hidden reasoning and returns EMPTY visible content,
    # which silently broke conversation titles on every non-Anthropic provider.
    # Only openai and gemini document this param; Ollama (local, non-reasoning)
    # is left alone.
    # Passed via extra_body, NOT as a top-level kwarg: the pinned openai SDK
    # rejects `reasoning_effort` as a typed argument, but forwards extra_body
    # straight into the request JSON on every SDK version (same mechanism the
    # zai/deepseek thinking toggles below use).
    reasoning_effort = kwargs.get("reasoning_effort")
    if provider == "openai":
        model_l = model.lower()
        is_reasoning_model = any(x in model_l for x in ("o1", "o3", "o4", "gpt-5", "terra"))
        effort = None
        # NOTE: gpt-5.6+ tool calls never reach this function — _use_responses_api
        # routes them to /v1/responses, where tools need no reasoning override.
        # The old `reasoning_effort: "none"` workaround lived here and is gone:
        # post-GA it was honoured intermittently, which is exactly what produced
        # the every-other-message 401. Do not reintroduce it — the OLDER gpt-5
        # generation (gpt-5 / mini / nano) outright REJECTS the value 'none'.
        if reasoning_effort and is_reasoning_model and not tools:
            # Tool-free reasoning call (e.g. background title/recap generation):
            # pass the caller's hint so the model doesn't burn its whole budget
            # on hidden reasoning and return empty content.
            effort = reasoning_effort
        if effort:
            params.setdefault("extra_body", {})["reasoning_effort"] = effort
    elif provider == "gemini" and reasoning_effort:
        params.setdefault("extra_body", {})["reasoning_effort"] = reasoning_effort

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
            call = {
                "id": block["id"],
                "type": "function",
                "function": {
                    "name": block["name"],
                    "arguments": json.dumps(block.get("input") or {}),
                },
            }
            # Hand Gemini back its own thought signature on the call it signed.
            # Mandatory on Gemini 3.x (see _thought_signature); every other
            # provider never set one, so nothing is added for them.
            signature = block.get("_signature")
            if signature:
                call["extra_content"] = {"google": {"thought_signature": signature}}
            tool_calls.append(call)
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


# ── Anthropic-format → Responses API translation (OpenAI gpt-5.6+ tools) ─────
# Same job as _to_openai_params above, different dialect. The Responses API is a
# flat list of typed `input` items rather than chat messages: tool calls are
# `function_call` items and tool results are `function_call_output` items paired
# by `call_id` (NOT nested inside role messages), tools are declared flat
# (no {"type":"function","function":{…}} envelope), the system prompt rides
# `instructions`, and the budget parameter is `max_output_tokens`.


def _to_responses_params(model: str, kwargs: dict) -> dict:
    items: list[dict] = []
    for m in kwargs.get("messages", []):
        items.extend(_translate_message_responses(m))

    params: dict = {"model": model, "input": items}

    system = kwargs.get("system")
    if isinstance(system, list):
        # `_cache` markers are Anthropic prompt-cache hints — meaningless here.
        system = "\n\n".join(b.get("text", "") for b in system)
    if system:
        params["instructions"] = system

    tools = kwargs.get("tools")
    if tools:
        params["tools"] = [
            {
                "type": "function",
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object"}),
            }
            for t in tools
        ]

    max_tokens = kwargs.get("max_tokens")
    if max_tokens:
        params["max_output_tokens"] = max_tokens

    # Same cache-routing key as the chat-completions path — the Responses API
    # takes it natively rather than through extra_body.
    cache_key = kwargs.get("cache_key")
    if cache_key:
        params["prompt_cache_key"] = str(cache_key)

    # Reasoning is native here and needs no override for tools to work. Only a
    # caller's explicit hint is forwarded, and never "none" — on this endpoint
    # the field is an enum of minimal/low/medium/high.
    effort = kwargs.get("reasoning_effort")
    if effort and effort != "none":
        params["reasoning"] = {"effort": effort}

    return params


def _translate_message_responses(message: dict) -> list[dict]:
    """One Anthropic-format message → one or more Responses API input items.
    tool_result blocks become standalone `function_call_output` items, emitted
    before the user text they arrive with — mirroring _translate_message's
    ordering so the call/output pairing stays adjacent."""
    role = message["role"]
    content = message.get("content")
    if isinstance(content, str):
        ctype = "output_text" if role == "assistant" else "input_text"
        return [{"role": role, "content": [{"type": ctype, "text": content}]}]

    outputs: list[dict] = []
    calls: list[dict] = []
    parts: list[dict] = []

    for block in content or []:
        btype = block.get("type")
        if btype == "text":
            parts.append(
                {
                    "type": "output_text" if role == "assistant" else "input_text",
                    "text": block.get("text", ""),
                }
            )
        elif btype == "image":
            src = block.get("source", {})
            if src.get("type") == "base64" and src.get("data"):
                parts.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:{src.get('media_type', 'image/png')};"
                        f"base64,{src['data']}",
                    }
                )
        elif btype == "tool_use":
            calls.append(
                {
                    "type": "function_call",
                    "call_id": block["id"],
                    "name": block["name"],
                    "arguments": json.dumps(block.get("input") or {}),
                }
            )
        elif btype == "tool_result":
            rc = block.get("content", "")
            if isinstance(rc, list):
                rc = "\n".join(p.get("text", "") for p in rc if isinstance(p, dict))
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": block.get("tool_use_id", ""),
                    "output": rc if isinstance(rc, str) else str(rc),
                }
            )

    out: list[dict] = []
    if role == "assistant":
        if parts:
            out.append({"role": "assistant", "content": parts})
        out.extend(calls)
    else:
        out.extend(outputs)
        if parts:
            out.append({"role": role, "content": parts})
    return out


def _responses_to_message(resp) -> LLMMessage:
    """Responses API result → the Anthropic-shaped LLMMessage callers expect."""
    texts: list[str] = []
    tool_blocks: list[ToolUseBlock] = []
    for item in getattr(resp, "output", None) or []:
        itype = getattr(item, "type", None)
        if itype == "message":
            for c in getattr(item, "content", None) or []:
                if getattr(c, "type", None) == "output_text" and getattr(c, "text", None):
                    texts.append(c.text)
        elif itype == "function_call":
            tool_blocks.append(
                ToolUseBlock(
                    # call_id is the handle the API pairs function_call_output
                    # against — item.id is a different, unusable identifier.
                    id=getattr(item, "call_id", None) or _gen_tool_id(),
                    name=getattr(item, "name", "") or "",
                    input=_parse_tool_args(
                        getattr(item, "arguments", None), getattr(item, "name", "") or ""
                    ),
                )
            )

    blocks: list = []
    text = "".join(texts)
    if text:
        blocks.append(TextBlock(text=text))
    blocks.extend(tool_blocks)

    if tool_blocks:
        stop_reason = "tool_use"
    elif getattr(getattr(resp, "incomplete_details", None), "reason", None) == "max_output_tokens":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end_turn"

    return LLMMessage(
        content=blocks, stop_reason=stop_reason, usage=_usage_from_responses(resp.usage)
    )


def _usage_from_responses(u) -> Usage:
    if u is None:
        return Usage()
    details = getattr(u, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    prompt = getattr(u, "input_tokens", 0) or 0
    # Same inclusive-vs-exclusive split as _usage_from below: the Responses API
    # reports input_tokens INCLUSIVE of the cached prefix, Anthropic reports it
    # exclusive. Subtract so `input_tokens` means the same thing — tokens billed
    # at full rate — on every path the orchestrator sums.
    return Usage(
        input_tokens=max(0, prompt - cached),
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_input_tokens=cached,
    )


def _parse_tool_args(raw: str | None, tool_name: str) -> dict:
    # The Anthropic SDK hands tool input pre-parsed; chat-completions providers
    # send a JSON string, which open-weight models occasionally truncate.
    try:
        parsed = json.loads(raw) if raw else {}
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, ValueError):
        logger.warning("llm_tool_args_unparseable", extra={"tool": tool_name})
        return {}


def _thought_signature(tc) -> str | None:
    """Pull Gemini's thought signature off a returned tool call.

    Gemini 3.x attaches an opaque signature of its own reasoning to each
    functionCall part and REQUIRES it back, verbatim, on the request that
    carries the tool result. Drop it and the follow-up call dies with
    400 INVALID_ARGUMENT "Function call is missing a thought_signature in
    functionCall parts" — i.e. the tool runs, then the turn that was supposed
    to say something about the result never happens.

    On the OpenAI-compat endpoint it rides a non-standard field on the tool
    call itself, `extra_content.google.thought_signature` (Google's take on the
    Realtime API extension pattern). The OpenAI SDK has no model for it, so
    depending on version it lands either as a stray attribute or in
    `model_extra` — read both. Only the FIRST of a set of parallel calls is
    signed; the rest legitimately have none, so absence is never an error.

    https://ai.google.dev/gemini-api/docs/thinking#signatures
    """
    extra = getattr(tc, "extra_content", None)
    if extra is None:
        extra = (getattr(tc, "model_extra", None) or {}).get("extra_content")
    google = extra.get("google") if isinstance(extra, dict) else getattr(extra, "google", None)
    if isinstance(google, dict):
        return google.get("thought_signature")
    return getattr(google, "thought_signature", None)


def _gen_tool_id() -> str:
    # Gemini's compat layer can omit tool-call ids; the internal format requires
    # one to pair tool_use with tool_result. Generated, never hardcoded.
    return f"call_{uuid.uuid4().hex[:24]}"


def _stop_reason_for(finish: str | None, blocks: list) -> str:
    """finish_reason → Anthropic stop_reason, with the assembled blocks winning
    over the reported reason whenever a tool was requested.

    Gemini's compat bridge emits tool_calls while reporting finish_reason
    "stop" — and on the streaming path frequently reports none at all. Mapping
    that verbatim yields end_turn, so the orchestrator breaks out of the loop at
    the exact moment the model asked for a tool: the tool never runs, and the
    turn ends holding a tool_use block with no text. That renders as an empty
    answer, and because the turn produced no chunks and no TOOL event, the
    runner has nothing to persist — the reloaded transcript loses it entirely.
    The blocks are the ground truth: if the model asked for a tool, the stop
    reason IS tool_use. Same guard covers Ollama, which has the identical quirk."""
    if any(isinstance(b, ToolUseBlock) for b in blocks):
        return "tool_use"
    return _FINISH_TO_STOP.get((finish or "").lower(), "end_turn")


def _usage_from(u) -> Usage:
    if u is None:
        return Usage()
    details = getattr(u, "prompt_tokens_details", None)
    # OpenAI/Gemini report cache reads under prompt_tokens_details.cached_tokens;
    # DeepSeek (whose cache is a headline cost feature) reports them directly on
    # the usage object as prompt_cache_hit_tokens. Prefer whichever is present.
    cached = getattr(details, "cached_tokens", 0) or 0
    cached = cached or (getattr(u, "prompt_cache_hit_tokens", 0) or 0)

    prompt = getattr(u, "prompt_tokens", 0) or 0
    completion = getattr(u, "completion_tokens", 0) or 0
    # Hidden reasoning is BILLED as output but is not in completion_tokens on
    # every provider: Gemini 2.5/3.x returns e.g. prompt=8 completion=7
    # total=204, the 189-token gap being thinking. Taking completion_tokens at
    # face value there would under-report output by ~30x. The total is the
    # provider's own arithmetic, so trust whichever is larger.
    total = getattr(u, "total_tokens", 0) or 0
    if total > prompt + completion:
        completion = total - prompt
    # Chat-completions providers report prompt_tokens INCLUSIVE of the cached
    # prefix; Anthropic reports input_tokens EXCLUSIVE of it. The orchestrator
    # sums input + cache_read + cache_write into one "what this turn read"
    # figure, so leaving prompt inclusive double-counted every cached token on
    # every OpenAI-compatible provider — the more the cache worked, the more the
    # counter overstated. Subtract here so both conventions mean the same thing.
    return Usage(
        input_tokens=max(0, prompt - cached),
        output_tokens=completion,
        cache_read_input_tokens=cached,
    )


# ── Streaming ────────────────────────────────────────────────────────────────

_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def _partial_tail(text: str, tag: str) -> int:
    """Length of the longest suffix of `text` that is a proper prefix of `tag` —
    i.e. how much of the buffer might still turn out to be a split tag."""
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:n]):
            return n
    return 0


class _ReasoningFilter:
    """Keeps a model's thinking out of its answer.

    Reasoning reaches us on two different channels and only one of them is
    safe. Providers that follow DeepSeek's convention put it on its own
    `reasoning_content` delta field, which we simply never read into the text.
    Others — GLM, Ollama, and anything proxied through a generic OpenAI-compat
    gateway — inline it into `content` wrapped in `<think>…</think>`, where it
    is indistinguishable from the answer unless someone strips it. Persisted, it
    becomes conversation history, so the next turn reads the model's own
    scratchpad back as dialogue.

    Streaming makes this stateful: a tag can be split across chunk boundaries
    ("<thi" / "nk>"), so the filter holds back any tail that could still become
    one and releases it once it cannot.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False
        self._emitted = False
        self.reasoning: list[str] = []

    def feed(self, delta: str) -> str:
        """Visible text from this delta. Thinking is diverted to .reasoning."""
        self._buf += delta
        out: list[str] = []
        while True:
            if self._inside:
                idx = self._buf.find(_THINK_CLOSE)
                if idx == -1:
                    keep = _partial_tail(self._buf, _THINK_CLOSE)
                    cut = len(self._buf) - keep
                    self.reasoning.append(self._buf[:cut])
                    self._buf = self._buf[cut:]
                    break
                self.reasoning.append(self._buf[:idx])
                self._buf = self._buf[idx + len(_THINK_CLOSE):]
                self._inside = False
            else:
                idx = self._buf.find(_THINK_OPEN)
                if idx == -1:
                    keep = _partial_tail(self._buf, _THINK_OPEN)
                    cut = len(self._buf) - keep
                    out.append(self._buf[:cut])
                    self._buf = self._buf[cut:]
                    break
                out.append(self._buf[:idx])
                self._buf = self._buf[idx + len(_THINK_OPEN):]
                self._inside = True
        text = "".join(out)
        if text:
            self._emitted = True
        return text

    def flush(self) -> str:
        """Whatever is still held back at end of stream.

        An unterminated `<think>` means the model was cut off mid-thought, so
        there is no answer to deliver. If it never produced any visible text at
        all, the held-back remainder is released anyway and logged: a truncated
        turn is a failure either way, and one that arrives visibly wrong is far
        easier to diagnose than one that silently delivers nothing.
        """
        rest, self._buf = self._buf, ""
        if not self._inside:
            return rest
        self.reasoning.append(rest)
        if self._emitted:
            return ""
        # Nothing visible was ever produced, so the diverted text is all there
        # is. It has already been drained out of the buffer chunk by chunk, so
        # salvage the accumulation rather than the (empty) remainder.
        salvage = "".join(self.reasoning)
        logger.warning(
            "reasoning_block_unterminated",
            extra={"chars": len(salvage)},
        )
        return salvage


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
        self._reasoning = _ReasoningFilter()

    async def open(self) -> None:
        params = dict(self._params, stream=True)
        # Asking for usage is the ONLY way any of these providers reports token
        # counts on a stream — without it the final chunk carries none at all.
        # Gemini used to reject the parameter, which is why it was skipped here;
        # it accepts it now (verified against gemini-2.5-flash and 3.6-flash),
        # and skipping it was leaving every Gemini turn with zero usage.
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
            # DeepSeek-convention providers stream thinking on its own field.
            # Collect it for logging only — it must never reach the text.
            if (thought := getattr(delta, "reasoning_content", None)):
                self._reasoning.reasoning.append(thought)
            if delta.content:
                visible = self._reasoning.feed(delta.content)
                if visible:
                    self._text.append(visible)
                    yield visible
            for tc in delta.tool_calls or []:
                # `index` is what pairs an argument delta with the call it
                # belongs to. Gemini's bridge can omit it; without a fallback
                # every parallel call collapses onto the key None (arguments
                # concatenated into one unparseable blob) and sorted() then
                # raises on mixed None/int keys. A fresh call carries its name,
                # so start a new slot on those and keep appending to the last
                # one otherwise.
                idx = tc.index
                if idx is None:
                    name = getattr(tc.function, "name", None) if tc.function else None
                    idx = len(self._tool_calls) if (name or not self._tool_calls) \
                        else max(self._tool_calls)
                acc = self._tool_calls.setdefault(
                    idx, {"id": "", "name": "", "arguments": "", "signature": None}
                )
                signature = _thought_signature(tc)
                if signature:
                    acc["signature"] = signature
                if tc.id:
                    acc["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        acc["name"] = tc.function.name
                    if tc.function.arguments:
                        acc["arguments"] += tc.function.arguments
        if (tail := self._reasoning.flush()):
            self._text.append(tail)
            yield tail
        if self._reasoning.reasoning:
            logger.debug(
                "reasoning_stripped",
                extra={
                    "provider": self._provider,
                    "chars": sum(len(r) for r in self._reasoning.reasoning),
                },
            )
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
                    signature=acc.get("signature"),
                )
            )
        return LLMMessage(
            content=blocks,
            stop_reason=_stop_reason_for(self._finish, blocks),
            usage=self._usage,
        )

    async def aclose(self) -> None:
        if self._raw is not None:
            await self._raw.close()


class _OpenAIResponsesStream:
    """/v1/responses stream exposing the same surface as _OpenAICompatStream
    (.text_stream and get_final_message()), so the orchestrator can't tell the
    two endpoints apart. Used for gpt-5.6+ tool calls only."""

    def __init__(self, client, params: dict) -> None:
        self._client = client
        self._params = params
        self._raw = None
        self._text: list[str] = []
        # output_index → accumulating function call. The API streams a
        # function_call item first (with its call_id and name), then its
        # arguments JSON in deltas keyed by the same index.
        self._calls: dict[int, dict] = {}
        self._final = None
        self._incomplete: str | None = None
        self._usage = Usage()
        self._consumed = False

    async def open(self) -> None:
        self._raw = await self._client.responses.create(**dict(self._params, stream=True))

    @property
    def text_stream(self) -> AsyncIterator[str]:
        return self._consume()

    async def _consume(self) -> AsyncIterator[str]:
        async for event in self._raw:
            etype = getattr(event, "type", "")
            if etype == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    self._text.append(delta)
                    yield delta
            elif etype == "response.output_item.added":
                item = getattr(event, "item", None)
                if item is not None and getattr(item, "type", None) == "function_call":
                    self._calls[getattr(event, "output_index", len(self._calls))] = {
                        "id": getattr(item, "call_id", "") or "",
                        "name": getattr(item, "name", "") or "",
                        "arguments": getattr(item, "arguments", "") or "",
                    }
            elif etype == "response.function_call_arguments.delta":
                acc = self._calls.setdefault(
                    getattr(event, "output_index", 0), {"id": "", "name": "", "arguments": ""}
                )
                acc["arguments"] += getattr(event, "delta", "") or ""
            elif etype in ("response.completed", "response.incomplete", "response.failed"):
                # The terminal event carries the fully assembled response —
                # authoritative for usage, and a fallback if any delta was missed.
                self._final = getattr(event, "response", None)
        self._consumed = True

    async def get_final_message(self) -> LLMMessage:
        if not self._consumed:  # drain remainder if text_stream wasn't finished
            async for _ in self._consume():
                pass
        if self._final is not None:
            final = _responses_to_message(self._final)
            if final.content:
                return final
            self._usage = final.usage  # empty output: keep the real usage numbers

        blocks: list = []
        text = "".join(self._text)
        if text:
            blocks.append(TextBlock(text=text))
        for idx in sorted(self._calls):
            acc = self._calls[idx]
            blocks.append(
                ToolUseBlock(
                    id=acc["id"] or _gen_tool_id(),
                    name=acc["name"],
                    input=_parse_tool_args(acc["arguments"], acc["name"]),
                )
            )
        return LLMMessage(
            content=blocks,
            stop_reason="tool_use" if self._calls else "end_turn",
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
                    # Same kwarg strip as create_message: these two are hints for
                    # the compat path and the Anthropic SDK rejects unknown keys.
                    anthro = {
                        k: v for k, v in self._kwargs.items()
                        if k not in ("reasoning_effort", "cache_key")
                    }
                    cm = self._owner._anthropic.stream_message(
                        **{**anthro, "model": model}
                    )
                    stream = await cm.__aenter__()
                    self._anthropic_cm = cm
                    return stream
                client = self._owner._compat_client(provider)
                if _use_responses_api(provider, model, self._kwargs):
                    stream = _OpenAIResponsesStream(
                        client, _to_responses_params(model, self._kwargs)
                    )
                else:
                    stream = _OpenAICompatStream(
                        client,
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
