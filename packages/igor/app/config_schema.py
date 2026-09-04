# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

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

FieldType = Literal["text", "password", "bool", "int", "float", "select", "url"]


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
        "language", "Language",
        "The one language the system speaks. It sets what every agent WRITES, what "
        "the replies are spoken in, what the microphone is decoded as, and what the "
        "clients render their own buttons in — one value, so the three halves of a "
        "spoken conversation can never disagree. The clients put this on a switch "
        "next to the message box; this is the same setting.",
        [
            ConfigField("agent_language", "Language", "select", requires_restart=_LIVE,
                        options=["tr", "en"],
                        help="tr = Türkçe, en = English. Stamped into every agent's system "
                             "prompt as a hard contract: not one word of the other language, "
                             "whatever language the owner, a tool result or a web page happens "
                             "to be in. Proper nouns, code and identifiers are never translated."),
            ConfigField("language_enforcement", "Check Replies", "bool", requires_restart=_LIVE,
                        help="Scan finished replies for words in the wrong language and log "
                             "what leaked. The prompt contract holds either way — turning this "
                             "off only stops you hearing about the misses."),
            ConfigField("language_repair", "Repair Leaks", "bool", requires_restart=_LIVE,
                        help="On a detected leak, rewrite the text into the chosen language "
                             "with one cheap-model pass. Runs only where the owner has not "
                             "already seen the text — voice synthesis and automation pushes. A "
                             "streamed chat reply is on screen before the check finishes, so "
                             "there it is flagged, never rewritten."),
            ConfigField("language_leak_tolerance", "Leak Tolerance", "int", requires_restart=_LIVE,
                        help="How many foreign words a reply may contain before it counts as a "
                             "leak. 1 is the strict reading this setting exists for."),
        ],
    ),
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
                        help="Enables gemini:* model refs (AI Studio — billed to its own "
                             "prepay balance, NOT to Google Cloud credits)."),
            ConfigField("vertex_project_id", "Vertex AI Project ID", "text",
                        help="Google Cloud project linked to the billing account holding your "
                             "credits. Setting it enables vertex:* refs — the same Gemini "
                             "models as gemini:*, but billed as Cloud usage, which is the only "
                             "route Developer Program / AI Pro credits can pay for.",
                        placeholder="e.g. speda-mark6"),
            ConfigField("vertex_credentials_file", "Vertex Service-Account JSON", "text",
                        help="Path to a service-account key with roles/aiplatform.user. Leave "
                             "empty to use ADC (`gcloud auth application-default login`). "
                             "There is no API-key option: Vertex's chat endpoint accepts OAuth "
                             "only, whatever key the console shows you.",
                        placeholder="/path/to/service-account.json"),
            ConfigField("vertex_location", "Vertex AI Location", "text",
                        help="'global' is the default and the cheapest — regional endpoints "
                             "carry a ~10% uplift.",
                        placeholder="global"),
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
        "One bot per agent — chat + notifications. Set a token per agent; Speda's "
        "is the fallback voice. Switch ingress to polling (dev) or webhook (prod).",
        [
            ConfigField("telegram_mode", "Ingress Mode", "select",
                        options=["off", "polling", "webhook"],
                        help="off = outbound only; polling = dev; webhook = prod (needs base+secret)."),
            ConfigField("telegram_webhook_base", "Webhook Base URL", "url",
                        help="Public HTTPS base, e.g. https://speda.example.com (webhook mode)."),
            ConfigField("telegram_webhook_secret", "Webhook Secret", "password", secret=True,
                        help="Shared secret_token validated on every inbound webhook."),
            ConfigField("telegram_bot_token", "Speda Bot Token (legacy alias)", "password", secret=True,
                        help="From @BotFather. Used for Speda if the per-agent token is unset."),
            ConfigField("telegram_bot_token_speda", "Speda Bot Token", "password", secret=True),
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
        "n8n is the sole scheduling organ. Speda is a control plane over its REST API.",
        [
            ConfigField("n8n_api_url", "n8n API URL", "url",
                        help="e.g. http://n8n:5678 (compose) or your public n8n host."),
            ConfigField("n8n_api_key", "n8n API Key", "password", secret=True,
                        help="n8n → Settings → n8n API → create key."),
            ConfigField("n8n_secret", "n8n Trigger Secret", "password", secret=True,
                        help="Shared X-N8N-Secret validated on POST /trigger."),
            ConfigField("speda_callback_url", "Speda Callback URL", "url",
                        help="URL n8n uses to call back into Speda's /trigger endpoint."),
            ConfigField("automation_run_retention_days", "Run History Retention (days)", "int",
                        requires_restart=_LIVE,
                        help="How long each automation's run history (status, delivery, "
                             "the report text) is kept before being pruned."),
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
        "microsoft", "Microsoft 365 (University Mail)",
        "OAuth client for the owner's Outlook / school mailbox. Register an app in "
        "Azure Portal → App registrations, add a Web redirect matching the URL below, "
        "create a client secret, and grant the DELEGATED permissions offline_access, "
        "Mail.ReadWrite, Mail.Send and User.Read. Then use the in-app "
        "'Connect Microsoft 365' button for the refresh token.",
        [
            ConfigField("microsoft_client_id", "Application (client) ID", "text"),
            ConfigField("microsoft_client_secret", "Client Secret Value", "password", secret=True,
                        help="The secret VALUE, not the secret id — Azure shows the value once."),
            ConfigField("microsoft_tenant", "Tenant", "text",
                        help="'common' (work/school + personal, the default), 'organizations', "
                             "or the university's tenant GUID if IT blocks multi-tenant apps.",
                        placeholder="common"),
            ConfigField("microsoft_refresh_token", "Microsoft Refresh Token", "password", secret=True,
                        help="Usually captured by the Connections sign-in flow, and rotated "
                             "automatically after that."),
            ConfigField("microsoft_oauth_redirect", "OAuth Redirect", "url",
                        help="Must match a Web platform redirect URI on the app registration."),
            ConfigField("school_mail_domain", "School Mail Domain", "text", requires_restart=_LIVE,
                        help="The sender domain the school-mail watch treats as 'the "
                             "university'. Used by the shipped n8n workflow's default rule.",
                        placeholder="ostimteknik.edu.tr"),
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
        "maps", "Maps & Navigation",
        "Powers get_route (live-traffic directions) and find_places, rendered as "
        "inline Stark maps. One Google Cloud key with Routes API, Places API (New) "
        "and Geocoding API enabled (billing account required). Server-side only — "
        "the phone never holds this key.",
        [
            ConfigField("google_maps_api_key", "Google Maps API Key", "password", secret=True,
                        requires_restart=_LIVE,
                        help="Enables the get_route / find_places tools. Read live on every "
                             "call — takes effect immediately, no restart. Empty disables both."),
            ConfigField("owner_home_lat", "Home Latitude", "text", requires_restart=_LIVE,
                        help="Fallback route origin when no live location is shared (e.g. a "
                             "Telegram turn). Decimal degrees.", placeholder="41.0082"),
            ConfigField("owner_home_lng", "Home Longitude", "text", requires_restart=_LIVE,
                        help="Fallback route origin longitude. Decimal degrees.",
                        placeholder="28.9784"),
        ],
    ),
    ConfigGroup(
        "recall", "Memory Recall Quality",
        "How hard recall has to try before it admits it does not know. The floors "
        "are the difference between 'not in memory, ask him' and a confident wrong "
        "answer; the fragment guard is what keeps the store answerable as it grows.",
        [
            ConfigField("recall_min_similarity", "Fact Relevance Floor", "float",
                        requires_restart=_LIVE,
                        help="Minimum cosine similarity a distilled fact must reach before "
                             "search_memory will return it at all. Below this, recall answers "
                             "'nothing matches' instead of handing back the nearest unrelated "
                             "row. Raise for precision, lower for reach; 0 restores the old "
                             "rank-only behaviour. Sweep it with evals/recall/run_eval.py.",
                        placeholder="0.30"),
            ConfigField("recall_message_min_similarity", "Transcript Relevance Floor", "float",
                        requires_restart=_LIVE,
                        help="The same floor for recall_conversations, over raw message text. "
                             "Looser than the fact floor because long messages score lower "
                             "cosines for the same relevance.",
                        placeholder="0.25"),
            ConfigField("memory_injected_file_max_chars", "Injected File Character Cap", "int",
                        requires_restart=_LIVE,
                        help="Per-file ceiling on what the always-injected memory files add to "
                             "every prompt. owner.md was 13.8 KB — 48% of the block — and the "
                             "declared 12 KB cap was only checked on write. Truncation keeps "
                             "whole sections and drops the MIDDLE, so the directives at a "
                             "file's end survive; nothing is removed from disk and the elided "
                             "part is still readable with the memory tool. 0 = inject whole."),
            ConfigField("memory_directory_collapse_above", "Collapse Folders Larger Than", "int",
                        requires_restart=_LIVE,
                        help="Folders in the injected directory listing with more files than "
                             "this are shown as a name and a count rather than enumerated. "
                             "/memories/projects alone is 33 entries in a listing re-sent every "
                             "turn; the names stay one `view` away. 0 = list everything."),
            ConfigField("relevant_recall_enabled", "Inject Relevant Facts Every Turn", "bool",
                        requires_restart=_LIVE,
                        help="Search the record with his own message each turn and put what "
                             "matches in front of the model, instead of waiting for it to "
                             "decide to call search_memory. The read-side twin of automatic "
                             "extraction — together they are what makes telling an agent "
                             "something enough for it to be remembered."),
            ConfigField("relevant_recall_limit", "Facts Injected Per Turn", "int",
                        requires_restart=_LIVE,
                        help="Kept small deliberately: this is a reminder, not a second memory "
                             "file, and a long block of near-misses teaches the model to skip "
                             "the section."),
            ConfigField("relevant_recall_max_chars", "Injected Facts Character Cap", "int",
                        requires_restart=_LIVE,
                        help="Hard ceiling on the injected block, independent of the count."),
            ConfigField("relevant_recall_min_query_chars", "Minimum Message Length To Search", "int",
                        requires_restart=_LIVE,
                        help="Shorter messages ('ok', 'devam') carry no retrievable intent and "
                             "would match on stopwords alone."),
            ConfigField("auto_extract_facts", "Extract Facts From Every Turn", "bool",
                        requires_restart=_LIVE,
                        help="Mine each exchange for the durable facts he stated, in the "
                             "background, instead of waiting for an agent to volunteer a "
                             "record_observation call. It waited a long time: 93 facts across "
                             "10,409 messages before this existed. This is the setting that "
                             "decides whether telling an agent something makes it remembered."),
            ConfigField("auto_extract_max_facts", "Max Facts Per Turn", "int",
                        requires_restart=_LIVE,
                        help="Ceiling on what one exchange may add. A turn that looks like it "
                             "holds twenty facts is usually a long answer being mined for "
                             "trivia, and flooding the store is how recall degraded before."),
            ConfigField("auto_extract_model", "Fact Extraction Model", "text",
                        requires_restart=_LIVE,
                        help="Model for that extraction. Empty = the background model. Runs "
                             "once per turn behind the response, so cost matters more than "
                             "brilliance.",
                        placeholder="e.g. deepseek:deepseek-v4-flash"),
            ConfigField("observation_min_content_length", "Minimum Fact Length", "int",
                        requires_restart=_LIVE,
                        help="Characters an observation must reach to be recorded. Short "
                             "entries are almost always bullets lifted out of a file "
                             "('a table of incomes'), which rank near the top of every query "
                             "and answer none of them."),
            ConfigField("recall_translate_queries", "Translate Turkish Queries", "bool",
                        requires_restart=_LIVE,
                        help="The memory store is English; his questions often are not. With this "
                             "off, a Turkish question searches an English store directly and finds "
                             "roughly half of what the same question in English finds. Translated "
                             "queries are cached per process, so a repeat costs nothing."),
            ConfigField("recall_translation_model", "Query Translation Model", "text",
                        requires_restart=_LIVE,
                        help="Model used for that translation. Empty = the background model. Small, "
                             "cached and on the retrieval hot path, so pick the cheapest capable one.",
                        placeholder="e.g. deepseek:deepseek-v4-flash"),
            ConfigField("observation_require_subject", "Require a Named Subject", "bool",
                        requires_restart=_LIVE,
                        help="Reject observations that never say who or what they are about. "
                             "On is strongly recommended: a subject-less fact is unfindable "
                             "once it is retrieved on its own."),
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
            ConfigField("aircraft_tracking_base_url", "Aircraft Tracking API Base URL", "url",
                        requires_restart=_LIVE,
                        help="Powers track_aircraft. Defaults to adsb.lol's free, keyless feed "
                             "(airplanes.live now 403s unregistered callers). Override to point "
                             "at another compatible mirror (api.adsb.one) if this one is ever "
                             "rate-limited or down.",
                        placeholder="https://api.adsb.lol"),
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
            ConfigField("browser_url", "Browser URL", "url",
                        help="Playwright container behind browse_page / browser_act / "
                             "portal_login. Empty disables all three."),
            ConfigField("browser_token", "Browser Token", "password", secret=True,
                        help="Shared secret with the browser container — it holds live "
                             "session cookies for your portals, so set it."),
            ConfigField("browser_fallback_enabled", "Render When Fetch Comes Back Empty", "bool",
                        requires_restart=_LIVE,
                        help="Lets a page watch retry through the browser when a plain "
                             "fetch finds no readable text (a JS-rendered results page). "
                             "Costs a render instead of a GET on those polls."),
            ConfigField("browser_max_chars", "Browser Page Text Cap", "int", requires_restart=_LIVE,
                        help="How much rendered page text one browse_page call may return."),
            ConfigField("browser_max_images", "Page Images Returned", "int", requires_restart=_LIVE,
                        help="How many of a page's pictures come back with it, so an agent can "
                             "put a real photo on a presentation board instead of inventing a "
                             "URL. The page's own share image leads, then its largest content "
                             "images. 0 turns it off.",
                        placeholder="6"),
            ConfigField("playwright_mcp_url", "Public-Web Browser (playwright-mcp) URL", "url",
                        help="A SEPARATE, isolated container running the official "
                             "@playwright/mcp server — full click/drag/evaluate/tabs/"
                             "dialogs/upload tool parity, for the OPEN PUBLIC WEB ONLY. "
                             "Never a route to one of your saved logins — that stays "
                             "Browser URL above, on purpose. Empty disables it."),
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
        "voice", "Voice",
        "Azure Speech powers both halves of voice mode — spoken replies and dictation — "
        "from ONE key. Synthesis bills per character with 0.5M free per month, and markup "
        "is stripped first so only spoken words are billed; recognition bills per audio hour "
        "with 5 free per month. ElevenLabs is a THIRD synthesis engine, spoken-replies-only "
        "(no dictation) — it is what gives each agent a distinct, non-Azure voice and is what "
        "an automation's \"reply as voice\" checkbox and Settings → Voices actually speak "
        "through when an agent is pinned to one of its voices. Leave a key empty to disable "
        "that engine; the others keep working.",
        [
            ConfigField("elevenlabs_api_key", "ElevenLabs API Key", "password", secret=True,
                        requires_restart=_LIVE,
                        help="From elevenlabs.io → Settings → API Keys. Enables elevenlabs:* "
                             "voice refs, the real per-account voice catalog in Settings → "
                             "Voices, and automation voice replies for any agent pinned to an "
                             "ElevenLabs voice there. Per-agent voice + tuning live in "
                             "Settings → Voices, not here — this is only the credential."),
            ConfigField("azure_speech_key", "Azure Speech Key", "password", secret=True,
                        requires_restart=_LIVE,
                        help="From the Azure Speech resource. Must match the region below."),
            ConfigField("azure_speech_region", "Azure Region", "text", requires_restart=_LIVE,
                        help="The Speech resource's region, e.g. westeurope. A key from another "
                             "region is rejected with HTTP 403.",
                        placeholder="westeurope"),
            ConfigField("tts_default_voice", "Default Voice", "text", requires_restart=_LIVE,
                        help="Fallback for agents whose profile names no voice. Prefer a "
                             "Multilingual voice — the two native Turkish ones (tr-TR-Emel/Ahmet) "
                             "are the stock CapCut voices and sound like it.",
                        placeholder="en-US-BrianMultilingualNeural"),
            ConfigField("tts_locale", "Spoken Locale Override", "text", requires_restart=_LIVE,
                        help="Normally EMPTY — synthesis follows the Language switch in the "
                             "client. Set a BCP-47 tag here only to pin a regional variant the "
                             "switch does not offer (e.g. en-GB). Separate from the voice on "
                             "purpose: a multilingual voice is named en-US-… while speaking "
                             "Turkish, and this is what tells it which language to read.",
                        placeholder="follows the Language switch"),
            ConfigField("tts_output_format", "Audio Format", "text", requires_restart=_LIVE,
                        help="Azure output-format token. MP3 plays natively in the client.",
                        placeholder="audio-24khz-48kbitrate-mono-mp3"),
            ConfigField("stt_locale", "Heard Locale Override", "text", requires_restart=_LIVE,
                        help="Normally EMPTY — the mic is decoded in the language the Language "
                             "switch is set to. Set a BCP-47 tag here only to dictate in a "
                             "different language than you are answered in. Recognition has no "
                             "multilingual fallback: a wrong tag returns confident nonsense.",
                        placeholder="follows the Language switch"),
        ],
    ),
    ConfigGroup(
        "canvas", "Canvas",
        "Voice mode is a PRESENTATION surface, not a chatbot that talks. The agent "
        "narrates while the screen carries the evidence — every figure a chart or a "
        "stat, every source its own window, the transcript demoted to a subtitle "
        "under the orb. These settings tune that. The word budgets are rendered into "
        "the agent's instructions and change how it WRITES; the rest is read by the "
        "clients and only changes how the board looks. Nothing here truncates a "
        "reply: a sentence cut off mid-word costs the same to synthesize as a "
        "finished one.",
        [
            ConfigField("canvas_enabled", "Canvas", "bool", requires_restart=_LIVE,
                        help="Off, voice mode falls back to the orb and a caption — still "
                             "spoken, no board, and the agent is no longer asked to present."),
            ConfigField("canvas_spoken_words", "Spoken Budget (words)", "int",
                        requires_restart=_LIVE,
                        help="Soft target for a normal spoken answer. A target, not a limit — "
                             "the agent is told to write to it, never cut off at it.",
                        placeholder="90"),
            ConfigField("canvas_briefing_words", "Briefing Budget (words)", "int",
                        requires_restart=_LIVE,
                        help="The same target for a full briefing or research readout, which "
                             "legitimately needs longer to walk a board of a dozen windows.",
                        placeholder="200"),
            ConfigField("canvas_max_panels", "Max Windows Per Turn", "int",
                        requires_restart=_LIVE,
                        help="A board past roughly ten windows is unreadable at any scale the "
                             "layout can find.",
                        placeholder="10"),
            ConfigField("canvas_reveal_stagger_ms", "Window Entrance Stagger (ms)", "int",
                        requires_restart=_LIVE,
                        help="Delay between one window arriving and the next. A board that "
                             "appears at once reads as a page load; one that assembles reads "
                             "as an instrument coming up. 0 lands them together.",
                        placeholder="160"),
            ConfigField("canvas_caption_lines", "Caption Lines", "int", requires_restart=_LIVE,
                        help="How many lines of live subtitle stay under the orb.",
                        placeholder="3"),
            ConfigField("canvas_activity_after_ms", "Show Activity After (ms)", "int",
                        requires_restart=_LIVE,
                        help="How long a spoken turn may stay silent before the board opens a "
                             "window showing what the machine is doing. A tool firing opens it "
                             "at once regardless. Raise it to keep short answers on the bare "
                             "orb for longer; lower it if a turn ever feels hung.",
                        placeholder="1200"),
            ConfigField("canvas_image_proxy", "Proxy Board Images", "bool", requires_restart=_LIVE,
                        help="Photos on the board are fetched by the SERVER and handed to the "
                             "client as bytes. Keep this on: the clients block remote images "
                             "outright, and loading one directly would tell its host that you "
                             "are looking — which on a research board is the whole problem. "
                             "Off, windows render without their pictures."),
            ConfigField("canvas_image_max_bytes", "Max Image Size (bytes)", "int",
                        requires_restart=_LIVE,
                        help="Ceiling on one proxied picture. A board image is a thumbnail; "
                             "past this it is something else and is refused.",
                        placeholder="8388608"),
            ConfigField("canvas_image_timeout_s", "Image Fetch Timeout (s)", "float",
                        requires_restart=_LIVE,
                        help="How long the server waits on a slow image host before giving up "
                             "and letting the window render without its picture.",
                        placeholder="12"),
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
                        help="Extra comma-separated browser origins. The desktop app is always allowed."),
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
