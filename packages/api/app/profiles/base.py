from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class DocTheme:
    """
    Per-agent document branding (Rule 10: identity lives in the profile, never
    in core). One knob — the agent's signature ``accent`` hex, matching its UI
    brand colour. The documents skill derives the whole PDF/DOCX/PPTX palette
    (heading colour, table header tint, rules, zebra striping) from this single
    value, so a profile only ever sets ``accent``.
    """

    accent: str = "#5b6472"   # neutral slate — the engine default for any agent
                              # that does not declare its own brand colour.


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

    # Whether other agents may dispatch tasks TO this profile. False for
    # aliases that exist only to scope sessions (e.g. the war-room command
    # channel) — they answer /chat requests but never appear as a target in
    # the dispatch tool schema.
    dispatch_target: bool = True

    # True for agents whose real engine is a standalone peer reached over the
    # agents WebSocket (Optimus). While the peer is connected, /chat turns are
    # proxied to it (core/external_proxy.py) and dispatches route external-first;
    # when it is offline, this in-process profile answers as the fallback.
    external_backend: bool = False

    # Whether this agent has a Telegram presence (its own bot). Identity only —
    # the token itself is a secret in config, keyed by agent_id (Rule 10 split).
    # Session-scope aliases that never notify (e.g. the war-room command channel)
    # set this False so no bot is built for them. See docs/TELEGRAM_ARCHITECTURE.md.
    telegram_enabled: bool = True

    name: str
    sonnet_model: str = "claude-sonnet-4-6"
    haiku_model: str = "claude-haiku-4-5-20251001"

    # Document branding for generate_document output. Neutral by default; each
    # concrete profile overrides with its signature accent (Rule 10).
    doc_theme: DocTheme = DocTheme()

    # Per-provider cheap models for background tasks (title generation, session
    # log, daily maintenance). Populated by the concrete profile — Rule 10:
    # model IDs live in the profile file, never in core. Keys are provider
    # names ("openai", "gemini", "zai", "deepseek"); Anthropic uses haiku_model,
    # Ollama reuses the active local model (it's the only one in a dead zone).
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
        if not sep or provider not in ("openai", "gemini", "zai", "deepseek", "nvidia", "ollama"):
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
        Precedence: the owner's runtime per-agent override (set from the UI,
        app/core/runtime_state.py) wins over everything; then the .env
        LLM_MAIN_MODEL / LLM_BACKGROUND_MODEL deployment overrides; then this
        profile's own models (any "provider:model" ref — see llm_client.py).
        """
        from app.config import settings
        from app.core.runtime_state import get_agent_models

        override = get_agent_models().get(self.agent_id)
        if override:
            return override

        if is_background or triggered_by in ("n8n", "agent"):
            return settings.llm_background_model or self.haiku_model
        return settings.llm_main_model or self.sonnet_model
