"""
Configuration schema — the single catalog of everything the owner can configure
from the desktop Settings → Configuration tab.

Each entry maps one `Settings` field (app/config.py) to UI metadata: which group
it belongs to, how to render it, whether it's a secret (masked on read), and
whether changing it needs a backend restart to take effect. routers/config.py
turns this into a GET (current values, secrets masked) and a PUT (persist to the
managed .env + live-apply where safe).

Adding a new setting = add its field to Settings AND a row here. Nothing else.
"""

from dataclasses import dataclass, field as dc_field
from typing import Literal

FieldType = Literal["text", "password", "bool", "int", "select", "url"]


@dataclass(frozen=True)
class ConfigField:
    key: str                          # Settings attribute name (env var = key.upper())
    label: str
    type: FieldType = "text"
    secret: bool = False              # masked on read; only overwritten when re-sent
    requires_restart: bool = True     # most clients are built once at startup
    help: str = ""
    placeholder: str = ""
    options: list[str] = dc_field(default_factory=list)  # for type == "select"


@dataclass(frozen=True)
class ConfigGroup:
    id: str
    label: str
    blurb: str
    fields: list[ConfigField]


# Fields whose value the running process reads LAZILY (every call), so a live
# update to the in-memory settings object takes effect without a restart.
# Everything else is baked into a client/loop at startup → restart required.
_LIVE = False  # requires_restart=False shorthand for readability below

CONFIG_GROUPS: list[ConfigGroup] = [
    ConfigGroup(
        "llm", "LLM Providers & Routing",
        "API keys and default model routing across every provider. A bare model "
        "ref means Anthropic; otherwise 'provider:model' (e.g. openai:gpt-4o).",
        [
            ConfigField("anthropic_api_key", "Anthropic API Key", "password", secret=True,
                        help="Primary provider. Powers Claude models."),
            ConfigField("openai_api_key", "OpenAI API Key", "password", secret=True,
                        help="Enables openai:* refs and embeddings (semantic recall)."),
            ConfigField("gemini_api_key", "Google Gemini API Key", "password", secret=True,
                        help="Enables gemini:* model refs."),
            ConfigField("zai_api_key", "z.ai (GLM) API Key", "password", secret=True,
                        help="OpenAI-compatible. Enables zai:* refs."),
            ConfigField("deepseek_api_key", "DeepSeek API Key", "password", secret=True,
                        help="OpenAI-compatible. Enables deepseek:* refs."),
            ConfigField("nvidia_api_key", "NVIDIA NIM API Key", "password", secret=True,
                        help="Free key from build.nvidia.com. Enables the full NIM open-model "
                             "catalog (Llama, Nemotron, DeepSeek, Qwen…) as nvidia:* refs, "
                             "listed live in the model picker."),
            ConfigField("ollama_base_url", "Ollama Base URL", "url",
                        help="Local models (dead-zone/offline). e.g. http://localhost:11434/v1"),
            ConfigField("embedding_model", "Embedding Model", "text", requires_restart=_LIVE,
                        help="Always OpenAI. Used for semantic memory recall."),
            ConfigField("llm_main_model", "Main Model Override", "text", requires_restart=_LIVE,
                        help="Override the profile's user-facing model. Empty = profile default.",
                        placeholder="e.g. claude-sonnet-4-6"),
            ConfigField("llm_background_model", "Background Model Override", "text", requires_restart=_LIVE,
                        help="Override the model for n8n/agent/background tasks."),
            ConfigField("llm_fallback_chain", "Fallback Chain", "text", requires_restart=_LIVE,
                        help="Comma-separated provider:model refs tried when the primary fails."),
            ConfigField("legion_model_override", "Legion Worker Model Override", "text", requires_restart=_LIVE,
                        help="Pin all Legion workers to one 'provider:model'. Empty = automatic: "
                             "cheap same-provider model for low/medium-effort workers, parent model for high."),
        ],
    ),
    ConfigGroup(
        "behavior", "Behavior & Cost",
        "Spend controls, offline mode, conversation compaction, and prompt caching.",
        [
            ConfigField("budget_mode", "Budget Mode", "bool", requires_restart=_LIVE,
                        help="Hard frugality: the Legion disabled + concise output. (Runtime toggle.)"),
            ConfigField("dead_zone_mode", "Dead Zone Mode", "select", requires_restart=_LIVE,
                        options=["auto", "on", "off"],
                        help="Offline operation. auto probes connectivity; on forces local-only."),
            ConfigField("compaction_enabled", "Conversation Compaction", "bool", requires_restart=_LIVE,
                        help="Summarize old turns on long chats to cap per-turn cost."),
            ConfigField("compaction_threshold_tokens", "Compaction Threshold (tokens)", "int",
                        requires_restart=_LIVE, help="Compact once live history exceeds this."),
            ConfigField("compaction_keep_tokens", "Compaction Keep (tokens)", "int",
                        requires_restart=_LIVE, help="Most-recent tokens always kept verbatim."),
            ConfigField("prompt_cache_ttl", "Prompt Cache TTL", "select",
                        options=["5m", "1h"], help="Tools/system prefix cache lifetime."),
            ConfigField("lazy_tools", "Lazy Tool Loading", "bool",
                        help="Only always-on servers in the prefix; the rest load on demand."),
            ConfigField("mcp_enabled", "MCP Servers Enabled", "text",
                        help="Comma-separated MCP servers to connect at startup."),
            ConfigField("always_on_servers", "Always-on Servers", "text",
                        help="Servers whose tools are always in the prompt prefix."),
        ],
    ),
    ConfigGroup(
        "telegram", "Telegram Channel",
        "One bot per agent — chat + notifications. Set a token per agent; SPEDA's "
        "is the fallback voice. Switch ingress to polling (dev) or webhook (prod).",
        [
            ConfigField("telegram_mode", "Ingress Mode", "select",
                        options=["off", "polling", "webhook"],
                        help="off = outbound only; polling = dev; webhook = prod (needs base+secret)."),
            ConfigField("telegram_webhook_base", "Webhook Base URL", "url",
                        help="Public HTTPS base, e.g. https://speda.example.com (webhook mode)."),
            ConfigField("telegram_webhook_secret", "Webhook Secret", "password", secret=True,
                        help="Shared secret_token validated on every inbound webhook."),
            ConfigField("telegram_bot_token", "SPEDA Bot Token (legacy alias)", "password", secret=True,
                        help="From @BotFather. Used for SPEDA if the per-agent token is unset."),
            ConfigField("telegram_bot_token_speda", "SPEDA Bot Token", "password", secret=True),
            ConfigField("telegram_bot_token_sentinel", "Sentinel Bot Token", "password", secret=True),
            ConfigField("telegram_bot_token_nightcrawler", "NightCrawler Bot Token", "password", secret=True),
            ConfigField("telegram_bot_token_ultron", "Ultron Bot Token", "password", secret=True),
            ConfigField("telegram_bot_token_centurion", "Centurion Bot Token", "password", secret=True),
            ConfigField("telegram_bot_token_atomix", "Atomix Bot Token", "password", secret=True),
            ConfigField("telegram_bot_token_orion", "Orion Bot Token", "password", secret=True),
            ConfigField("telegram_bot_token_optimus", "Optimus Bot Token", "password", secret=True),
        ],
    ),
    ConfigGroup(
        "automation", "Automation (n8n)",
        "n8n is the sole scheduling organ. SPEDA is a control plane over its REST API.",
        [
            ConfigField("n8n_api_url", "n8n API URL", "url",
                        help="e.g. http://n8n:5678 (compose) or your public n8n host."),
            ConfigField("n8n_api_key", "n8n API Key", "password", secret=True,
                        help="n8n → Settings → n8n API → create key."),
            ConfigField("n8n_secret", "n8n Trigger Secret", "password", secret=True,
                        help="Shared X-N8N-Secret validated on POST /trigger."),
            ConfigField("speda_callback_url", "SPEDA Callback URL", "url",
                        help="URL n8n uses to call back into SPEDA's /trigger endpoint."),
        ],
    ),
    ConfigGroup(
        "google", "Google Workspace",
        "OAuth client for Gmail, Calendar, Drive, Contacts. Prefer the in-app "
        "'Sign in with Google' for the refresh token.",
        [
            ConfigField("google_client_id", "Google Client ID", "text"),
            ConfigField("google_client_secret", "Google Client Secret", "password", secret=True),
            ConfigField("google_refresh_token", "Google Refresh Token", "password", secret=True,
                        help="Usually captured by the Connections sign-in flow."),
            ConfigField("google_oauth_redirect", "OAuth Redirect", "url"),
        ],
    ),
    ConfigGroup(
        "notion", "Notion",
        "OAuth client for the hosted Notion MCP. Prefer the in-app 'Sign in with Notion'.",
        [
            ConfigField("notion_client_id", "Notion Client ID", "text"),
            ConfigField("notion_client_secret", "Notion Client Secret", "password", secret=True),
            ConfigField("notion_api_key", "Notion API Key (legacy)", "password", secret=True),
            ConfigField("notion_oauth_redirect", "OAuth Redirect", "url"),
            ConfigField("notion_version", "Notion API Version", "text",
                        help="Sent on every Notion request, e.g. 2022-06-28."),
        ],
    ),
    ConfigGroup(
        "search", "Search & Data APIs",
        "Web search, deep search, finance, and code providers used by skills/MCP.",
        [
            ConfigField("tavily_api_key", "Tavily API Key", "password", secret=True),
            ConfigField("exa_api_key", "Exa API Key", "password", secret=True),
            ConfigField("brave_search_api_key", "Brave Search API Key", "password", secret=True),
            ConfigField("alpha_vantage_api_key", "Alpha Vantage API Key", "password", secret=True),
            ConfigField("github_token", "GitHub Token", "password", secret=True),
        ],
    ),
    ConfigGroup(
        "osint", "OSINT / Threat Intelligence",
        "Optional keys for NightCrawler/Centurion. Most tools also run keyless.",
        [
            ConfigField("abuseipdb_api_key", "AbuseIPDB Key", "password", secret=True),
            ConfigField("abuse_ch_api_key", "abuse.ch Auth-Key", "password", secret=True),
            ConfigField("otx_api_key", "AlienVault OTX Key", "password", secret=True),
            ConfigField("shodan_api_key", "Shodan Key", "password", secret=True),
            ConfigField("hunter_api_key", "Hunter.io Key", "password", secret=True),
            ConfigField("etherscan_api_key", "Etherscan Key", "password", secret=True),
            ConfigField("intelx_api_key", "Intelligence X Key", "password", secret=True),
            ConfigField("blockchair_api_key", "Blockchair Key", "password", secret=True),
        ],
    ),
    ConfigGroup(
        "system", "System & Ops",
        "Sandbox, OSS adapters, temp outputs, and Orion's privileged host operations.",
        [
            ConfigField("owner_timezone", "Owner Timezone", "text", requires_restart=_LIVE,
                        help="IANA name the app presents time in (server stays UTC). "
                             "e.g. Europe/Istanbul, America/New_York. Takes effect immediately.",
                        placeholder="Europe/Istanbul"),
            ConfigField("sandbox_url", "Sandbox URL", "url",
                        help="Isolated container for run_command. Empty disables the tool."),
            ConfigField("gpt_researcher_url", "GPT-Researcher URL", "url"),
            ConfigField("shannon_url", "Shannon URL", "url"),
            ConfigField("temp_outputs_dir", "Temp Outputs Dir", "text",
                        help="Where generated files land (24h n8n cleanup)."),
            ConfigField("system_ops_enabled", "Host Ops (Orion/Optimus)", "bool",
                        help="Privileged host operations for Orion & Optimus. Off by default."),
            ConfigField("system_ops_root", "Host Ops Write Jail", "text",
                        help="Confines any system_ops file write to this subtree."),
            ConfigField("system_ops_timeout", "Host Ops Timeout (s)", "int"),
            ConfigField("system_ops_host", "Host Ops SSH Target", "text", requires_restart=_LIVE,
                        help="user@host the containerized backend SSHes to for REAL host "
                             "maintenance, e.g. root@host.docker.internal. Empty = run locally.",
                        placeholder="root@host.docker.internal"),
            ConfigField("system_ops_ssh_port", "Host Ops SSH Port", "int", requires_restart=_LIVE,
                        help="The host's sshd port."),
            ConfigField("system_ops_ssh_key", "Host Ops SSH Key Path", "text", requires_restart=_LIVE,
                        help="Private key path inside the container (default: data dir/host_ops_key)."),
        ],
    ),
    ConfigGroup(
        "security", "Security & Server",
        "Service credential, CORS, and diagnostics. Change the API key with care — "
        "the desktop app must use the same value.",
        [
            ConfigField("speda_api_key", "Service API Key (X-API-Key)", "password", secret=True,
                        help="Validated on every request. The desktop app must match it."),
            ConfigField("house_party_passphrase", "House Party Passphrase", "password", secret=True,
                        help="Owner passphrase to engage the all-hands protocol."),
            ConfigField("cors_allowed_origins", "CORS Allowed Origins", "text",
                        help="Comma-separated browser origins. Empty = none (desktop is unaffected)."),
            ConfigField("log_level", "Log Level", "select", requires_restart=_LIVE,
                        options=["INFO", "DEBUG", "WARNING", "ERROR"]),
            ConfigField("debug", "Debug Mode", "bool",
                        help="Enables docs and verbose logging. Never on an internet-facing server."),
        ],
    ),
]

# Flat key → field lookup for the PUT validator.
FIELD_BY_KEY: dict[str, ConfigField] = {
    f.key: f for g in CONFIG_GROUPS for f in g.fields
}
