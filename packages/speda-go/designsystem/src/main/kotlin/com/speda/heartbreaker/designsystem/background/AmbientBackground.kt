// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.designsystem.background

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import com.speda.heartbreaker.designsystem.theme.BaseTokens
import com.speda.heartbreaker.designsystem.theme.ThemeEngine

/** Static void wash. No frame clock, moving blobs, offscreen layers or blur. */
@Composable
fun AmbientBackground(modifier: Modifier = Modifier) {
    val brush = remember {
        Brush.linearGradient(
            *BaseTokens.BODY_GRADIENT_STOPS
                .map { (stop, hex) -> stop to ThemeEngine.parseHex(hex) }
                .toTypedArray(),
        )
    }
    Box(modifier.fillMaxSize().background(brush))
}
