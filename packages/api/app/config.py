import logging
import logging.config
import json
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_DATA_DIR = Path.home() / ".speda"


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
        env_file=".env",
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
    # z.ai (Zhipu GLM) — OpenAI-compatible endpoint. Get a key from
    # https://z.ai/manage-apikey/apikey-list. Enables refs like "zai:glm-4.6".
    zai_api_key: str = ""
    # DeepSeek — OpenAI-compatible endpoint (api.deepseek.com). Get a key from
    # https://platform.deepseek.com. Enables refs like "deepseek:deepseek-v4-pro".
    deepseek_api_key: str = ""
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

    # ── Telegram (proactive outbound delivery) ───────────────────────────────
    # When an n8n watcher fires, SPEDA composes a message and pushes it here.
    telegram_bot_token: str = ""   # from @BotFather
    # Captured at runtime via the in-app "Connect Telegram" flow (runtime_state);
    # this is only a fallback for headless setups.
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
    always_on_servers: str = "tavily"

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

    # Model for Task sub-agents. Defaults to Haiku — research/synthesis grunt work
    # doesn't need Sonnet, Haiku is ~5x cheaper, and crucially it uses a SEPARATE
    # rate-limit pool, so a sub-agent's burst of calls doesn't stack against the
    # main Sonnet loop's tokens-per-minute limit (the tier-0 429 cause). The
    # user-facing answer is still composed by the main loop on the chosen model.
    # Set empty to run sub-agents on the same model as the parent.
    sub_agent_model: str = "claude-haiku-4-5-20251001"

    # Budget mode — a HARD, enforced frugality switch (not a prompt suggestion):
    #   - the Task sub-agent tool is not registered at all (impossible to spawn)
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

    # Sandbox — the isolated container SPEDA runs commands in ("capable computer").
    # In Docker this is the sandbox service; empty disables the run_command tool.
    sandbox_url: str = "http://localhost:9000"


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
