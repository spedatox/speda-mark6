package com.speda.heartbreaker.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.i18n.LocalStrings

/**
 * First-run uplink setup — the user points the app at their own Igor backend and
 * enters their own API key (replaces the Electron env config). The key is handed
 * straight to [com.speda.heartbreaker.data.UplinkStore] which Keystore-wraps it.
 */
@Composable
fun UplinkSetupScreen(onConnect: (apiBase: String, apiKey: String) -> Unit) {
    val palette = LocalHbPalette.current
    val t = LocalStrings.current
    var apiBase by remember { mutableStateOf("") }
    var apiKey by remember { mutableStateOf("") }
    val canConnect = apiBase.isNotBlank() && apiKey.isNotBlank()

    Column(
        modifier = Modifier.fillMaxSize().padding(28.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        HbText("HEARTBREAKER", style = HbType.headerBar, color = palette.accentBright, caps = true)
        Spacer(Modifier.height(6.dp))
        HbText(t.uplink.establishUplink, style = HbType.label, color = palette.textDim, caps = true)
        Spacer(Modifier.height(28.dp))

        UplinkField(
            label = t.uplink.apiBaseLabel,
            value = apiBase,
            onValueChange = { apiBase = it },
            placeholder = "https://host:port",
            keyboardType = KeyboardType.Uri,
        )
        Spacer(Modifier.height(14.dp))
        UplinkField(
            label = t.uplink.apiKeyLabel,
            value = apiKey,
            onValueChange = { apiKey = it },
            placeholder = "X-API-Key",
            keyboardType = KeyboardType.Password,
            visualTransformation = PasswordVisualTransformation(),
        )
        Spacer(Modifier.height(28.dp))

        HbGlassButton(
            label = t.uplink.connect,
            onClick = { if (canConnect) onConnect(apiBase, apiKey) },
            state = if (canConnect) HbGlassState.Active else HbGlassState.Default,
            shape = HbGlassShape.Pill,
            contentColor = if (canConnect) palette.accentBright else palette.textFaint,
            modifier = Modifier.widthIn(min = 160.dp),
        )
    }
}

@Composable
private fun UplinkField(
    label: String,
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    keyboardType: KeyboardType,
    visualTransformation: VisualTransformation = VisualTransformation.None,
) {
    val palette = LocalHbPalette.current
    Column(Modifier.fillMaxWidth().widthIn(max = 420.dp)) {
        HbText(label, style = HbType.label, color = palette.textDim, caps = true)
        Spacer(Modifier.height(6.dp))
        BasicTextField(
            value = value,
            onValueChange = onValueChange,
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
            visualTransformation = visualTransformation,
            textStyle = HbType.read.merge(androidx.compose.ui.text.TextStyle(color = palette.text)),
            cursorBrush = androidx.compose.ui.graphics.SolidColor(palette.accentBright),
            modifier = Modifier.fillMaxWidth(),
            decorationBox = { inner ->
                androidx.compose.foundation.layout.Box(
                    Modifier
                        .fillMaxWidth()
                        .hbGlass(shape = HbGlassShape.Tile)
                        .padding(horizontal = 12.dp, vertical = 12.dp),
                ) {
                    if (value.isEmpty()) {
                        HbText(placeholder, style = HbType.read, color = palette.textFaint)
                    }
                    inner()
                }
            },
        )
    }
}
