package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.HbSettings
import com.speda.heartbreaker.designsystem.brand.Brand
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.delay

@Composable
fun AccountTab(
    config: AppConfig,
    graph: AppGraph,
    settings: HbSettings,
    brand: Brand,
    onResetUplink: () -> Unit,
) {
    val palette = LocalHbPalette.current
    var name by remember { mutableStateOf(settings.userName) }
    LaunchedEffect(name) { delay(400); graph.settings.setUserName(name) }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).imePadding().padding(horizontal = 16.dp, vertical = 4.dp),
    ) {
        Spacer(Modifier.height(14.dp))
        // Avatar row
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            Box(
                Modifier.size(52.dp).background(palette.accent.copy(alpha = 0.18f), CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                HbText(
                    (name.firstOrNull() ?: brand.avatarInitial.first()).uppercase(),
                    style = HbType.headerBar.copy(fontSize = 20.sp, fontWeight = FontWeight.Bold),
                    color = palette.accentBright,
                )
            }
            Column {
                HbText(
                    name.ifBlank { brand.userName.ifBlank { brand.name } },
                    style = HbType.read.copy(fontSize = 15.sp, fontWeight = FontWeight.SemiBold),
                    color = palette.text,
                    maxLines = 1,
                )
                HbText(brand.tagline, style = HbType.readout.copy(fontSize = 11.sp), color = palette.textFaint, maxLines = 1)
            }
        }

        SectionHeader("Your name")
        Panel {
            Hint("Used in the greeting on the home screen.")
            Spacer(Modifier.height(8.dp))
            GlassField(name, { name = it }, "Enter your name…", singleLine = true)
        }

        SectionHeader("Uplink")
        Panel {
            FieldLabel("BACKEND")
            HbText(config.apiBase, style = HbType.readout.copy(fontSize = 12.sp), color = palette.textDim, maxLines = 1)
            Spacer(Modifier.height(12.dp))
            Hint("Disconnect from this Igor backend and re-enter the URL + key.")
            Spacer(Modifier.height(8.dp))
            SettingsButton("Reset uplink", onClick = onResetUplink, tint = palette.red)
        }

        Spacer(Modifier.height(24.dp))
    }
}
