// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

if (file("google-services.json").exists()) {
    apply(plugin = libs.plugins.google.services.get().pluginId)
}

// Release plumbing. CI (.github/workflows/speda-go-release.yml) injects the build
// number and the signing material through the environment; a plain local build
// sees none of it and falls back to versionCode 1 and an unsigned release.
// providers.environmentVariable (not System.getenv) keeps the configuration cache
// honest — it re-configures when these change instead of baking in stale values.
val baseVersion = providers.gradleProperty("spedaGoVersion").get()
val buildNumber = providers.environmentVariable("SPEDA_GO_BUILD_NUMBER").orNull?.toIntOrNull()
val keystoreFile = providers.environmentVariable("SPEDA_GO_KEYSTORE_FILE").orNull

// Push is opt-in at build time, exactly as ultron-core does it (docs/ULTRON_WEAR.md
// §3). Drop the Firebase console's google-services.json next to this file and the
// plugin turns on; leave it out and the app still compiles, installs and runs —
// only the FCM wake channel is dead, and the 15-minute HealthDemandWorker poll
// covers for it. A build that HARD-required the file would mean nobody could
// build SPEDA GO without the owner's Firebase project.
//
// NOTE: debug builds carry applicationIdSuffix ".debug", and the plugin fails on
// an applicationId with no matching client in the JSON. Register BOTH
// com.speda.heartbreaker and com.speda.heartbreaker.debug in the Firebase
// console, or debug builds will stop working the moment the file lands.
val pushEnabled = file("google-services.json").exists()

android {
    namespace = "com.speda.heartbreaker"
    compileSdk = libs.versions.compileSdk.get().toInt()

    defaultConfig {
        applicationId = "com.speda.heartbreaker"   // matches the Electron appUserModelId
        minSdk = libs.versions.minSdk.get().toInt()
        targetSdk = libs.versions.targetSdk.get().toInt()
        versionCode = buildNumber ?: 1
        versionName = if (buildNumber != null) "$baseVersion-b$buildNumber" else baseVersion

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables { useSupportLibrary = true }

        // Lets the runtime tell "push is switched off in this build" apart from
        // "Firebase is present but failing", which are very different bugs.
        buildConfigField("boolean", "PUSH_ENABLED", pushEnabled.toString())
    }

    // Personal keystore, single-user app, no Play Store. Present only when the
    // environment supplies it (CI secrets, or a local export before a release
    // build) — otherwise the release APK comes out unsigned rather than failing.
    val releaseSigning = keystoreFile
        ?.takeIf { it.isNotBlank() && file(it).exists() }
        ?.let { path ->
            signingConfigs.create("release") {
                storeFile = file(path)
                storePassword = providers.environmentVariable("SPEDA_GO_KEYSTORE_PASSWORD").orNull
                keyAlias = providers.environmentVariable("SPEDA_GO_KEY_ALIAS").orNull
                keyPassword = providers.environmentVariable("SPEDA_GO_KEY_PASSWORD").orNull
            }
        }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            isDebuggable = true
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            signingConfig = releaseSigning
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    testOptions {
        unitTests {
            isReturnDefaultValues = true
        }
    }
}

dependencies {
    implementation(project(":designsystem"))

    val composeBom = platform(libs.compose.bom)
    implementation(composeBom)

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.datastore.preferences)

    // Atomix health sync: read Samsung Health's data out of Health Connect and
    // trickle it to Igor on a WorkManager cadence (docs/ATOMIX_HEALTH_SYNC.md).
    implementation(libs.androidx.health.connect)
    implementation(libs.androidx.work.runtime)

    // FCM: Igor's only way to wake this app. Atomix refuses to brief on stale
    // biometrics, so without a wake channel a morning briefing can only report
    // that the link is down. Declared unconditionally — the code must compile
    // whether or not google-services.json is present; it degrades at runtime.
    implementation(libs.firebase.messaging)
    implementation(libs.firebase.installations)

    implementation(libs.compose.foundation)
    implementation(libs.compose.ui)
    implementation(libs.compose.material3)
    implementation(libs.haze)

    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.collections.immutable)
    implementation(libs.okhttp)

    // Markdown: commonmark gives us the AST only — the Compose rendering is ours,
    // because the Stark prose (heading plates, _SUB splits, source chips, fence
    // interception) can't be expressed through an off-the-shelf renderer.
    implementation(libs.commonmark)
    implementation(libs.commonmark.ext.gfm.tables)
    implementation(libs.commonmark.ext.gfm.strikethrough)
    implementation(libs.commonmark.ext.autolink)

    // Inline ```svg fences → native vector rendering (diagrams/flowcharts the
    // model emits per prompts/core/06_visual_output). AndroidSVG renders to a
    // Picture we draw on a Compose Canvas — crisp at any size, no WebView.
    implementation(libs.androidsvg)

    // Inline ```map fences → MapLibre GL Native. Vector tiles + a style JSON we
    // own (Stark dark basemap), no Google Play Services dependency.
    implementation(libs.maplibre)

    debugImplementation(libs.compose.ui.tooling)
    implementation(libs.compose.ui.tooling.preview)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.kotlinx.serialization.json)
    androidTestImplementation(platform(libs.compose.bom))
    androidTestImplementation(libs.androidx.test.junit)
    androidTestImplementation(libs.androidx.espresso.core)
}
