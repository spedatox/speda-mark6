package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.domain.AppConfig

/**
 * Configuration tab — the full backend config editor (every API key, token and
 * feature flag, grouped, dirty-delta save) + the per-agent source-of-truth memory
 * assignment. The transport is already wired (IgorApi.getConfig/saveConfig/
 * getMemorySources/setMemorySource); the grouped editor UI is the next pass.
 */
@Composable
fun ConfigTabView(config: AppConfig, graph: AppGraph) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        SectionHeader("Configuration")
        Panel {
            Hint("The full backend configuration surface — API keys, bot tokens, endpoints and feature flags — lands here next. The transport is already in place.")
        }
        Spacer(Modifier.height(24.dp))
    }
}
