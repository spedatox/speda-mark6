// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.spedatox.atomixwear.presentation

import android.Manifest
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.lifecycleScope
import androidx.wear.compose.material3.MaterialTheme
import androidx.wear.compose.material3.Text
import com.spedatox.atomixwear.AtomixWear
import kotlinx.coroutines.launch

/**
 * Phase 1 surface: permission capture, and a plain statement of whether
 * collection is actually running.
 *
 * This is intentionally not the app's real interface. The training client
 * (docs/ATOMIX_WEAR.md §4) and the ported design system arrive in Phase 3; until
 * then the only thing worth putting on screen is whether the pipe works, because
 * that is the one question a half-built pipeline needs to answer honestly. It
 * says "no data supported" or "Igor not configured" rather than showing a
 * reassuring green tick over a dead link.
 */
class MainActivity : ComponentActivity() {

    private val app: AtomixWear get() = application as AtomixWear

    private var status by mutableStateOf(Status())

    data class Status(
        val sensors: Boolean = false,
        val background: Boolean = false,
        val collecting: Int = 0,
        val queued: Int = 0,
        val configured: Boolean = false,
    )

    /**
     * Background sensor access is requested SEPARATELY, and only after
     * BODY_SENSORS has been granted. Android rejects a combined request
     * outright, and the background grant is not cosmetic here: without it,
     * passive delivery stops whenever the app is not in the foreground — which
     * is every moment this app exists to cover.
     */
    private val requestBackground = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { refresh() }

    private val requestSensors = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) requestBackground.launch(Manifest.permission.BODY_SENSORS_BACKGROUND)
        refresh()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme { StatusScreen(status) }
        }
        if (!app.biometrics.hasSensorPermission()) {
            requestSensors.launch(Manifest.permission.BODY_SENSORS)
        }
    }

    override fun onResume() {
        super.onResume()
        // A grant may have been changed in system settings while the app was
        // away. Re-registering is idempotent and cheap, so just re-derive.
        refresh()
    }

    private fun refresh() {
        lifecycleScope.launch {
            val types = app.biometrics.register()
            status = Status(
                sensors = app.biometrics.hasSensorPermission(),
                background = app.biometrics.hasBackgroundSensorPermission(),
                collecting = types.size,
                queued = app.queue.size(),
                configured = app.igor.isConfigured,
            )
        }
    }
}

@Composable
private fun StatusScreen(status: MainActivity.Status) {
    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = "ATOMIX",
            style = MaterialTheme.typography.titleMedium,
            textAlign = TextAlign.Center,
        )
        Text(
            // Ordered by what blocks what: a missing permission makes every
            // later state unknowable, so it is reported first rather than
            // alongside.
            text = when {
                !status.sensors -> "Sensör izni verilmedi"
                !status.background -> "Arka plan sensör izni verilmedi"
                status.collecting == 0 -> "Bu saat desteklenen veri sunmuyor"
                !status.configured -> "Igor adresi yapılandırılmadı"
                else -> "${status.collecting} ölçüm toplanıyor"
            },
            style = MaterialTheme.typography.bodyMedium,
            textAlign = TextAlign.Center,
        )
        if (status.queued > 0) {
            Text(
                text = "${status.queued} kayıt gönderilmeyi bekliyor",
                style = MaterialTheme.typography.bodySmall,
                textAlign = TextAlign.Center,
            )
        }
    }
}
