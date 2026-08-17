package com.speda.heartbreaker.domain

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The ordering is the feature — the tree exists so the owner can tell which
 * domain he is looking at, and that only works if the groups come out in the
 * roster's order rather than the alphabet's.
 */
class MemoryTreeTest {

    private fun dirs(paths: List<String>) = MemoryTree.group(paths) { it }.map { it.first }

    @Test
    fun `root files lead, before any folder`() {
        val out = dirs(listOf("/memories/finance/ledger.md", "/memories/owner.md"))
        assertEquals(listOf("", "finance"), out)
    }

    @Test
    fun `domains come out in roster order, not alphabetical`() {
        val out = dirs(
            listOf(
                "/memories/ops/hosts.md",
                "/memories/academic/courses.md",
                "/memories/dossier/profile.md",
                "/memories/finance/ledger.md",
            ),
        )
        assertEquals(listOf("dossier", "academic", "finance", "ops"), out)
    }

    @Test
    fun `dot-folders sink to the bottom — they are machine trails`() {
        val out = dirs(
            listOf(
                "/memories/.audit/trail.md",
                "/memories/ops/hosts.md",
                "/memories/owner.md",
            ),
        )
        assertEquals(listOf("", "ops", ".audit"), out)
    }

    @Test
    fun `an unlisted folder sits between the roster and the dot-folders`() {
        val out = dirs(
            listOf(
                "/memories/.archive/old.md",
                "/memories/zzz-scratch/notes.md",
                "/memories/ops/hosts.md",
            ),
        )
        assertEquals(listOf("ops", "zzz-scratch", ".archive"), out)
    }

    @Test
    fun `files inside a folder are alphabetical`() {
        val files = MemoryTree.group(
            listOf(
                "/memories/finance/ledger.md",
                "/memories/finance/budget.md",
                "/memories/finance/tax.md",
            ),
        ) { it }
        assertEquals(
            listOf("/memories/finance/budget.md", "/memories/finance/ledger.md", "/memories/finance/tax.md"),
            files.single().second,
        )
    }

    @Test
    fun `nested domains keep their full folder name`() {
        val out = dirs(
            listOf(
                "/memories/social/personal/friends.md",
                "/memories/social/professional/colleagues.md",
            ),
        )
        assertEquals(listOf("social/professional", "social/personal"), out)
    }

    @Test
    fun `folderOf reads the directory, and is empty at the root`() {
        assertEquals("", MemoryTree.folderOf("/memories/owner.md"))
        assertEquals("finance", MemoryTree.folderOf("/memories/finance/ledger.md"))
        assertEquals("social/personal", MemoryTree.folderOf("/memories/social/personal/friends.md"))
    }
}
