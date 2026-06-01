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

    # Prompt cache TTL — "1h" (survives restarts, ~1 write/hour) or "5m".
    # The stable prefix (tools + core prompt + memory) is content-keyed at
    # Anthropic, so 1h dramatically cuts cache-write cost for active sessions.
    prompt_cache_ttl: str = "1h"

    # Which MCP servers to load (comma-separated server names). Each tool a
    # server contributes is cached into the prompt prefix on EVERY request, so
    # loading everything (notion=22 tools, filesystem=14, arxiv=10 …) bloats the
    # prefix to ~38k tokens — which exceeds the tier-0 rate limit of 30k input
    # tokens/minute and makes every cold call 429. Keep this lean; enable a
    # server only when you actually use it. "all" loads every configured server.
    # Heavy servers (notion=22, filesystem=14, arxiv=10) are OFF by default —
    # add them here when you need them and your tier can afford the prefix.
    # Default keeps ~10 tools (~12k prefix) — well under the tier-0 30k/min cap.
    mcp_enabled: str = "tavily,exa,fetch,alpha_vantage"

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

    # OSS Adapter URLs
    gpt_researcher_url: str = "http://localhost:8001"
    shannon_url: str = "http://localhost:9000"


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
