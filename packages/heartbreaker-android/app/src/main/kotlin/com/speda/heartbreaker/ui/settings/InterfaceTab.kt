package com.speda.heartbreaker.ui.settings

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
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.launch

@Composable
fun InterfaceTab(config: AppConfig, graph: AppGraph) {
    val scope = rememberCoroutineScope()
    val settings by graph.settings.settings.collectAsStateWithLifecycle(initialValue = HbSettings())

    // Enabling from Settings requests the permission if it isn't granted yet.
    val locationPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> if (granted) scope.launch { graph.settings.setLocationEnabled(true) } }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        SectionHeader("Theme")
        Panel {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                ThemeChip("Dark", active = true)
                ThemeChip("Light (soon)", active = false)
            }
            Spacer(Modifier.height(8.dp))
            Hint("The command deck is AMOLED-black by design; the whole palette re-hues to the active agent's accent.")
        }

        SectionHeader("Location awareness")
        Panel {
            ToggleRow(
                label = "Share location with SPEDA",
                subtitle = "Sends your device's precise position each turn, so SPEDA can answer location-aware questions.",
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
            Hint(
                "Your device model, OS and app version always travel with a turn so SPEDA knows it's on the phone. " +
                    "Location is opt-in and never stored — it rides only the message you send.",
            )
        }

        SectionHeader("Display")
        Panel {
            Hint("Switch agents from the drawer to recolour the entire interface. The House Party Protocol parades the full roster's colours.")
        }

        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun ThemeChip(label: String, active: Boolean) {
    val palette = LocalHbPalette.current
    Box(
        Modifier
            .hbGlass(shape = HbGlassShape.R9, state = if (active) HbGlassState.Tint(palette.accent) else HbGlassState.Default)
            .padding(horizontal = 16.dp, vertical = 9.dp),
        contentAlignment = Alignment.Center,
    ) {
        HbText(
            label,
            style = HbType.headerBar.copy(fontSize = 12.sp),
            color = if (active) palette.accentBright else palette.textFaint,
            caps = true,
        )
    }
}
