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

    # Prompt cache TTL — "5m" or "1h".
    # 5m is cheaper for bursty/personal use: the write premium is 1.25x vs 1h's
    # 2x, and short sessions stay warm within the 5-min window anyway. 1h only
    # wins for sustained sessions with 5-60 min gaps. A cache write is only worth
    # it if the prefix is READ back more than ~2x within the TTL — so keep the
    # prefix small (few tools) and the 5m write barely costs anything.
    prompt_cache_ttl: str = "5m"

    # Which MCP servers to load. Each tool is cached into the prompt prefix on
    # EVERY request, so a big set means an expensive cold cache-write each time
    # the cache goes cold (which dominated cost during testing). LEAN BY DEFAULT:
    # just web search (~12k prefix → cheap writes). Enable Gmail/Calendar/Notion
    # from the Connections panel only when you need them — and ideally on Tier 2
    # (450k ITPM) so the big prefix doesn't 429. Override via MCP_ENABLED.
    mcp_enabled: str = "tavily"

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
