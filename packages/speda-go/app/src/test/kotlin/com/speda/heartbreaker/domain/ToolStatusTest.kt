// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.domain

import com.speda.heartbreaker.i18n.En
import com.speda.heartbreaker.i18n.Tr
import org.junit.Assert.assertEquals
import org.junit.Test

class ToolStatusTest {

    @Test
    fun formatToolName_strips_mcp_framing_and_adjacent_duplicates() {
        assertEquals("google tasks list", ToolStatus.formatToolName("mcp_google_tasks_tasks_list_1e78403006"))
        assertEquals("Google tasks list", ToolStatus.displayName("mcp_google_tasks_tasks_list_1e78403006"))
        assertEquals("google gmail search", ToolStatus.formatToolName("mcp_google_gmail_gmail_search_abcdef1234"))
        assertEquals("microsoft outlook search", ToolStatus.formatToolName("mcp_microsoft_outlook_outlook_search_0123456789"))
        assertEquals("tavily search", ToolStatus.formatToolName("mcp_tavily_tavily-search_deadbeef01"))
        assertEquals("github list issues", ToolStatus.formatToolName("mcp__github__list_issues"))
        assertEquals("memory lookup", ToolStatus.formatToolName("memory_lookup"))
        assertEquals("my tool deadbeef01", ToolStatus.formatToolName("my_tool_deadbeef01"))
    }

    @Test
    fun statusLabel_uses_formatted_name_for_mcp_tools() {
        assertEquals(
            "Using google tasks list",
            ToolStatus.statusLabel("mcp_google_tasks_tasks_list_1e78403006", En),
        )
        assertEquals(
            "google tasks list kullanılıyor",
            ToolStatus.statusLabel("mcp_google_tasks_tasks_list_1e78403006", Tr),
        )
    }
}
