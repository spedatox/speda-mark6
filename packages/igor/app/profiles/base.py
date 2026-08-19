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
    ABC for all in-process agent identity profiles (Speda + the five Superior
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

    # Episodic session recall scope (Rule 10: identity decision lives here, not
    # in the skill). "own" — a new session sees only THIS agent's recent session
    # recaps (specialists stay in their lane). "all" — sees every agent's recaps,
    # tagged by agent_id; the orchestrator profile (Speda) overrides to this so
    # it has cross-agent situational awareness.
    episodic_recall_scope: Literal["own", "all"] = "own"

    # Which side of the House Party Protocol this agent is on while it is
    # engaged: True = MISSION COMMANDER (plans the objective, dispatches the
    # roster, debriefs the owner), False = OPERATIVE (takes dispatched work,
    # including work outside its usual domain). The orchestrator picks the
    # protocol block off this flag — identity decision, so it lives here and not
    # as an agent_id comparison in core (Rule 10). Speda sets it True, and its
    # war-room session alias inherits it.
    house_party_commander: bool = False

    name: str
    sonnet_model: str = "claude-sonnet-4-6"
    haiku_model: str = "claude-haiku-4-5-20251001"

    # Azure Speech voice this agent speaks with in voice mode. Identity, so it
    # lives here and never in core (Rule 10) — the TTS service only resolves it.
    # Empty = fall back to settings.tts_default_voice.
    #
    # Prefer a MULTILINGUAL voice. Azure has exactly two native Turkish voices
    # and both are the ones every CapCut video uses, so they make the assistant
    # sound like stock filler; the ~57 multilingual voices speak Turkish just as
    # well, cost the same, and give the roster room to sound like individuals.
    # Their names are en-US-…/de-DE-… while speaking Turkish, which is why the
    # spoken language is configured separately (settings.tts_locale).
    voice_id: str = ""

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

    def cheap_tier(self, model_ref: str) -> str:
        """The cheap model on `model_ref`'s OWN provider — or `model_ref` itself
        when that provider has no cheaper tier declared.

        Provider detection goes through llm_client.parse_model_ref, the same
        function that decides where a call is actually sent, so this can never
        disagree with the router. It never returns a different provider than it
        was given: a deployment with no Anthropic key must not have Anthropic
        models appear out of a "cheap tier" lookup.

        The Anthropic tier applies only to a genuinely bare Anthropic model name.
        parse_model_ref reports an UNRECOGNISED prefix ("groq:…") as Anthropic
        too — that ref is returned untouched instead, so a ref the owner set
        fails loudly as theirs rather than quietly becoming a Haiku call.
        """
        from app.services.llm_client import parse_model_ref

        provider, _ = parse_model_ref(model_ref)
        tier = self.background_models.get(provider)
        if tier:
            return tier
        if provider == "anthropic" and ":" not in model_ref:
            return self.haiku_model
        return model_ref

    def background_model(self, active_model_ref: str) -> str:
        """
        Cheap model for background work (titles, recaps, compaction, Legion
        pre-filters) — ALWAYS on the provider the owner actually chose.

        `active_model_ref` is the model this turn is genuinely running on, which
        is already the end of the routing chain: the composer's per-turn pick,
        else the Telegram pin, else the routing-matrix pin for this agent, else
        the .env override, else the profile. So the cheap tier is derived from
        it and never from a provider the engine picked on its own. Only an
        explicit LLM_BACKGROUND_MODEL — the owner naming a background model
        themselves — outranks it.

        The old version keyed off a hardcoded provider tuple and fell back to
        Anthropic Haiku for anything it didn't recognise, so an agent routed to
        a provider outside that list had every title, recap and Legion
        pre-filter quietly billed to Anthropic. Nothing here reaches for
        Anthropic unless the resolved model IS Anthropic.
        """
        from app.config import settings

        if settings.llm_background_model:
            return settings.llm_background_model
        return self.cheap_tier(active_model_ref)

    def allocate_model(
        self,
        triggered_by: Literal["user", "n8n", "agent"],
        is_background: bool = False,
    ) -> str:
        """
        Which model this agent runs on, for any kind of turn.

        Precedence:
          1. the owner's per-agent pin from the routing matrix
             (app/core/runtime_state.get_agent_models) — it wins over
             everything, for EVERY trigger source. If the owner pinned an
             agent, that is the model it runs, whether the turn came from the
             app, from n8n, or from another agent. No exceptions.
          2. the .env deployment overrides (LLM_MAIN_MODEL /
             LLM_BACKGROUND_MODEL).
          3. this profile's own models (D-C4: interactive → Sonnet,
             automated → the cheap tier).

        An unpinned automated turn takes the cheap tier OF THE MAIN MODEL'S
        PROVIDER, not a hardcoded Anthropic one: a deployment whose main model
        is `zai:…` must not have its n8n turns silently land on Anthropic.
        """
        from app.config import settings
        from app.core.runtime_state import get_agent_models

        override = get_agent_models().get(self.agent_id)
        if override:
            return override

        if is_background or triggered_by in ("n8n", "agent"):
            if settings.llm_background_model:
                return settings.llm_background_model
            return self.cheap_tier(settings.llm_main_model or self.sonnet_model)
        return settings.llm_main_model or self.sonnet_model

    def allocate_telegram_model(self) -> str:
        """Model for Telegram-channel turns. Checks the Telegram-specific pin
        first; falls back to the normal allocate_model("user") chain."""
        from app.core.runtime_state import get_telegram_models

        tg_override = get_telegram_models().get(self.agent_id)
        if tg_override:
            return tg_override
        return self.allocate_model("user")
