package com.speda.heartbreaker

import android.app.Application

/** Owns the process-wide [AppGraph]. Registered as android:name in the manifest. */
class HeartbreakerApp : Application() {

    lateinit var graph: AppGraph
        private set

    override fun onCreate() {
        super.onCreate()
        graph = AppGraph(this)
    }
}
