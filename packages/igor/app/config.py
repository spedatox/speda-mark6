# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging
import logging.config
import json
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_DATA_DIR = Path.home() / ".speda"

# Owner-editable overrides written from the desktop Settings → Configuration tab
# (routers/config.py). Ranked ABOVE real OS environment variables by
# _ManagedEnvSource below — listing it in `env_file=` is NOT enough in production;
# see that class for why.
_MANAGED_ENV = _DATA_DIR / ".env"

# Keys the DEPLOYMENT must own outright, never the owner's override file.
# docker-compose.yml sets DATABASE_URL via `environment:` specifically to outrank
# the deployment .env; the managed file lives in the bind-mounted data dir, so
# without this exemption a stray line there could silently repoint the database.
_DEPLOYMENT_OWNED = frozenset({"DATABASE_URL"})


# ── Managed-env override store (desktop Configuration tab) ───────────────────
# A tiny KEY=VALUE reader/writer over _MANAGED_ENV. Deliberately not a full
# dotenv parser — we only ever write what we wrote (simple, quoted values) and
# read it back. Defined above Settings because _ManagedEnvSource reads it while
# the module-level `settings` object is being constructed.

def _dq(value: str) -> str:
    """Double-quote a value, escaping quotes/backslashes, so multi-word or
    special-character secrets round-trip intact."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def read_managed_env() -> dict[str, str]:
    """Parse the managed override file into a dict. Missing file → empty."""
    out: dict[str, str] = {}
    if not _MANAGED_ENV.exists():
        return out
    try:
        for line in _MANAGED_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            key = key.strip()
            raw = raw.strip()
            if len(raw) >= 2 and raw[0] == raw[-1] == '"':
                raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            out[key] = raw
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).error("managed_env_read_failed", extra={"error": str(e)})
    return out


def write_managed_env(updates: dict[str, str | None]) -> None:
    """Merge `updates` into the managed override file. A None value deletes the
    key (falls back to the deployment .env / default). Atomic-ish rewrite."""
    current = read_managed_env()
    for key, value in updates.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Speda managed overrides — written by the desktop Configuration tab.",
        "# Edit in the app (Settings → Configuration). Values here win over .env.",
        "",
    ]
    for key in sorted(current):
        lines.append(f"{key}={_dq(current[key])}")
    _MANAGED_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


class _ManagedEnvSource(PydanticBaseSettingsSource):
    """The owner's Configuration-tab overrides, ranked above real OS env vars.

    Naming this file in `env_file=` is not enough in production. pydantic ranks
    `os.environ` above EVERY dotenv layer, and docker-compose's `env_file:` does
    not hand the deployment .env to the process as a file — it expands it into
    real environment variables. So in a container every key present in both the
    deployment .env and this file was decided by the deployment .env, and the
    Configuration tab silently did nothing. (It behaved as documented under bare
    uvicorn, which is why it looked fine in dev.) This source restores the
    intended precedence in both environments.

    Keys in `_DEPLOYMENT_OWNED` are skipped — the deployment keeps those.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # Unused: __call__ supplies the whole mapping in one pass.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return {
            key.lower(): value
            for key, value in read_managed_env().items()
            if key.upper() not in _DEPLOYMENT_OWNED
        }


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_data: dict = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        # Propagate request_id if attached to the record
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        # Merge any extra fields passed via extra={...}
        for key, value in record.__dict__.items():
            if key not in (
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "id", "levelname", "levelno", "lineno", "module",
                "msecs", "message", "msg", "name", "pathname", "process",
                "processName", "relativeCreated", "stack_info", "thread",
                "threadName", "request_id",
            ) and not key.startswith("_"):
                log_data[key] = value
        return json.dumps(log_data)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Bare-metal/dev only: the deployment .env sitting next to the process.
        # In Docker this file is not in the image — compose expands it into real
        # env vars instead. _MANAGED_ENV is deliberately NOT listed here; it is
        # applied by _ManagedEnvSource, which outranks os.environ.
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Highest → lowest precedence. The owner's Configuration tab sits just
        under explicit init kwargs and above everything the deployment supplies."""
        return (
            init_settings,
            _ManagedEnvSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    # Anthropic
    anthropic_api_key: str = "not-set"

    # ── Multi-provider LLM routing ──────────────────────────────────────────
    # Model refs everywhere are "provider:model" — e.g. "openai:gpt-4o",
    # "gemini:gemini-2.5-flash", "zai:glm-4.6", "ollama:llama3.1:8b" (Ollama is
    # local, dev/testing only). A bare model name means Anthropic, so existing
    # refs keep working. Routing lives in app/services/llm_client.py.
    openai_api_key: str = ""
    gemini_api_key: str = ""
    # Vertex AI — the SAME Gemini models as "gemini:", reached through Google
    # Cloud instead of AI Studio. Added ALONGSIDE gemini:*, never replacing it:
    # both can be configured at once and both appear in the model picker, so a
    # ref is a deliberate choice of BILLING ROUTE, not just of model.
    #
    # Why the second route exists: AI Studio (generativelanguage.googleapis.com)
    # runs on its own prepay balance, and Google Cloud credits — the Developer
    # Program / AI Pro monthly grants — categorically cannot pay for it. The same
    # token on Vertex bills as ordinary Cloud usage, which those credits DO cover.
    # Per-token list price is the same on both; "global" location avoids the
    # regional-endpoint uplift.
    #
    # Auth is OAuth, and ONLY OAuth. There is deliberately no VERTEX_API_KEY:
    # aiplatform.googleapis.com refuses API keys on the Chat Completions method
    # in every form — ?key=, x-goog-api-key, v1 and v1beta1, with or without the
    # project in the path — with 401 CREDENTIALS_MISSING, "API keys are not
    # supported by this API. Expected OAuth2 access token…". The console showing
    # one key for both AI Studio and Agent Platform is real, but that key opens
    # the AI Studio door, not this one. So: Application Default Credentials
    # (`gcloud auth application-default login`) or the service-account JSON at
    # VERTEX_CREDENTIALS_FILE, which needs roles/aiplatform.user.
    #
    # The project must be linked to the billing account holding the credits, or
    # they are not what gets drawn down.
    vertex_project_id: str = ""
    vertex_location: str = "global"
    vertex_credentials_file: str = ""
    # Embedding model for semantic recall (app/skills/semantic_search.py). Always
    # OpenAI regardless of llm_main_model/llm_background_model — reuses openai_api_key.
    embedding_model: str = "text-embedding-3-small"
    # z.ai (Zhipu GLM) — OpenAI-compatible endpoint. Get a key from
    # https://z.ai/manage-apikey/apikey-list. Enables refs like "zai:glm-4.6".
    zai_api_key: str = ""
    # DeepSeek — OpenAI-compatible endpoint (api.deepseek.com). Get a key from
    # https://platform.deepseek.com. Enables refs like "deepseek:deepseek-v4-pro".
    deepseek_api_key: str = ""
    # NVIDIA NIM — OpenAI-compatible endpoint (integrate.api.nvidia.com/v1). Get a
    # free key from https://build.nvidia.com (generous free credits across a large
    # open-model catalog: Llama, Nemotron, DeepSeek, Qwen, Mistral, …). Enables
    # refs like "nvidia:meta/llama-3.1-405b-instruct". The full live catalog is
    # listed from /v1/models, so every model your key can reach shows in the picker.
    nvidia_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434/v1"

    # Optional model-ref overrides for the profile's defaults (per-agent
    # assignment stays in each agent's profile; these swap it via .env without
    # touching code). Empty = use the profile's models.
    llm_main_model: str = ""        # user-facing interactive responses
    llm_background_model: str = ""  # n8n/agent-triggered + background tasks

    # Comma-separated "provider:model" refs tried in order when the primary
    # provider call fails (auth, rate limit, connection, 5xx). Empty = no
    # fallback. Example: "openai:gpt-4o,ollama:llama3.1:8b"
    llm_fallback_chain: str = ""

    # Database — SQLite by default (no server needed); override with postgresql+asyncpg:// for prod
    database_url: str = f"sqlite+aiosqlite:///{_DATA_DIR / 'speda.db'}"

    # Owner's IANA timezone (e.g. "Europe/Istanbul", "America/New_York"). The
    # server clock and every stored timestamp stay UTC — correct for a server —
    # but the owner is a single human in ONE place, so the app PRESENTS time and
    # interprets "9am" in this zone. It is injected into every AgentContext and
    # user-message stamps render in it. Read lazily at request time, so a change
    # via the Configuration tab is live.
    #
    # The default is the owner's zone, NOT "UTC". It used to be UTC, and because
    # the server's .env never set OWNER_TIMEZONE, every wall-clock decision in
    # the system quietly ran three hours early: message stamps the agents read,
    # the health day boundary, lecture-end detection. A default of "UTC" for a
    # single-user system is not a neutral choice — it is a wrong one that nobody
    # sees until they compare a timestamp to their own watch. See core/clock.py.
    owner_timezone: str = "Europe/Istanbul"

    # Auth — single service credential (X-API-Key) for the desktop app + scripts.
    speda_api_key: str = "dev-key"
    # Shared secret for the n8n webhook trigger — X-N8N-Secret.
    n8n_secret: str = "dev-n8n-secret"
    # House Party Protocol authorization passphrase. The all-hands protocol is
    # heavy, expensive and still a prototype, so engaging it requires the owner
    # to speak this exact passphrase — the house_party tool validates the agent's
    # supplied value against this in constant time and refuses otherwise. Speda
    # never knows it; it must be given by the owner each time. Change it in .env.
    house_party_passphrase: str = "wheels-up-24-karat"

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Comma-separated *extra* allowed browser origins. The desktop client's
    # `app://bundle` origin is always allowed by main.py and does not need to be
    # listed here. Empty in production = desktop only, no other cross-origin
    # browser access (the API is header-authenticated). In DEBUG, localhost dev
    # origins are allowed automatically. NEVER ship "*" to an internet-facing
    # server.
    cors_allowed_origins: str = ""

    # ── n8n automation engine ────────────────────────────────────────────────
    # n8n is the sole scheduling/automation organ (CLAUDE.md). Speda is a CONTROL
    # PLANE over its REST API — it composes/lists/toggles workflows but never
    # schedules anything internally. Default host is the docker-compose service.
    # ── Hisar: the owner's own cloud filesystem ──────────────────────────────
    # Reached over the container network, not the public hostname — this is an
    # internal call and should not leave the box. The machine token is the same
    # HISAR_MACHINE_TOKEN the Hisar container holds; it grants read across the
    # vault and write only under /Speda and /Forge (server/auth.py there).
    hisar_base_url: str = "http://hisar:8600"
    hisar_machine_token: str = ""

    n8n_api_url: str = "http://n8n:5678"
    n8n_api_key: str = ""   # n8n → Settings → n8n API → create key
    # URL n8n uses to call BACK into Speda's /trigger endpoint. Internal compose
    # network by default; override with the public domain if n8n runs elsewhere.
    speda_callback_url: str = "http://app:8000"

    # ── Telegram (first-class chat channel + primary notification surface) ────
    # ONE BOT PER AGENT. Each agent speaks from its own @BotFather bot so a
    # Sentinel budget alert arrives from @SentinelBot and a NightCrawler hit
    # from @NightCrawlerBot — attribution lives at the notification surface.
    # Tokens are SECRETS (config, keyed by agent_id); the agent's *identity* is
    # its profile (Rule 10). A leaked token burns one bot, never the fleet. A
    # missing token degrades that one agent to Speda's bot (tagged), never the
    # channel. See docs/TELEGRAM_ARCHITECTURE.md.
    #
    # telegram_bot_token is the legacy single-bot alias — treated as Speda's bot
    # when telegram_bot_token_speda is unset, so existing setups keep working.
    telegram_bot_token: str = ""            # from @BotFather — legacy alias for Speda
    telegram_bot_token_speda: str = ""
    telegram_bot_token_sentinel: str = ""
    telegram_bot_token_nightcrawler: str = ""
    telegram_bot_token_ultron: str = ""
    telegram_bot_token_centurion: str = ""
    telegram_bot_token_atomix: str = ""
    telegram_bot_token_orion: str = ""
    telegram_bot_token_optimus: str = ""

    # Ingress mode for inbound chat/media:
    #   webhook — production (Contabo, public HTTPS): setWebhook per bot.
    #   polling — dev (no public URL): one getUpdates long-poll task per bot.
    #   off     — channel disabled (no ingress; outbound-only if tokens exist).
    telegram_mode: str = "off"              # off | polling | webhook
    # Public base URL for webhook mode, e.g. https://speda.example.com. The per-
    # bot webhook is {base}/telegram/webhook/{agent_id}.
    telegram_webhook_base: str = ""
    # Shared secret set as each bot's webhook secret_token and validated on every
    # inbound webhook via the X-Telegram-Bot-Api-Secret-Token header (Telegram's
    # own auth mechanism — we can't mint an X-API-Key on their redirect).
    telegram_webhook_secret: str = ""

    # Captured at runtime via the in-app "Connect Telegram" flow (runtime_state);
    # this is only a fallback for headless setups. The owner's private-chat id is
    # the SAME number for every bot (it is their Telegram user id).
    telegram_chat_id: str = ""

    # Firebase Cloud Messaging — the push channel to Ultron Wear.
    # Path to a service-account JSON with the Firebase Admin SDK role. FCM HTTP
    # v1 requires an OAuth2 bearer token minted from this; the legacy server-key
    # endpoint was decommissioned in June 2024. Empty = push disabled, which is
    # a supported configuration (the watch falls back to asking locally).
    fcm_credentials_file: str = ""
    # Optional: derived from the credentials file's project_id when blank.
    fcm_project_id: str = ""

    # App
    debug: bool = False
    log_level: str = "INFO"

    # Prompt cache TTL — Anthropic offers only "5m" or "1h" (no 24h exists).
    # 1h is the max, and the TTL RESETS on every cache read — so with regular use
    # (e.g. an always-on server) the prefix stays warm continuously and is
    # re-written at most ~once/day. The cache is content-keyed at the ORG level,
    # so this warm prefix is shared across ALL sessions automatically. Combined
    # with lazy loading (small, stable prefix), the tool/system tokens are
    # effectively written once and read forever.
    prompt_cache_ttl: str = "1h"
    # TTL for the CONVERSATION breakpoint (the growing message history). The
    # history changes every turn, so its cache entry is rewritten incrementally
    # anyway — the cheaper 5m write (1.25x base vs 2x for 1h) wins. Anthropic
    # requires longer-TTL breakpoints to precede shorter ones; tools/system
    # render before messages, so 1h prefix + 5m conversation is always valid.
    prompt_cache_conversation_ttl: str = "5m"

    # Dead Zone Protocol — offline operating mode (Ollama is the only provider
    # that still answers without an uplink). "auto" probes connectivity and
    # engages by itself when the internet is gone; "on" forces it (dev testing);
    # "off" disables it. OUTSIDE the dead zone, Ollama models run with the full
    # online toolset like any other provider — dev testing stays unrestricted.
    dead_zone_mode: str = "auto"  # auto | on | off

    # Which MCP servers to CONNECT at startup. With lazy tool loading (below),
    # connecting a server is cheap — its tools only enter the prompt prefix when
    # Speda actually loads them via use_toolset. So this can be generous.
    mcp_enabled: str = (
        "tavily,google_gmail,google_calendar,google_tasks,microsoft_outlook,notion"
    )

    # Lazy tool loading (progressive disclosure). When True, only always_on
    # servers' tools sit in the prompt prefix; everything else is listed in a
    # compact catalog and pulled in on demand via the use_toolset tool. This
    # keeps the cached prefix tiny (cheap writes, no rate-limit pressure) while
    # all tools stay available. Set False to load every connected tool eagerly.
    lazy_tools: bool = True
    # Servers whose tools are always in the prefix (no use_toolset needed).
    always_on_servers: str = "tavily,notion"

    # ── Language ─────────────────────────────────────────────────────────────
    # The ONE language the whole system speaks. Not a hint and not a default the
    # conversation may drift away from: it is stamped into every agent's system
    # prompt (prompts/core/15_language.md), it is what the replies are
    # synthesized in, it is what the mic is decoded as, and it is what the
    # clients render their own chrome in. The owner flips it on a switch in the
    # client, which PUTs this key — one value, so the three halves of a spoken
    # conversation can never disagree about which language they are in.
    #
    # Two-letter code. "tr" and "en" are the supported pair; anything else needs
    # a name in services/language.py LANGUAGES and an i18n dictionary in the
    # clients, so an unknown code degrades to English rather than half-applying.
    agent_language: str = "tr"
    # The finished reply is checked against the chosen language before it is
    # spoken or pushed, and a leak is logged with the offending fragments. This
    # is the backstop for the prompt contract, not a replacement for it — off,
    # the contract still holds, you just stop hearing about the misses.
    language_enforcement: bool = True
    # On a detected leak, rewrite the text into the chosen language rather than
    # only logging it. Costs one cheap-model pass on the affected reply and only
    # runs on the paths where the text has not already been shown to the owner
    # (voice synthesis, automation pushes) — a streamed chat reply is on his
    # screen before the check finishes, so there it is flagged, never rewritten.
    language_repair: bool = True
    # How many foreign fragments a reply may contain before it counts as a leak.
    # 1 is the strict reading the setting is for; raise it only if proper-noun
    # false positives become a nuisance in a language pair added later.
    language_leak_tolerance: int = 1

    # ── Voice / TTS (Azure Speech) ───────────────────────────────────────────
    # Voice mode speaks EVERY reply, so synthesis is a transport concern, not a
    # tool the model chooses to call (see services/tts.py). Azure Speech is the
    # engine: native Turkish neural voices (tr-TR-EmelNeural, tr-TR-AhmetNeural),
    # billed per character with 0.5M free/month, and fast enough to synthesize
    # sentence-by-sentence while the previous sentence is still playing.
    # An empty key disables voice mode and nothing else — the rest of the app is
    # unaffected.
    azure_speech_key: str = ""
    azure_speech_region: str = "westeurope"
    # A third TTS engine, alongside Azure and OpenAI — same voice-ref shape
    # ("elevenlabs:eleven_multilingual_v2:<voice_id>"), same degrade-on-empty
    # contract as the other two (see services/tts.py). Picked for automation
    # voice replies over Azure/OpenAI where a distinctive per-agent voice
    # matters more than raw cost.
    elevenlabs_api_key: str = ""
    # Fallback voice for any agent whose profile does not name one. The per-agent
    # voice is IDENTITY and lives in app/profiles/*.py (Rule 10); this is only the
    # engine default for profiles that stay silent about it.
    tts_default_voice: str = "en-US-BrianMultilingualNeural"
    # OVERRIDE for the language replies are SPOKEN in. Normally EMPTY: the
    # locale is derived from `agent_language` above, so the switch the owner
    # throws moves what is written and what is spoken together. Set it only to
    # pin a regional variant the switch does not offer (en-GB rather than
    # en-US). Deliberately separate from the voice's own locale, because for a
    # multilingual voice the two differ on purpose: Azure has only two native
    # Turkish voices and both are the stock CapCut ones, so the roster uses
    # multilingual voices instead; those carry an en-US/de-DE name while
    # speaking Turkish, and the SSML xml:lang must follow the TEXT, not the
    # voice. With this empty AND no language resolved, build_ssml falls back to
    # guessing from the voice name — which for a multilingual voice would read
    # Turkish with English phonetics.
    tts_locale: str = ""
    # Azure output-format token. MP3 decodes natively in the browser AudioContext
    # and is roughly a tenth the size of PCM over the wire.
    tts_output_format: str = "audio-24khz-48kbitrate-mono-mp3"
    # OVERRIDE for the language the OWNER speaks, which is not necessarily the
    # one the agent speaks back — dictating Turkish and being answered in
    # English is a real combination, so recognition can be pinned separately.
    # Normally EMPTY, following `agent_language` like synthesis does, which is
    # right for the common case where both halves of the conversation are one
    # language. Unlike synthesis there is no multilingual model to fall back on:
    # Azure decodes the language it is told, and a wrong locale returns
    # confident nonsense rather than degrading.
    stt_locale: str = ""

    # ── Conversation compaction ──────────────────────────────────────────────
    # On a long chat, older turns are summarized (background, Haiku) so the model
    # sees [summary] + recent window instead of the whole growing transcript —
    # the single biggest cost driver on long conversations. Raw messages are
    # never deleted (the UI still shows everything); only the model's context is
    # compacted. Compaction triggers when the live history exceeds the token
    # threshold, keeping at least the most recent `keep` tokens verbatim.
    compaction_enabled: bool = True
    compaction_threshold_tokens: int = 12000
    compaction_keep_tokens: int = 4000

    # ── Episodic session recall ──────────────────────────────────────────────
    # The cross-session memory tier. A short recap of every session (subject,
    # decisions, open threads) is maintained by a post-turn background task;
    # when a NEW session starts, the last few recaps for that agent are injected
    # into the system prompt so "what were we discussing last time?" is
    # answerable without any tool call. Distinct from compaction (in-session)
    # and from semantic recall (recall_conversations, on-demand).
    episodic_recap_enabled: bool = True
    # How many recent sessions' recaps are injected into a new session.
    episodic_recall_sessions: int = 5
    # Hard cap on the injected block (~1.5k tokens) — oldest entries drop first.
    episodic_recall_max_chars: int = 6000
    # max_tokens for the per-turn recap generation call.
    episodic_recap_max_tokens: int = 300

    # ── Recall quality ───────────────────────────────────────────────────────
    # The relevance floor. Reciprocal Rank Fusion keeps ORDER and throws away
    # SCORE, which means the rank-1 result of a list of pure noise is presented
    # with the same confidence as a perfect match — measured at a 71% "confident
    # miss" rate against evals/recall before this existed. A candidate must now
    # clear this cosine similarity before it is allowed into the vector ranking
    # at all, so recall can answer "nothing in memory matches" instead of
    # answering wrongly. Tuned by sweeping evals/recall/run_eval.py; raise it to
    # trade recall for precision, lower it to trade the other way. 0.0 disables
    # the floor and restores the old rank-only behaviour.
    #
    # 0.25 is measured, not chosen: swept over the probe set after the store was
    # repaired, it beat every other value on every metric at once (hit@1 57.8%,
    # hit@5 82.2%, MRR 0.683, confident misses 35.6%). Re-sweep it whenever the
    # store changes shape — the optimum moved once already, because the first
    # sweep was run against a store that was 60% fragments.
    recall_min_similarity: float = 0.25
    # The same floor for the raw-transcript half (recall_conversations). Message
    # text is longer and noisier than a distilled fact, so its cosines run lower
    # for the same degree of relevance and the floor is looser to match.
    recall_message_min_similarity: float = 0.25
    # Reject observations that are fragments rather than self-contained facts —
    # a bare list item ("a table of incomes", "**Started:** 2026-08-01") carries
    # no meaning on its own, embeds to a generic centroid, and therefore ranks
    # near the top of EVERY query. 60% of the store was this before the guard.
    # Minimum characters before content is treated as a fact rather than a
    # fragment.
    observation_min_content_length: int = 25
    # Require an observation to name what it is about (the owner, a person, a
    # project) rather than leaving the subject implicit in a file it was copied
    # out of. This is the check that keeps orphan bullets out of the store.
    observation_require_subject: bool = True
    # Translate a Turkish recall query into English before searching. The store
    # is English by policy; his questions often are not, and that asymmetry cost
    # half of Turkish recall (43% hit@5 against 87% for English on the same
    # probe set). See app/services/query_translation.py. Off = the old
    # behaviour: Turkish queries search an English store directly.
    recall_translate_queries: bool = True
    # Model for that translation. Empty = the background model. It is a small,
    # cached, latency-sensitive call, so the cheapest capable model wins.
    recall_translation_model: str = ""

    # The Legion — worker model override. EMPTY by default (the provider-agnostic
    # fix): legionnaire models resolve from the parent chat model's provider —
    # low/medium-effort workers run on the profile's cheap tier for that provider
    # (Anthropic parent → Haiku, which keeps the old separate-rate-pool benefit;
    # zai parent → glm-air; …), high-effort workers inherit the parent model.
    # Set a "provider:model" ref here to pin EVERY worker to one model instead.
    # Legacy env name SUB_AGENT_MODEL still works via the validation alias.
    legion_model_override: str = Field(
        default="",
        validation_alias=AliasChoices(
            "legion_model_override", "LEGION_MODEL_OVERRIDE",
            "sub_agent_model", "SUB_AGENT_MODEL",
        ),
    )

    # Budget mode — a HARD, enforced frugality switch (not a prompt suggestion):
    #   - the Legion (Task tool) is not listed at all (impossible to deploy)
    #   - a strict concise-output directive is injected into the system prompt
    # Toggle with BUDGET_MODE=true in .env. Survives restarts. Default ON.
    budget_mode: bool = True

    # Temp outputs
    temp_outputs_dir: str = str(_DATA_DIR / "outputs")

    # Notion MCP / API integration
    notion_api_key: str = ""
    notion_client_id: str = ""
    notion_client_secret: str = ""
    notion_oauth_redirect: str = "http://localhost:8000/oauth/notion/callback"
    # Notion REST API version — required on every Notion request. Pinned here so
    # it can be bumped via .env without touching code when Notion ships a new one.
    notion_version: str = "2022-06-28"
    brave_search_api_key: str = ""
    alpha_vantage_api_key: str = ""
    tavily_api_key: str = ""
    exa_api_key: str = ""
    github_token: str = ""

    # ── OSINT / threat-intelligence skills (app/skills/osint.py) ─────────────
    # Most run keyless. AbuseIPDB needs a free key (https://www.abuseipdb.com,
    # 1,000 checks/day on the free tier). abuse.ch (URLhaus/ThreatFox/
    # MalwareBazaar) now issues a free Auth-Key from https://auth.abuse.ch — the
    # skills send it when set and still attempt keyless otherwise.
    abuseipdb_api_key: str = ""
    abuse_ch_api_key: str = ""
    # AlienVault OTX (https://otx.alienvault.com — free), Shodan
    # (https://account.shodan.io), Hunter.io (https://hunter.io — 25/mo free),
    # Etherscan (https://etherscan.io/apis — free), Intelligence X
    # (https://intelx.io — free tier). Blockchair runs keyless; a key just
    # raises the rate limit.
    otx_api_key: str = ""
    shodan_api_key: str = ""
    hunter_api_key: str = ""
    etherscan_api_key: str = ""
    intelx_api_key: str = ""
    blockchair_api_key: str = ""

    # Google Workspace MCP — official remote servers (googleapis.com/mcp/v1)
    # Get these from Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client
    # Run scripts/google_oauth.py once to obtain the refresh token.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    # Redirect the in-app "Sign in with Google" flow comes back to. For a Desktop
    # OAuth client, loopback redirects are allowed automatically.
    google_oauth_redirect: str = "http://localhost:8000/oauth/google/callback"

    # ── Microsoft 365 / Outlook (app/mcp/microsoft_rest.py) ────────────────────
    # The owner's UNIVERSITY mailbox (@ostimteknik.edu.tr) is a Microsoft work/
    # school account, so it is a second mail estate rather than a second door
    # into Gmail. Register an app in Azure Portal → App registrations, add a Web
    # platform redirect matching microsoft_oauth_redirect, create a client
    # secret, and add the DELEGATED Graph permissions listed in MS_SCOPES.
    #
    # microsoft_tenant is the authority segment: "common" (work/school AND
    # personal, the safe default), "organizations" (work/school only), or a
    # specific tenant GUID/domain. Leave it "common" unless the university's
    # tenant blocks multi-tenant apps — a single-tenant registration pinned to
    # the school's own tenant id is the fallback if IT refuses user consent.
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant: str = "common"
    microsoft_refresh_token: str = ""
    microsoft_oauth_redirect: str = "http://localhost:8000/oauth/microsoft/callback"
    # Sender domain the school-mail watch treats as "the university". Read by the
    # shipped n8n workflow's default rule; the probe itself takes the domain as a
    # parameter and knows nothing about this.
    school_mail_domain: str = "ostimteknik.edu.tr"

    # ── Maps & navigation (app/skills/navigation.py) ───────────────────────────
    # One Google Cloud key with Routes API v2, Places API (New) and Geocoding API
    # enabled. Backend-side ONLY — the watch/phone never holds it; clients receive
    # finished route geometry inside the ```map fence. Empty disables get_route /
    # find_places with a clear "not configured" message (same pattern as the other
    # keyed skills). owner_home_* is the fallback origin when the client sends no
    # live location (e.g. a Telegram turn) and the model needs a "from home" route.
    google_maps_api_key: str = ""
    owner_home_lat: float | None = None
    owner_home_lng: float | None = None

    # ── Aircraft tracking (app/skills/aircraft.py) ──────────────────────────────
    # Live ADS-B lookup by tail number/registration or callsign. Keyless —
    # adsb.lol's community feed needs no signup. airplanes.live (the original
    # default) started hard-403ing unregistered API callers in 2026 ("contact
    # us" body, no amount of retrying gets through) — adsb.lol mirrors the same
    # ADSBExchange v2 response schema (the "ac" array, same field names) without
    # that wall, so it's a drop-in swap. The base URL stays overridable so
    # another compatible mirror (api.adsb.one, or airplanes.live again if you've
    # registered with them) can stand in if this one is ever rate-limited or down.
    aircraft_tracking_base_url: str = "https://api.adsb.lol"

    # OSS Adapter URLs
    gpt_researcher_url: str = "http://localhost:8001"
    shannon_url: str = "http://localhost:9000"

    # Sandbox — the isolated computer Speda runs commands in ("capable computer").
    # In Docker this is the sandbox service (SANDBOX_URL=http://sandbox:9000);
    # empty disables the run_command tool. The default port is 9100 to avoid a
    # collision with Shannon (9000). In local dev the sandbox launcher spawns
    # packages/sandbox/server.py here when nothing already answers /health.
    sandbox_url: str = "http://localhost:9100"

    # ── Local sandbox launcher (app/services/sandbox_launcher.py) ──────────────
    # When there is no Docker (dev machines), the backend can run the stdlib exec
    # server as a child process so run_command works with a single boot. It only
    # spawns when sandbox_url points at localhost AND nothing already answers
    # /health there (a running Docker sandbox or a manual instance wins). This is
    # honestly reduced isolation — a workspace jail, not a container; Docker
    # remains the production isolation on Contabo.
    sandbox_autostart: bool = True
    sandbox_local_port: int = 9100
    sandbox_workspace: str = str(_DATA_DIR / "sandbox_workspace")

    # ── The browser sidecar (packages/browser, app/services/browser.py) ────────
    # Playwright in its own container: the plan-B fetch for pages that only exist
    # after JavaScript, and the way into the owner's logged-in portals. In Docker
    # this is the browser service (BROWSER_URL=http://browser:9200); empty
    # disables browse_page / browser_act / portal_login with a clear message,
    # same pattern as every other keyed capability.
    #
    # browser_token is shared with the sidecar, which holds live session cookies
    # for the owner's student portal — on a shared Docker network, reachable is
    # not the same as authorized. Leave it empty ONLY in local dev.
    browser_url: str = "http://localhost:9200"
    browser_token: str = ""
    # Ceiling on how much rendered page text one call may return. The sidecar caps
    # itself too; this is the cap the CONTEXT sees, and it is the one that costs
    # money. Raise it for a research-heavy deployment, not for a phone.
    browser_max_chars: int = 12_000
    # Let a plain-HTTP fetch that came back empty retry through the browser
    # (services/web_watch.py). A JS-rendered exam-results page is invisible to a
    # plain GET, which is the failure this exists to end — but it turns one cheap
    # probe into a browser render, so it stays a deliberate switch.
    browser_fallback_enabled: bool = True

    # ── The public-web browser (packages/playwright-mcp) ───────────────────────
    # The official @playwright/mcp server, in its own container, registered as a
    # Tier 2 MCP server (app/mcp/servers.py) — full upstream tool parity (click,
    # drag, evaluate, tabs, dialogs, uploads, network/console, resize…) for the
    # OPEN PUBLIC WEB ONLY. This is a SEPARATE path from browser_url above and
    # must stay that way: browser_url is the ONLY route to one of the owner's
    # saved logins, because a login through this server means the model types
    # the password. Empty disables the registration entirely (see
    # app/mcp/servers.py's "mcp_skip" log line), same pattern as every other
    # keyed capability. No token field — @playwright/mcp has no built-in auth,
    # so the container never being published (docker-compose.yml: `expose`, not
    # `ports`) is the entire boundary.
    playwright_mcp_url: str = ""

    # ── The Forge peer (app/services/forge_peer.py) ────────────────────────────
    # The Forge (Mark II) is the standalone execution engine. Speda owns its
    # lifecycle: the lifespan handler launches ONE child process per agent in
    # `forge_agents`, and each connects back to WS /agents/ws/<agent_id> as an
    # external peer. While an agent's peer is online, /chat/<agent_id> is proxied
    # to it; offline, that agent's in-process profile answers instead (graceful
    # fallback — the Forge is never a hard dependency). forge_dir empty disables
    # autostart. The Forge repo ships profiles for every agent listed here.
    forge_autostart: bool = True
    forge_dir: str = ""                       # absolute path to the forge-mk1 repo
    # Comma-separated agents to back with a Forge peer. Each must have a
    # `<id>/profile.toml` in the Forge repo AND `external_backend = True` on its
    # in-process profile here. `forge_agent` (singular) is the legacy fallback
    # used only when `forge_agents` is left blank.
    forge_agents: str = "optimus,centurion"
    forge_agent: str = "optimus"
    # Base agents-WS URL — the launcher appends `/<agent_id>` per peer, so this
    # must NOT carry a trailing agent segment. A legacy value ending in an agent
    # id (…/agents/ws/optimus) is tolerated: the launcher strips it.
    forge_ws_url: str = "ws://127.0.0.1:8000/agents/ws"
    forge_cell_backend: str = "auto"          # docker | subprocess | auto
    forge_python: str = ""                    # override interpreter; empty → uv run

    # ── News desk (two-tier RSS + NewsData.io) ─────────────────────────────────
    # Tier 1 (RSS) is keyless and always on. Tier 2 (NewsData.io) needs a free
    # key from https://newsdata.io — empty disables the news_deep_dive tool with
    # a clear message (same pattern as other keyed skills). The collector runs on
    # the n8n clock (POST /news/poll), never an internal timer.
    newsdata_api_key: str = ""
    news_poll_enabled: bool = True
    news_extra_feeds: str = ""            # comma-separated extra RSS URLs (owner)
    news_retention_days: int = 14         # prune news_items older than this
    # Tier-2 daily quota ledger split (NewsData free tier = 200/day). Buckets are
    # per-purpose so an owner deep-dive spree can't starve the auto-flag path.
    news_quota_deep_dive: int = 50        # owner-initiated "tell me more" calls
    news_quota_auto_flag: int = 50        # keyword-flagged corroboration calls
    news_quota_digest: int = 50           # daily topic digests
    # One escalation per keyword per this many minutes (developing-story guard).
    news_flash_cooldown_min: int = 120

    # ── Orion host operation (system_ops skill) ────────────────────────────────
    # Orion's ability to operate the HOST Mark VI runs on (log rotation, disk
    # checks, container inspection). OFF by default — a privileged capability
    # that must be deliberately enabled on the deployment that wants it. The
    # write jail confines any file write Orion makes to this root subtree.
    system_ops_enabled: bool = False
    system_ops_root: str = str(_DATA_DIR)     # write jail for system_ops file writes
    system_ops_timeout: int = 60              # hard cap (seconds) per host command
    # HOST BRIDGE — in prod the backend runs INSIDE a container, so local exec
    # would only ever see the container. When system_ops_host is set (e.g.
    # "root@host.docker.internal"), every system_ops action runs over SSH on the
    # actual host instead: same deny-list, jail, and audit trail — different
    # namespace. Empty = execute locally (dev / bare-metal deployments). The
    # private key lives in the data dir (bind-mounted from the host in prod) so
    # it never enters the repo or the image.
    system_ops_host: str = ""
    system_ops_ssh_port: int = 22
    system_ops_ssh_key: str = str(_DATA_DIR / "host_ops_key")

    # ── Lockdown Protocol ─────────────────────────────────────────────────────
    # Owner-triggered inbound containment: sealing the host's exposed SSH and raw
    # app ports behind firewall rules (app/services/lockdown.py). OFF by default
    # — it edits the host firewall, so a deployment must opt in, and it needs the
    # system_ops host bridge configured above to reach the host at all.
    # Engaging reuses house_party_passphrase; standing down never needs one, so
    # the way out is always available.
    lockdown_protocol_enabled: bool = False

    # ── Lifeboat Protocol ─────────────────────────────────────────────────────
    # The host-resource watch and the tiered disk reclamation it authorises
    # (app/services/lifeboat.py, runbook in docs/LIFEBOAT_PROTOCOL.md). OFF by
    # default: it runs maintenance shell on the real host, so it needs the
    # system_ops host bridge configured above to reach it at all, and a
    # deployment must opt in deliberately.
    lifeboat_protocol_enabled: bool = False
    lifeboat_script: str = "/opt/speda/lifeboat.sh"
    lifeboat_watch_fs: str = "/"
    # Disk and inode thresholds. `watch` tells the owner and waits for them;
    # `critical` is the only level at which Orion may run Tier 1 unattended, so
    # the gap between them is the size of the owner's decision window — keep it
    # wide enough that a normal week never reaches the upper one.
    lifeboat_watch_pct: int = 85
    lifeboat_critical_pct: int = 92
    # Free space at which reclamation stops (mirrors LIFEBOAT_TARGET_FREE_GB in
    # the script; set both if you change one).
    lifeboat_target_free_gb: int = 30
    # Memory runs hot legitimately — a box with caches doing its job reads high —
    # so its thresholds sit above the disk ones. Memory never authorises a
    # lifeboat run: the script reclaims disk, and RAM is a container question.
    lifeboat_mem_watch_pct: int = 90
    lifeboat_mem_critical_pct: int = 96
    # How long an unhealthy host stays quiet after being reported once. Edges are
    # the rule; this is the single nudge that stops a problem nobody fixed from
    # being mentioned exactly once and then never again.
    lifeboat_renotify_hours: int = 24

    # ── Doormat Protocol ──────────────────────────────────────────────────────
    # Moving the deployment to a new domain: a staged Caddy site, the derived
    # OAuth/Telegram settings, and the retirement of the old hostname
    # (app/services/doormat.py, runbook in docs/DOORMAT_PROTOCOL.md). OFF by
    # default — it edits the host's reverse-proxy config and the deployment .env,
    # so it needs the system_ops host bridge, and a deployment must opt in.
    doormat_protocol_enabled: bool = False

    # ── Octavius Protocol ─────────────────────────────────────────────────────
    # Igor's own database, snapshotted and shipped to the owner's Google Drive
    # (app/services/octavius.py, runbook in docs/OCTAVIUS_PROTOCOL.md). OFF by
    # default. Unlike the other host protocols this one needs NO ssh bridge — the
    # snapshot is taken through Igor's own SQLAlchemy engine — but it does need
    # Google connected, with the full `drive` scope the Connections flow already
    # requests.
    octavius_protocol_enabled: bool = False
    octavius_drive_folder: str = "Speda Mark VI — Backups"
    # How many backups to keep. Older ones are TRASHED, not deleted: Drive holds
    # trash for 30 days, so a retention number typed with one digit too few stays
    # a mistake the owner can undo.
    octavius_keep: int = 14
    # Age at which the newest backup stops counting as protection. Set from the
    # cron's period, not from taste — at a nightly cadence anything past ~48h
    # means two runs in a row failed silently.
    octavius_stale_hours: int = 48


settings = Settings()


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        handlers=[handler],
    )
    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.debug else logging.WARNING
    )
