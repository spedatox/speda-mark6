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

    # Database — SQLite by default (no server needed); override with postgresql+asyncpg:// for prod
    database_url: str = f"sqlite+aiosqlite:///{_DATA_DIR / 'speda.db'}"

    # Auth
    speda_api_key: str = "dev-key"
    n8n_secret: str = "dev-n8n-secret"

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

    # MCP API keys (optional — servers degrade gracefully if missing)
    notion_api_key: str = ""
    brave_search_api_key: str = ""
    alpha_vantage_api_key: str = ""
    tavily_api_key: str = ""
    exa_api_key: str = ""
    github_token: str = ""

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
