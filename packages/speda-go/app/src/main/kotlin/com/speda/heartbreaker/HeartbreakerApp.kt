package com.speda.heartbreaker

import android.app.Application
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/** Owns the process-wide [AppGraph]. Registered as android:name in the manifest. */
class HeartbreakerApp : Application() {

    lateinit var graph: AppGraph
        private set

    override fun onCreate() {
        super.onCreate()
        graph = AppGraph(this)

        // Re-register the installation with Igor on every start. The FID is
        // stable, so this is normally a no-op write — but it is also the only
        // repair path when the owner reinstalls, clears app data, or points the
        // uplink at a rebuilt backend, any of which leaves Igor holding an FID
        // that silently delivers nowhere. Cheap enough to just always do.
        //
        // Fire-and-forget on the application scope: it must never delay startup,
        // and a failure means push is off, not that the app is broken.
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            graph.push.register()
        }
    }
}
