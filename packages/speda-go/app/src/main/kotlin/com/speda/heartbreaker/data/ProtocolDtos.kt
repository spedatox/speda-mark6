package com.speda.heartbreaker.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.Transient

/**
 * Wire DTOs for the standing operational protocols — mirrors of the interfaces
 * in lib/api.ts that ProtocolsTab.tsx reads.
 *
 * EVERY ONE OF THEM CARRIES [reachable], AND IT IS NOT ON THE WIRE. It is set
 * to true by the IgorApi call that decoded a real response, and stays false on
 * the fallback. Without it each fallback reads as a clean bill of health the
 * server never sent: containment "off", the disk "healthy", the backup "fine".
 * An unreachable backend is not evidence that anything is fine — it is the
 * absence of evidence, and the panel has to be able to say so.
 *
 * The same distinction with three different failures behind it: this app cannot
 * reach Igor ([reachable] false), Igor cannot reach the host (`status`/`detail`),
 * or the protocol is switched off on the deployment (`enabled` false). Collapsing
 * them is how a panel announces a protocol is disabled when in fact nobody ever
 * managed to ask it anything.
 */

/** GET /agents/lockdown — the containment flag plus what the host firewall shows. */
@Serializable
data class LockdownState(
    val engaged: Boolean = false,
    val enabled: Boolean = false,
    /** What the host firewall ACTUALLY shows, keyed by what each rule seals.
     *  Reported apart from [engaged] so a drift between the flag and the real
     *  rules is visible instead of averaged into one green light. */
    val rules: Map<String, Boolean> = emptyMap(),
    val report: String? = null,
    @Transient val reachable: Boolean = false,
)

/** The host figures the Lifeboat Protocol read off the machine. */
@Serializable
data class LifeboatReadings(
    val filesystem: String = "",
    @SerialName("disk_pct") val diskPct: Double? = null,
    @SerialName("disk_free_gb") val diskFreeGb: Double? = null,
    @SerialName("disk_total_gb") val diskTotalGb: Double? = null,
    @SerialName("inode_pct") val inodePct: Double? = null,
    @SerialName("mem_pct") val memPct: Double? = null,
    @SerialName("mem_available_gb") val memAvailableGb: Double? = null,
    @SerialName("mem_total_gb") val memTotalGb: Double? = null,
    @SerialName("swap_pct") val swapPct: Double? = null,
    @SerialName("docker_reclaimable_gb") val dockerReclaimableGb: Double? = null,
)

/** GET /host/lifeboat — disk/inode/memory pressure on the deployment host. */
@Serializable
data class LifeboatState(
    /** ok | error | disabled */
    val status: String = "error",
    /** healthy | watch | critical — the WORST of disk, inodes and memory. */
    val level: String = "healthy",
    @SerialName("by_resource") val byResource: Map<String, String> = emptyMap(),
    val pressed: List<String> = emptyList(),
    val readings: LifeboatReadings = LifeboatReadings(),
    val summary: String = "",
    val recommendation: String = "",
    @SerialName("target_free_gb") val targetFreeGb: Double = 0.0,
    val detail: String = "",
    @Transient val reachable: Boolean = false,
)

/** One console the owner still has to edit by hand after a domain move. */
@Serializable
data class DoormatChecklistItem(
    val provider: String = "",
    val where: String = "",
    val field: String = "",
    val value: String = "",
    val note: String = "",
)

/** GET /host/doormat — where the domain move stands, and what is left by hand. */
@Serializable
data class DoormatState(
    val enabled: Boolean = false,
    /** "" (idle) | "staged" | "cutover" */
    val phase: String = "",
    val target: String = "",
    val previous: String = "",
    @SerialName("staged_at") val stagedAt: String = "",
    @SerialName("cutover_at") val cutoverAt: String = "",
    @SerialName("current_domain") val currentDomain: String = "",
    /** null while idle; otherwise whether the new domain actually answers. */
    @SerialName("target_serving") val targetServing: Boolean? = null,
    /** Cutover written but Igor not restarted, so it still runs on the old door. */
    @SerialName("restart_pending") val restartPending: Boolean = false,
    val checklist: List<DoormatChecklistItem> = emptyList(),
    val detail: String = "",
    @Transient val reachable: Boolean = false,
)

/** One snapshot in the owner's Drive (GET /admin/octavius). */
@Serializable
data class BackupEntry(
    val id: String = "",
    val name: String = "",
    val bytes: Long = 0,
    val mb: Double = 0.0,
    val created: String = "",
    val sha256: String = "",
)

/** GET /admin/octavius — read from DRIVE, never from a local note that a backup
 *  was made. Such a note survives exactly the failures it exists to catch. */
@Serializable
data class OctaviusState(
    val enabled: Boolean = false,
    val count: Int = 0,
    val latest: BackupEntry? = null,
    @SerialName("age_hours") val ageHours: Double? = null,
    /** Newest copy too old, none at all, or Drive unreachable. All three mean
     *  the same thing to whoever is reading: no protection to count on. */
    val stale: Boolean = true,
    val detail: String = "",
    @Transient val reachable: Boolean = false,
)

/** POST /admin/octavius/backup — the one action worth its own button.
 *  [stage] is where a failed run stopped: an "integrity" failure is a statement
 *  about the LIVE database, not about the backup, and must not read as
 *  "try again later". */
@Serializable
data class OctaviusBackupResult(
    val ok: Boolean = false,
    val name: String? = null,
    val stage: String? = null,
    val error: String? = null,
)

/** POST /agents/lockdown {engaged:false} — standing containment down. */
@Serializable
data class LockdownActionResult(
    val ok: Boolean = false,
    val report: String? = null,
    val error: String? = null,
)


// ── Skyfall ─────────────────────────────────────────────────────────────────

/**
 * One launch target the owner configured (GET /protocols/skyfall/projects).
 *
 * [headers] carries the header NAMES mapped to a mask — the values stay on the
 * server and reach neither a client nor a model. Sending a masked value back on
 * save means "leave this one alone", so editing a description does not blank an
 * API token the owner never retyped.
 */
@Serializable
data class SkyfallProject(
    val id: String = "",
    val name: String = "",
    val description: String = "",
    val url: String = "",
    val method: String = "POST",
    val body: String = "",
    val headers: Map<String, String> = emptyMap(),
    @SerialName("has_body") val hasBody: Boolean = false,
    @SerialName("countdown_seconds") val countdownSeconds: Int = 10,
    @SerialName("last_fired_at") val lastFiredAt: String = "",
    @SerialName("last_result") val lastResult: String = "",
)

/**
 * What the countdown screen needs, and nothing else — no body, no headers. The
 * request is assembled server-side at zero, so a client that never holds the
 * secret cannot leak it, and one that cannot alter the payload cannot turn an
 * armed countdown into a different request than the one that was armed.
 */
@Serializable
data class SkyfallArm(
    @SerialName("project_id") val projectId: String = "",
    val name: String = "",
    val description: String = "",
    val method: String = "POST",
    val url: String = "",
    @SerialName("countdown_seconds") val countdownSeconds: Int = 10,
    @SerialName("armed_at") val armedAt: String = "",
)

/**
 * What happened at zero. [fired] and [ok] are separate on purpose: a request
 * that went out and came back 500 is not the same event as one that never left,
 * and the screen must not render them alike.
 */
@Serializable
data class SkyfallResult(
    val fired: Boolean = false,
    val ok: Boolean = false,
    val status: Int = 0,
    val body: String = "",
    val truncated: Boolean = false,
    val error: String = "",
)
