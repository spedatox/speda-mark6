// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.HbSettings
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.i18n.AppLocale
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.launch

@Composable
fun InterfaceTab(config: AppConfig, graph: AppGraph) {
    val scope = rememberCoroutineScope()
    val settings by graph.settings.settings.collectAsStateWithLifecycle(initialValue = HbSettings())
    val t = LocalStrings.current

    // Enabling from Settings requests the permission if it isn't granted yet.
    val locationPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> if (granted) scope.launch { graph.settings.setLocationEnabled(true) } }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        SectionHeader(t.settingsInterface.language, first = true)
        Panel {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                AppLocale.entries.forEach { locale ->
                    ThemeChip(locale.label, active = settings.locale == locale.wire) {
                        scope.launch { graph.settings.setLocale(locale.wire) }
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
            Hint(t.settingsInterface.languageHint)
        }

        SectionHeader(t.settingsInterface.theme)
        Panel {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                ThemeChip(t.settingsInterface.dark, active = true)
                ThemeChip(t.settingsInterface.lightSoon, active = false)
            }
            Spacer(Modifier.height(8.dp))
            Hint(t.settingsInterface.themeHint)
        }

        SectionHeader(t.settingsInterface.locationAwareness)
        Panel {
            ToggleRow(
                label = t.settingsInterface.shareLocation,
                subtitle = t.settingsInterface.shareLocationHint,
                checked = settings.locationEnabled,
                enabled = true,
                onToggle = { on ->
                    if (on) {
                        if (graph.platform.hasLocationPermission()) {
                            scope.launch { graph.settings.setLocationEnabled(true) }
                        } else {
                            locationPermission.launch(android.Manifest.permission.ACCESS_FINE_LOCATION)
                        }
                    } else {
                        scope.launch { graph.settings.setLocationEnabled(false) }
                    }
                },
            )
            Spacer(Modifier.height(8.dp))
            Hint(t.settingsInterface.locationFooter)
        }

        SectionHeader(t.settingsInterface.display)
        Panel {
            Hint(t.settingsInterface.displayHint)
        }

        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun ThemeChip(label: String, active: Boolean, onClick: () -> Unit = {}) {
    val palette = LocalHbPalette.current
    Box(
        Modifier
            .hbGlass(shape = HbGlassShape.Tile, state = if (active) HbGlassState.Tint(palette.accent) else HbGlassState.Default)
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 9.dp),
        contentAlignment = Alignment.Center,
    ) {
        HbText(
            label,
            style = HbType.read.copy(fontSize = 13.5.sp),
            color = if (active) palette.accentBright else palette.textFaint,
        )
    }
}
