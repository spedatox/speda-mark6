package com.speda.heartbreaker

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.speda.heartbreaker.ui.HeartbreakerRoot

/**
 * The single Activity. The whole app is one chat surface + overlays (plan §4.4),
 * so there is no fragment / nav-graph ceremony — overlays are composables, like
 * the web.
 *
 * FULLSCREEN: the status bar is hidden so the app runs edge-to-edge into the
 * cutout, the way a game does, instead of starting below the camera. Hiding it
 * collapses the statusBars inset to zero, so the shell's statusBarsPadding
 * resolves to nothing — and correctly re-appears if the bar is swiped back in.
 * The nav bar stays: it's the owner's way out of the app.
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        goFullscreen()
        val graph = (application as HeartbreakerApp).graph
        setContent {
            HeartbreakerRoot(graph)
        }
    }

    /** Re-assert on resume: leaving and returning otherwise restores the bar. */
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) goFullscreen()
    }

    private fun goFullscreen() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.statusBars())
            // A swipe brings it back transiently, then it auto-hides again.
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }
}
