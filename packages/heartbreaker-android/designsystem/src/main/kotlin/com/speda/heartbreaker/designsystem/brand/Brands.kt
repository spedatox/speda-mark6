package com.speda.heartbreaker.designsystem.brand

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  The single source of truth for front-end branding + accent colour — the
 *  Android mirror of profile/brands.ts, profile/warroom.ts and lib/agents.ts.
 *
 *  Mirror of backend Rule 10 / plan principle 4: brands, accents and taglines
 *  live HERE and nowhere else in the app. One accent hex per agent feeds
 *  ThemeEngine, which expands it into the whole palette.
 * ════════════════════════════════════════════════════════════════════════════
 */
data class Brand(
    val agentId: String,
    val name: String,
    val modelNumber: String,
    val userName: String,
    val tagline: String,
    val avatarInitial: String,
    /** The ONE accent colour; theme.ts/ThemeEngine expands it into the palette. */
    val accent: String,
)

object Brands {

    const val DEFAULT_AGENT = "speda"

    /** One entry per agent — verbatim from profile/brands.ts. */
    val BRANDS: Map<String, Brand> = linkedMapOf(
        "speda" to Brand("speda", "SPEDA", "Mark VI", "Ahmet Erol", "Main Assistant", "S", "#36abca"),
        "ultron" to Brand("ultron", "Ultron", "Mark III", "Ahmet Erol", "Academy and Work Operations", "U", "#8a93a6"),
        "centurion" to Brand("centurion", "Centurion", "Mark I", "Ahmet Erol", "Cyber Security & Threat Intelligence", "C", "#d8483c"),
        "sentinel" to Brand("sentinel", "Sentinel", "Mark II", "Ahmet Erol", "Finance & Budget Intelligence", "S", "#d99c44"),
        "atomix" to Brand("atomix", "Atomix", "Mark I", "Ahmet Erol", "Personal Health & Wellness", "A", "#3fae74"),
        "nightcrawler" to Brand("nightcrawler", "NightCrawler", "Mark III", "Ahmet Erol", "OSINT & Web Surveillance", "N", "#9165e6"),
        "optimus" to Brand("optimus", "Optimus", "Mark II", "Ahmet Erol", "Systems, Code & Infrastructure", "O", "#2f4f8f"),
        "orion" to Brand("orion", "Orion", "Mark I", "Ahmet Erol", "Mark VI Maintenance & Memory Custodian", "O", "#e0703a"),
    )

    /**
     * The House Party Protocol brand — deliberately NOT in [BRANDS] so it never
     * appears in the agent switcher. The app swaps into this profile while the
     * protocol is engaged (mirror of profile/warroom.ts). The amber accent is
     * only the resting base; the party cycle owns the palette while engaged.
     */
    val WARROOM = Brand(
        agentId = "warroom",
        name = "HOUSE PARTY",
        modelNumber = "PROTOCOL",
        userName = "Ahmet Erol",
        tagline = "All-Hands Command — Full Roster Engaged",
        avatarInitial = "W",
        accent = "#f2b75c",
    )

    /** The in-process roster, commander first — drives the war-room rail
     *  (lib/agents.ts ROSTER). */
    val ROSTER = listOf("speda", "sentinel", "nightcrawler", "ultron", "centurion", "atomix", "optimus", "orion")

    /** Comms-UI accent per agent (lib/agents.ts AGENT_COLORS). Includes the
     *  broadcast/warroom amber which is not a switchable brand. */
    val AGENT_COLORS: Map<String, String> = mapOf(
        "speda" to "#36abca", "sentinel" to "#d99c44", "nightcrawler" to "#9165e6",
        "ultron" to "#8a93a6", "centurion" to "#d8483c", "atomix" to "#3fae74",
        "optimus" to "#2f4f8f", "orion" to "#e0703a", "all" to "#f2b75c", "warroom" to "#f2b75c",
    )

    /**
     * House Party colour parade — ROSTER order, mirroring the PARTY_COLORS array
     * in theme.ts and the hbPartyCycle keyframe. The palette drifts through
     * these while the protocol is engaged.
     */
    val PARTY_COLORS = listOf(
        "#36abca", // speda
        "#d99c44", // sentinel
        "#9165e6", // nightcrawler
        "#8a93a6", // ultron
        "#d8483c", // centurion
        "#3fae74", // atomix
        "#2f4f8f", // optimus
        "#e0703a", // orion
    )

    fun agentColor(id: String): String = AGENT_COLORS[id] ?: "#5d7f8a"

    fun monogram(id: String): String = id.take(2).uppercase()
}
