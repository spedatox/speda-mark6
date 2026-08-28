package com.speda.heartbreaker.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

/**
 * Wire DTOs for the settings surface — mirrors of the interfaces in lib/api.ts
 * used by SettingsModal.tsx and ConfigTab.tsx.
 */

/** One MCP server row (GET /connections). */
@Serializable
data class ConnectionInfo(
    val server: String,
    val label: String = "",
    val connected: Boolean = false,
    val active: Boolean = false,
    val tools: Int = 0,
    @SerialName("always_on") val alwaysOn: Boolean = false,
    val needs: String? = null,
)

/** GET /connections — server list plus the prompt-prefix token budget. */
@Serializable
data class ConnectionsResult(
    val servers: List<ConnectionInfo> = emptyList(),
    @SerialName("active_tool_tokens") val activeToolTokens: Int = 0,
    @SerialName("itpm_limit") val itpmLimit: Int = 30000,
)

/** GET /automations — one proactive n8n watcher. */
@Serializable
data class AutomationInfo(
    val id: Int,
    @SerialName("agent_id") val agentId: String = "speda",
    val name: String = "",
    val kind: String = "",
    val intent: String = "",
    val active: Boolean = true,
    val summary: String = "",
    @SerialName("last_fired_at") val lastFiredAt: String? = null,
    @SerialName("expires_at") val expiresAt: String? = null,
    /** Null for a raw agent-authored watcher — it has no template at all. */
    val template: String? = null,
    /** Null for a Hook or a raw watcher — neither has a clock. */
    val schedule: AutomationSchedule? = null,
    /** Null for anything that isn't a Hook. */
    val hook: AutomationHook? = null,
    /** The editable content half: what the owner asked for, possibly rewritten. */
    val instruction: String? = null,
    /** His original wording, kept even after a polish so the editor can show it. */
    @SerialName("instruction_raw") val instructionRaw: String? = null,
    @SerialName("intent_status") val intentStatus: String? = null,   // raw | polished | failed
    val options: List<String>? = null,
    @SerialName("every_minutes") val everyMinutes: Int? = null,
    @SerialName("max_asks") val maxAsks: Int? = null,
    @SerialName("day_flags") val dayFlags: List<AutomationDayFlag>? = null,
    val url: String? = null,
    @SerialName("look_for") val lookFor: String? = null,
    val domain: String? = null,
    val recipient: String? = null,
    @SerialName("interval_minutes") val intervalMinutes: Int? = null,
    /** Push automations only — reply spoken in the firing agent's TTS voice and
     *  sent as a Telegram audio message instead of text. Always false for
     *  proactive_ask, which already delivers through the reminders tool. */
    val voice: Boolean = false,
)

/**
 * When an automation fires, as STRUCTURE — never a sentence. The backend
 * deliberately withholds a rendered string here: this pane speaks two
 * languages, and a server-picked "Every day" would have picked one for the
 * owner. `cron` rides along for debugging only, never shown.
 */
@Serializable
data class AutomationSchedule(
    val frequency: String = "daily",   // once | daily | weekly | monthly
    val at: String = "09:00",          // 'HH:MM' in the owner's timezone
    val days: List<Int>? = null,       // weekly — 1=Mon … 7=Sun
    val dom: Int? = null,              // monthly — day of month
    val date: String? = null,          // once — 'YYYY-MM-DD'
    @SerialName("skips_short_months") val skipsShortMonths: Boolean? = null,
    val timezone: String = "",
    val cron: String? = null,
)

/** A weekday class this automation's agent must be TOLD rather than left to
 *  work out — "gym day" on Mon/Wed/Fri. Computed at fire time and appended to
 *  the instruction. */
@Serializable
data class AutomationDayFlag(
    val label: String = "",
    val days: List<Int> = emptyList(),   // 1=Mon … 7=Sun
)

/** A Hook's structured watcher config — url/domain and polling interval,
 *  never a sentence, same reason [AutomationSchedule] is structural. */
@Serializable
data class AutomationHook(
    val type: String = "",     // keyword | address | mail
    val url: String? = null,
    @SerialName("look_for") val lookFor: String? = null,
    val domain: String? = null,
    val recipient: String? = null,
    @SerialName("interval_minutes") val intervalMinutes: Int = 360,
)

/** One agent that can own an automation, for the builder's picker
 *  (GET /automations/agents). */
@Serializable
data class AutomationAgent(
    @SerialName("agent_id") val agentId: String,
    val name: String = "",
    val domain: String = "",
)

/**
 * The builder's payload for POST /automations (create) and PUT /automations/{id}
 * (edit, as a partial). `explicitNulls = false` on the shared [Json] instance
 * means a null field here is OMITTED from the wire body rather than sent as
 * `null` — so the SAME shape serves both a full create and a partial edit
 * without a second type, matching the desktop's `Partial<AutomationDraft>`.
 */
@Serializable
data class AutomationDraft(
    @SerialName("agent_id") val agentId: String,
    val template: String,
    val name: String,
    val instruction: String,
    /** Required for the three schedule templates; absent for the three Hooks. */
    val schedule: AutomationDraftSchedule? = null,
    val options: List<String>? = null,
    @SerialName("every_minutes") val everyMinutes: Int? = null,
    @SerialName("max_asks") val maxAsks: Int? = null,
    @SerialName("day_flags") val dayFlags: List<AutomationDayFlag>? = null,
    /** Hook fields — hook_keyword/hook_address use url(+lookFor); hook_mail
     *  uses domain(+recipient). intervalMinutes applies to all three. */
    val url: String? = null,
    @SerialName("look_for") val lookFor: String? = null,
    val domain: String? = null,
    val recipient: String? = null,
    @SerialName("interval_minutes") val intervalMinutes: Int? = null,
    /** Any push template except proactive_ask. */
    val voice: Boolean? = null,
)

@Serializable
data class AutomationDraftSchedule(
    val frequency: String,
    val at: String,
    val days: List<Int>? = null,
    val dom: Int? = null,
    val date: String? = null,
)

/** Result of a create/update — the created/edited row, or the backend's own
 *  refusal message (names the field and the fix, the only feedback the form
 *  has to give). */
sealed interface AutomationSaveResult {
    data class Ok(val automation: AutomationInfo) : AutomationSaveResult
    data class Error(val message: String) : AutomationSaveResult
}

/** GET /automations/status — the n8n + Telegram pipeline health. */
@Serializable
data class AutomationsStatus(
    @SerialName("n8n_configured") val n8nConfigured: Boolean = false,
    @SerialName("n8n_online") val n8nOnline: Boolean = false,
    @SerialName("n8n_url") val n8nUrl: String = "",
    @SerialName("telegram_configured") val telegramConfigured: Boolean = false,
    @SerialName("telegram_connected") val telegramConnected: Boolean = false,
)

/** One editable backend setting (GET /config groups → fields). */
@Serializable
data class ConfigFieldInfo(
    val key: String,
    val label: String = "",
    val type: String = "text",           // text | password | bool | int | select | url
    val secret: Boolean = false,
    @SerialName("requires_restart") val requiresRestart: Boolean = false,
    val help: String = "",
    val placeholder: String = "",
    val options: List<String> = emptyList(),
    @SerialName("is_set") val isSet: Boolean = false,
    /** Present for non-secret fields; string/number/bool decoded per [type]. */
    val value: JsonElement? = null,
    /** Masked hint for secret fields (e.g. "sk-…abcd"). */
    val hint: String? = null,
)

@Serializable
data class ConfigGroupInfo(
    val id: String,
    val label: String = "",
    val blurb: String = "",
    val fields: List<ConfigFieldInfo> = emptyList(),
)

@Serializable
data class ConfigSaveResult(
    @SerialName("applied_live") val appliedLive: List<String> = emptyList(),
    @SerialName("restart_required") val restartRequired: List<String> = emptyList(),
    val rejected: List<String> = emptyList(),
)

/** Per-agent source-of-truth memory file assignment. */
@Serializable
data class SourceAgentInfo(
    @SerialName("agent_id") val agentId: String,
    val name: String = "",
    val domain: String = "",
    val source: String? = null,
    val default: String? = null,
)

@Serializable
data class MemorySources(
    val files: List<String> = emptyList(),
    val agents: List<SourceAgentInfo> = emptyList(),
)

/* ── Custom MCP servers (GET/POST /connections/mcp) ─────────────────────────
 * A Tier-2 capability the owner wires up without a code change: a server is a
 * command or a URL plus credentials. That is the whole point of MCP as a tier.
 */

/** One owner-registered MCP server.
 *
 *  CREDENTIAL VALUES COME BACK MASKED, and are meant to be sent back that way:
 *  the backend reads an unchanged mask as "keep the stored secret", which is
 *  what lets the owner fix a note or a header without retyping a token they can
 *  no longer see. Overwrite a value only when the owner actually typed a new one.
 *
 *  [command] is a string on some rows and an array on others, so it stays a raw
 *  [JsonElement] here and is flattened for display by [commandLine]. Coercing it
 *  to one shape on read is how a `["npx","-y","pkg"]` becomes the single literal
 *  argument `npx -y pkg` on the next save.
 */
@Serializable
data class CustomMcpServer(
    val name: String,
    val transport: String = "stdio",          // stdio | http
    val command: JsonElement? = null,
    val url: String = "",
    val env: Map<String, String> = emptyMap(),
    val headers: Map<String, String> = emptyMap(),
    val enabled: Boolean = true,
    val note: String = "",
    @SerialName("added_at") val addedAt: String? = null,
    val connected: Boolean? = null,
    val tools: Int? = null,
    val tokens: Int? = null,
)

/** GET /connections/mcp — the owner's servers plus the names the engine owns.
 *  A [reserved] name is one a built-in tier already answers to; registering over
 *  it would shadow a capability rather than add one. */
@Serializable
data class CustomMcpResult(
    val servers: List<CustomMcpServer> = emptyList(),
    val reserved: List<String> = emptyList(),
)

/** POST /connections/mcp — whether the server actually answered on save. */
@Serializable
data class McpSaveResult(
    val connected: Boolean? = null,
    val tools: Int? = null,
    val error: String? = null,
    val message: String? = null,
)

/* ── Web portals (GET/POST /connections/portals) ────────────────────────────
 * A portal is an ACCOUNT, not a scraping target. The browser container keeps
 * the cookies, this side keeps the credentials, and the two never swap jobs.
 */

/** One site the owner has an account on.
 *
 *  [password] is MASKED on read and is meant to be sent back untouched — the
 *  backend reads an unchanged mask as "keep the stored one". That is what lets
 *  a label be corrected without retyping a credential nobody can see any more.
 *
 *  Nothing here may ever reach a model: no tool takes a password as an argument
 *  and no completion, message row or embedding may contain one (CLAUDE.md,
 *  Security). This DTO exists for the settings form and for nothing else. */
@Serializable
data class Portal(
    val name: String,
    val label: String = "",
    @SerialName("login_url") val loginUrl: String = "",
    @SerialName("home_url") val homeUrl: String = "",
    val username: String = "",
    /** Masked on read. Send it back untouched to keep the stored password. */
    val password: String = "",
    val selectors: Map<String, String> = emptyMap(),
    @SerialName("extra_fields") val extraFields: Map<String, String> = emptyMap(),
    @SerialName("success_selector") val successSelector: String = "",
    @SerialName("success_url_contains") val successUrlContains: String = "",
    @SerialName("allowed_agents") val allowedAgents: List<String> = emptyList(),
    val note: String = "",
    val enabled: Boolean = true,
    @SerialName("added_at") val addedAt: String? = null,
    @SerialName("last_login") val lastLogin: String? = null,
    @SerialName("last_status") val lastStatus: String? = null,
    /** Whether the browser container currently holds cookies for this portal. */
    val session: Boolean? = null,
)

/** Whether the Playwright container is up at all. `off` means the deployment
 *  never configured one (BROWSER_URL unset) — a different answer from `down`,
 *  and the panel must not report a missing service as a broken one. */
@Serializable
data class BrowserStatus(
    val status: String = "down",              // ok | down | off
    val reason: String? = null,
    val sessions: Int? = null,
    val profiles: List<String> = emptyList(),
)

/** GET /connections/portals — portals, container health, and which agents may
 *  be granted one. */
@Serializable
data class PortalsResult(
    val portals: List<Portal> = emptyList(),
    val browser: BrowserStatus = BrowserStatus(),
    val agents: List<String> = emptyList(),
)

/** POST /connections/portals and .../login. [ok] is tri-state on save: null
 *  means the portal was stored without a login test being asked for, which is
 *  neither a success nor a failure and must not render as either. */
@Serializable
data class PortalActionResult(
    val ok: Boolean? = null,
    val already: Boolean? = null,
    val message: String? = null,
    @SerialName("landed_on") val landedOn: String? = null,
    val error: String? = null,
)

/* ── Memory record health (GET /admin/memory/status, /memory/folders) ──────── */

/** Progress written by a running rebuild itself, so a long job can say where it
 *  is instead of only that it is alive. */
@Serializable
data class MemoryJobProgress(
    val done: Int = 0,
    val total: Int = 0,
    val stored: Int = 0,
)

@Serializable
data class MemoryJobStatus(
    val id: Int = 0,
    val status: String = "",                  // pending | running | done | failed
    val attempts: Int = 0,
    @SerialName("last_error") val lastError: String? = null,
    val progress: MemoryJobProgress? = null,
)

/** GET /admin/memory/status — where the observation record stands. Cheap (no
 *  model call), so it is safe to read whenever the screen is open.
 *
 *  [atRiskFacts] and [thinCompositions] are the two failures that are invisible
 *  from the documents themselves: a fact nothing cites any more, and a composed
 *  file (owner.md, current.md) thin enough that it stopped saying anything. */
@Serializable
data class MemoryStatus(
    val job: MemoryJobStatus? = null,
    val observations: Int = 0,
    @SerialName("by_origin") val byOrigin: Map<String, Int> = emptyMap(),
    @SerialName("at_risk_facts") val atRiskFacts: Int = 0,
    @SerialName("thin_compositions") val thinCompositions: List<String> = emptyList(),
    val verdict: String = "",
)

/** GET /memory/folders — folders the store DECLARES, including ones holding no
 *  file yet. A folder with no files does not exist in the table, so without this
 *  the knowledge bank cannot show where a thing WILL go before something has
 *  gone there. */
@Serializable
data class MemoryFolderInfo(
    val path: String,
    val summary: String = "",
    @SerialName("owner_agent") val ownerAgent: String? = null,
    val open: Boolean = false,
)

/**
 * [CustomMcpServer.command] as one editable line.
 *
 * An array is joined with spaces for display only. What goes BACK on save is
 * whatever the owner left in the field, sent as a plain string — the backend
 * splits it. Round-tripping the decoded array would be worse: a value the owner
 * never touched would come back re-encoded, and an argument containing a space
 * would silently become two.
 */
fun CustomMcpServer.commandLine(): String = when (val c = command) {
    null -> ""
    is JsonArray -> c.mapNotNull { it.jsonPrimitive.contentOrNull }.joinToString(" ")
    is JsonPrimitive -> c.contentOrNull.orEmpty()
    else -> ""
}
