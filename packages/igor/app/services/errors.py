"""
Provider error translation — turns a raw LLM-provider exception into a short,
actionable message for the UI.

Why this exists as its own service (not inline in the chat router, per
CLAUDE.md Rule 1): the backend talks to six providers (Anthropic, OpenAI,
Gemini, NVIDIA, z.ai/DeepSeek, Ollama), each with its own failure shapes for
the same underlying problem (bad key, no credit, rate limit, no uplink). This
module owns that provider-specific string matching so routers stay thin
dispatch layers and the classification logic has one place to grow as new
providers or failure shapes are added.
"""


def friendly_provider_error(model: str, exc: Exception) -> str:
    """Turn a raw provider exception into a short, actionable message for the UI.
    Covers the common dead-ends (missing key, no credit, rate limit, no uplink)
    across every provider so the user sees WHY instead of a frozen spinner."""
    provider = model.partition(":")[0] if ":" in model else "anthropic"
    text = str(exc).lower()
    # NVIDIA NIM's signature failure: the key lists models fine (GET /v1/models
    # → 200) but EVERY chat call 404s with "Function … not found for account".
    # It's an account-side permission ("Public API Endpoints"), not our request —
    # so say that instead of a raw 404 the owner can't act on.
    if provider == "nvidia" and ("not found for account" in text or "function" in text or "404" in text):
        return (
            "NVIDIA can list models but can't run them for this account — it's missing the "
            "'Public API Endpoints' permission (a known NVIDIA account issue, not a model or "
            "key problem). Fix it at build.nvidia.com / email help@build.nvidia.com, or just "
            "use another provider (OpenAI, Gemini, z.ai, DeepSeek, Anthropic)."
        )
    # A tool-call rejection is NOT an auth failure, but OpenAI's 5.6 family
    # reports one as a 401 "insufficient permissions" — which used to surface as
    # "check your key" and sent the owner hunting a perfectly good key. Match the
    # payload wording first so the real cause is named.
    if "function tools" in text or "/v1/responses" in text or "reasoning_effort" in text:
        return (
            f"{provider.title()} rejected the tool definitions for this model — it's the "
            "request payload, not your API key. This model needs its tool calls on the "
            "Responses API; pick another model if it persists."
        )
    if "401" in text or "unauthorized" in text or "api key" in text or "authentication" in text:
        key = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}.get(provider, "ANTHROPIC_API_KEY")
        return f"{provider.title()} rejected the request — check that {key} is set and valid."
    if "credit" in text or "billing" in text or "quota" in text or "insufficient" in text:
        return f"{provider.title()} reports no available credit/quota for this account."
    if "429" in text or "rate limit" in text or "overloaded" in text or "529" in text:
        return f"{provider.title()} is rate-limited or overloaded right now — try again shortly."
    if "connect" in text or "timeout" in text or "connection" in text:
        return f"Couldn't reach {provider.title()}. Check the network (or the local Ollama daemon)."
    return f"{provider.title()} request failed: {exc}"
