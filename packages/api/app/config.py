import logging
import logging.config
import json
from pathlib import Path
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DATA_DIR = Path.home() / ".speda"

# Owner-editable overrides written from the desktop Settings → Configuration tab
# (routers/config.py). Layered OVER the checked-in .env so a value set in the UI
# wins, survives restarts, and never touches the repo. Real OS env vars still win
# over both (pydantic precedence: init > os.environ > env_file[last] > .. > first).
_MANAGED_ENV = _DATA_DIR / ".env"


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
        # Tuple order = precedence low→high: the managed override file wins over
        # the checked-in .env. Both are optional (missing file is ignored).
        env_file=(".env", str(_MANAGED_ENV)),
        env_file_encoding="utf-8",
        extra="ignore",
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

    # Auth — single service credential (X-API-Key) for the desktop app + scripts.
    speda_api_key: str = "dev-key"
    # Shared secret for the n8n webhook trigger — X-N8N-Secret.
    n8n_secret: str = "dev-n8n-secret"
    # House Party Protocol authorization passphrase. The all-hands protocol is
    # heavy, expensive and still a prototype, so engaging it requires the owner
    # to speak this exact passphrase — the house_party tool validates the agent's
    # supplied value against this in constant time and refuses otherwise. SPEDA
    # never knows it; it must be given by the owner each time. Change it in .env.
    house_party_passphrase: str = "wheels-up-24-karat"

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Comma-separated allowed browser origins. Empty in production = no
    # cross-origin browser access (the API is header-authenticated and the
    # desktop client is not a browser origin). In DEBUG, localhost dev origins
    # are allowed automatically. NEVER ship "*" to an internet-facing server.
    cors_allowed_origins: str = ""

    # ── n8n automation engine ────────────────────────────────────────────────
    # n8n is the sole scheduling/automation organ (CLAUDE.md). SPEDA is a CONTROL
    # PLANE over its REST API — it composes/lists/toggles workflows but never
    # schedules anything internally. Default host is the docker-compose service.
    n8n_api_url: str = "http://n8n:5678"
    n8n_api_key: str = ""   # n8n → Settings → n8n API → create key
    # URL n8n uses to call BACK into SPEDA's /trigger endpoint. Internal compose
    # network by default; override with the public domain if n8n runs elsewhere.
    speda_callback_url: str = "http://app:8000"

    # ── Telegram (first-class chat channel + primary notification surface) ────
    # ONE BOT PER AGENT. Each agent speaks from its own @BotFather bot so a
    # Sentinel budget alert arrives from @SentinelBot and a NightCrawler hit
    # from @NightCrawlerBot — attribution lives at the notification surface.
    # Tokens are SECRETS (config, keyed by agent_id); the agent's *identity* is
    # its profile (Rule 10). A leaked token burns one bot, never the fleet. A
    # missing token degrades that one agent to SPEDA's bot (tagged), never the
    # channel. See docs/TELEGRAM_ARCHITECTURE.md.
    #
    # telegram_bot_token is the legacy single-bot alias — treated as SPEDA's bot
    # when telegram_bot_token_speda is unset, so existing setups keep working.
    telegram_bot_token: str = ""            # from @BotFather — legacy alias for SPEDA
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
    # SPEDA actually loads them via use_toolset. So this can be generous.
    mcp_enabled: str = "tavily,google_gmail,google_calendar,notion"

    # Lazy tool loading (progressive disclosure). When True, only always_on
    # servers' tools sit in the prompt prefix; everything else is listed in a
    # compact catalog and pulled in on demand via the use_toolset tool. This
    # keeps the cached prefix tiny (cheap writes, no rate-limit pressure) while
    # all tools stay available. Set False to load every connected tool eagerly.
    lazy_tools: bool = True
    # Servers whose tools are always in the prefix (no use_toolset needed).
    always_on_servers: str = "tavily,notion"

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

    # OSS Adapter URLs
    gpt_researcher_url: str = "http://localhost:8001"
    shannon_url: str = "http://localhost:9000"

    # Sandbox — the isolated computer SPEDA runs commands in ("capable computer").
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

    # ── The Forge peer (app/services/forge_peer.py) ────────────────────────────
    # The Forge (Mark II) is the standalone execution engine for Optimus. SPEDA
    # owns its lifecycle: the lifespan handler launches it as a child process,
    # and it connects back to WS /agents/ws/<forge_agent> as an external peer.
    # While online, /chat/optimus is proxied to it; offline, the in-process
    # OptimusProfile answers instead (graceful fallback — the Forge is never a
    # hard dependency). forge_dir empty disables autostart.
    forge_autostart: bool = True
    forge_dir: str = ""                       # absolute path to the forge-mk1 repo
    forge_agent: str = "optimus"
    forge_ws_url: str = "ws://127.0.0.1:8000/agents/ws/optimus"
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


settings = Settings()


# ── Managed-env override store (desktop Configuration tab) ───────────────────
# A tiny KEY=VALUE reader/writer over _MANAGED_ENV. Deliberately not a full
# dotenv parser — we only ever write what we wrote (simple, quoted values) and
# read it back. pydantic re-reads the file at startup, so writes here take full
# effect on the next boot; the router also live-updates the in-memory `settings`
# object for values that are read lazily (feature flags, thresholds, model refs).

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
    key (falls back to the checked-in .env / default). Atomic-ish rewrite."""
    current = read_managed_env()
    for key, value in updates.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# SPEDA managed overrides — written by the desktop Configuration tab.",
        "# Edit in the app (Settings → Configuration). Values here win over .env.",
        "",
    ]
    for key in sorted(current):
        lines.append(f"{key}={_dq(current[key])}")
    _MANAGED_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
