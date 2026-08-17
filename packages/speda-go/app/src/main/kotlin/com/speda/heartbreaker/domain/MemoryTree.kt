package com.speda.heartbreaker.domain

/**
 * The memory store as a TREE: loose root files first, then one group per folder.
 *
 * Mirror of `memTree` in the desktop's SystemsBoard.tsx. The ordering is
 * deliberate and NOT alphabetical:
 *
 *   · the flat files at the root are what the owner reads about himself, so
 *     they lead;
 *   · the domains follow in the order the roster owns them, not in the order
 *     the alphabet happens to put them;
 *   · dot-folders (`.audit`, `.archive`) sink to the bottom — they are machine
 *     trails, not knowledge;
 *   · within a folder, alphabetical, because there is no meaningful order among
 *     topics and a stable one is easier to scan than a clever one.
 *
 * Kept out of the composable so it can be tested without a device — the
 * ordering IS the feature, and a rule that only shows up on screen is a rule
 * nothing can check.
 */
object MemoryTree {

    /** Folders in roster order. Anything unlisted sorts between these and the
     *  dot-folders, alphabetically among itself. */
    private val ORDER = listOf(
        "", "dossier", "social/professional", "social/personal",
        "projects", "life", "wellness", "academic", "finance",
        "cybersec", "ops",
    )

    private const val RANK_UNLISTED = 500
    private const val RANK_DOT = 900

    /** The directory part of a store path, "" for a file at the root. */
    fun folderOf(path: String): String =
        path.removePrefix("/memories/").split('/').dropLast(1).joinToString("/")

    private fun rank(dir: String): Int {
        val i = ORDER.indexOf(dir)
        if (i >= 0) return i
        return if (dir.startsWith(".")) RANK_DOT else RANK_UNLISTED
    }

    /**
     * Group [items] by folder and order both levels.
     *
     * Generic in the item so the tests can drive it with plain strings and the
     * screen can pass its DTOs; [pathOf] is how an item names itself.
     */
    fun <T> group(items: List<T>, pathOf: (T) -> String): List<Pair<String, List<T>>> {
        val groups = LinkedHashMap<String, MutableList<T>>()
        for (item in items) {
            groups.getOrPut(folderOf(pathOf(item))) { mutableListOf() }.add(item)
        }
        return groups.entries
            .sortedWith(compareBy({ rank(it.key) }, { it.key }))
            .map { (dir, files) -> dir to files.sortedBy { pathOf(it) } }
    }
}
