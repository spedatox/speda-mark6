from abc import ABC, abstractmethod
from typing import Literal


class AgentProfile(ABC):
    """
    ABC for all in-process agent identity profiles (SPEDA + the five Superior
    Six that run here). One subclass per agent_id; the engine is untouched by
    identity (Rule 10). The ProfileRegistry loads every enabled subclass at
    startup and the orchestrator resolves one per request from context.agent_id.
    """

    # ── Identity ────────────────────────────────────────────────────────────
    # agent_id is the discriminator that selects this profile, scopes sessions,
    # scopes automations, and (Phase 3) filters the tool allowlist. It must be
    # unique across all enabled profiles and stable (it is persisted on rows).
    agent_id: str = "speda"
    domain: str = ""                          # Short human label, e.g. "finance & budget"

    # Declarative tool allowlist (Rule 5/10): which skills/MCP servers/toolsets
    # this agent may use. None = no restriction (sees the full registry). The
    # CapabilityRegistry applies this filter — the profile only declares it.
    # Filter wiring lands in Phase 3; declaring it now is harmless.
    tool_allowlist: list[str] | None = None

    name: str
    sonnet_model: str = "claude-sonnet-4-6"
    haiku_model: str = "claude-haiku-4-5-20251001"

    # Per-provider cheap models for background tasks (title generation, session
    # log, daily maintenance). Populated by the concrete profile — Rule 10:
    # model IDs live in the profile file, never in core. Keys are provider
    # names ("openai", "gemini"); Anthropic uses haiku_model, Ollama reuses
    # the active local model (it's the only one available in a dead zone).
    background_models: dict[str, str] = {}

    @abstractmethod
    def build_system_prompt(self, context_vars: dict) -> str:
        """Build the full system prompt string from the template and runtime context vars."""
        ...

    def background_model(self, active_model_ref: str) -> str:
        """
        Cheap model ON THE SAME PROVIDER as the active chat model, for background
        tasks. Chatting on OpenAI/Gemini must not silently spend Anthropic
        credit (or fail when no Anthropic key is configured), and in the Dead
        Zone (Ollama, no uplink) the local model is the only one that answers.
        """
        from app.config import settings

        provider, sep, _ = active_model_ref.partition(":")
        if not sep or provider not in ("openai", "gemini", "ollama"):
            # Anthropic path — keep honoring the .env override.
            return settings.llm_background_model or self.haiku_model
        if provider == "ollama":
            return active_model_ref
        return self.background_models.get(provider, active_model_ref)

    def allocate_model(
        self,
        triggered_by: Literal["user", "n8n", "agent"],
        is_background: bool = False,
    ) -> str:
        """
        SPEDA governs model allocation — agents do not decide independently (D-C4).
        - User-facing interactive → Sonnet 4.6
        - Background / automated → Haiku 4.5
        LLM_MAIN_MODEL / LLM_BACKGROUND_MODEL in .env override per deployment
        (any "provider:model" ref — see app/services/llm_client.py).
        """
        from app.config import settings

        if is_background or triggered_by in ("n8n", "agent"):
            return settings.llm_background_model or self.haiku_model
        return settings.llm_main_model or self.sonnet_model
