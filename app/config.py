import logging
import logging.config
import json
import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AliasChoices, Field, SecretStr

_DATA_DIR = Path(os.environ.get('SPEDA_DATA_DIR', str(Path.home() / '.speda')))


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
        rendered = json.dumps(log_data)
        # Includes provider/connector exception text: query-authenticated MCP
        # endpoints must not put their credential into HTTP diagnostic logs.
        current = globals().get('settings')
        if current is not None:
            for field in type(current).model_fields:
                if any(marker in field for marker in ('api_key', 'token', 'secret', 'password')):
                    value = getattr(current, field, None)
                    if isinstance(value, str) and len(value) >= 4 and value not in {'not-set','dev-key','dev-n8n-secret'}:
                        rendered = rendered.replace(json.dumps(value)[1:-1], '[REDACTED]')
                        # Per-agent bot credentials are a JSON map. HTTP client
                        # diagnostics contain individual tokens inside URLs.
                        try: nested = json.loads(value)
                        except (ValueError, TypeError): nested = None
                        if isinstance(nested, dict):
                            for secret in nested.values():
                                if isinstance(secret, str) and len(secret) >= 4:
                                    rendered = rendered.replace(json.dumps(secret)[1:-1], '[REDACTED]')
        return rendered


class Settings(BaseSettings):
    @classmethod
    def settings_customise_sources(cls,settings_cls,init_settings,env_settings,dotenv_settings,file_secret_settings):
        from app.services.config_store import managed_settings
        return init_settings,managed_settings,env_settings,dotenv_settings,file_secret_settings

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Anthropic
    anthropic_api_key: str = "not-set"
    openai_api_key: str = ""
    gemini_api_key: str = ""
    zai_api_key: str = ""
    deepseek_api_key: str = ""
    nvidia_api_key: str = ""
    kimi_api_key: str = ""
    moonshot_api_key: str = ""
    ollama_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    zai_base_url: str = "https://api.z.ai/api/paas/v4"
    deepseek_base_url: str = "https://api.deepseek.com"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    kimi_base_url: str = "https://api.moonshot.ai/v1"
    ollama_base_url: str = "http://localhost:11434/v1"
    vertex_project_id: str = ""
    vertex_location: str = "global"
    vertex_credentials_file: str = ""
    llm_main_model: str = ""
    llm_background_model: str = ""
    llm_request_timeout_seconds: float = 90
    chat_max_output_tokens: int = 16384

    # Database
    database_url: str = "sqlite+aiosqlite:///./speda.db"
    memory_capture_enabled: bool = True
    iacon_telegram_token: str = ""
    iacon_telegram_chat_id: int = 0
    iacon_telegram_owner_id: int = 0
    hisar_base_url: str = "http://localhost:8002"
    hisar_library_sync_token: str = ""
    hisar_library_reader_token: str = ""
    hisar_machine_token: str = ""
    library_max_bytes: int = 25 * 1024 * 1024
    library_max_pages: int = 200
    external_integrations_enabled: bool = True
    orchestration_profiles_enabled: bool = True
    orchestration_rooms_enabled: bool = True
    project_execution_enabled: bool = False
    project_supervision_enabled: bool = True
    project_workspace_root: str = Field(default="", validation_alias=AliasChoices(
        "project_workspace_root", "PROJECT_WORKSPACE_ROOT", "FORGE_WORKSPACE_ROOT"))
    project_state_root: str = ""
    project_host_id: str = "local"
    project_cell_backend: str = "docker"
    project_cell_image: str = Field(default='', description='Optional trusted prebuilt FORGE image with the project toolchain and dependencies; workers retain denied networking.')
    project_cell_images: str = Field(default='{}', description='Trusted JSON map of coder, reviewer and pentester to prebuilt Docker images. Per-role images override the shared image; workers retain denied networking.')
    project_release_targets_file: str = Field(default='', description='Trusted host JSON file of deployment argv, verification argv and allowed environment names. Empty disables publication.')
    optimus_github_account: str = ""
    optimus_github_workspace_token: SecretStr = SecretStr("")
    optimus_github_commit_email: str = ""
    optimus_github_share_with: str = ""
    optimus_github_public_forks_in_chat: bool = False

    # Auth
    speda_api_key: str = "dev-key"
    n8n_secret: str = "dev-n8n-secret"

    # App
    http_host: str = '0.0.0.0'
    http_port: int = Field(default=8000,ge=1,le=65535)
    debug: bool = False
    log_level: str = "INFO"
    owner_timezone: str = "Europe/Istanbul"
    agent_language: str = ""
    cors_allowed_origins: str = ""
    fcm_credentials_file: str = ""
    fcm_project_id: str = ""
    google_maps_api_key: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    google_oauth_redirect: str = "http://localhost:8000/oauth/google/callback"
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_refresh_token: str = ""
    microsoft_oauth_redirect: str = "http://localhost:8000/oauth/microsoft/callback"
    microsoft_tenant: str = "common"
    owner_home_lat: float | None = None
    owner_home_lng: float | None = None
    nosyapi_api_key: str = ""
    aircraft_tracking_base_url: str = "https://api.adsb.lol"
    abuseipdb_api_key: str = ""
    abuse_ch_api_key: str = ""
    otx_api_key: str = ""
    shodan_api_key: str = ""
    hunter_api_key: str = ""
    etherscan_api_key: str = ""
    blockchair_api_key: str = ""
    intelx_api_key: str = ""
    mcp_enabled: str = "tavily,google_gmail,google_calendar,google_tasks,microsoft_outlook,notion"
    mcp_connections_file: str = ""
    # Operator-approved exact package versions; no network 'latest' resolution at startup.
    mcp_package_pins: str = "{}"
    playwright_mcp_url: str = ""
    browser_url: str = ""
    browser_token: str = ""
    browser_max_chars: int = 12000
    browser_max_images: int = 6
    browser_fallback_enabled: bool = True
    news_poll_enabled: bool = True
    news_extra_feeds: str = ''
    news_retention_days: int = 14
    news_quota_deep_dive: int = 50
    news_quota_auto_flag: int = 50
    news_quota_digest: int = 50
    news_flash_cooldown_min: int = 120
    newsdata_api_key: str = ''

    # Temp outputs
    temp_outputs_dir: str = "/tmp/speda_outputs"

    # MCP API keys (optional — servers degrade gracefully if missing)
    notion_api_key: str = ""
    notion_client_id: str = ""
    notion_client_secret: str = ""
    notion_oauth_redirect: str = "http://localhost:8000/oauth/notion/callback"
    brave_search_api_key: str = ""
    alpha_vantage_api_key: str = ""
    tavily_api_key: str = ""
    exa_api_key: str = ""
    github_token: str = ""
    house_party_passphrase: str = ""
    solus_ui_dir: str = ""  # Operator-built static Solus assets; no owner data.
    onyx_api_url: str = ""
    onyx_api_token: str = ""
    sandbox_url: str = ""  # Explicit isolated sidecar only; never host fallback.
    n8n_api_url: str = ""
    n8n_api_key: str = ""
    speda_callback_url: str = "http://speda:8000"
    telegram_bot_token: str = ""
    telegram_bot_tokens: str = "{}"  # Exact agent-id -> token mapping.
    telegram_webhook_secret: str = ""
    telegram_owner_id: int = 0
    damage_control_enabled: bool = False
    azure_speech_key: str = ""
    azure_speech_region: str = "westeurope"
    elevenlabs_api_key: str = ""
    tts_default_voice: str = "azure:tr-TR-EmelNeural"
    tts_locale: str = "tr-TR"
    voice_canvas_enabled: bool = Field(default=True, description="Enable visual-first canvas presentation for desktop/web turns that explicitly select canvas mode. Applies on restart.")
    voice_canvas_max_panels: int = Field(default=24, ge=1, le=32, description="Maximum authored visual panels per canvas turn. The board scrolls; this does not increase token or spending allowances. Applies on restart.")

    # OSS Adapter URLs
    gpt_researcher_url: str = "http://localhost:8001"
    shannon_url: str = "http://localhost:9000"


class DeploymentSettings(Settings):
    @classmethod
    def settings_customise_sources(cls,settings_cls,init_settings,env_settings,dotenv_settings,file_secret_settings):
        return init_settings,env_settings,dotenv_settings,file_secret_settings


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
