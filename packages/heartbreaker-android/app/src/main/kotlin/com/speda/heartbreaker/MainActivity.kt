package com.speda.heartbreaker

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.speda.heartbreaker.ui.HeartbreakerRoot

/**
 * The single Activity. The whole app is one chat surface + overlays (plan §4.4),
 * so there is no fragment / nav-graph ceremony — overlays are composables, like
 * the web.
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        val graph = (application as HeartbreakerApp).graph
        setContent {
            HeartbreakerRoot(graph)
        }
    }
}
