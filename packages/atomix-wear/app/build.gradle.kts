import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

/**
 * Firebase is applied ONLY when `app/google-services.json` is present.
 *
 * The google-services plugin hard-fails the build when that file is missing, so
 * applying it unconditionally would mean nobody can compile this module until a
 * Firebase project exists. The messaging SDK stays on the classpath either way —
 * it is the plugin, not the library, that needs the config — so the code
 * compiles identically and simply finds no FirebaseApp at runtime.
 *
 * FCM is not optional decoration here. It is the whole reason a watch client can
 * answer a `live=true` health query at all (docs/ATOMIX_WEAR.md §1.2): Speda GO
 * carries no Firebase, which is why /health/sync-demand has to leave a note and
 * hope. Without the JSON this app degrades to the same note-and-hope behaviour.
 */
val hasFirebaseConfig = file("google-services.json").exists()
if (hasFirebaseConfig) {
    apply(plugin = libs.plugins.google.services.get().pluginId)
}

/** Igor credentials come from local.properties, never from source control. */
val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
fun localProp(key: String): String = localProps.getProperty(key).orEmpty()

/**
 * A build secret, resolved environment-first (CI) then local.properties (a
 * developer machine), and `null` when genuinely absent. Deliberately no default:
 * a signing credential that silently falls back is one you discover is wrong
 * when the APK will not install on the watch.
 */
fun secret(key: String): String? =
    System.getenv(key)?.takeIf { it.isNotBlank() }
        ?: localProps.getProperty(key)?.takeIf { it.isNotBlank() }

val releaseStoreFile = secret("RELEASE_KEYSTORE_PATH")
val releaseStorePassword = secret("RELEASE_KEYSTORE_PASSWORD")
val releaseKeyAlias = secret("RELEASE_KEY_ALIAS")
val releaseKeyPassword = secret("RELEASE_KEY_PASSWORD")
val hasReleaseSigning = releaseStoreFile != null && releaseStorePassword != null &&
    releaseKeyAlias != null && releaseKeyPassword != null

/** Local profiling escape hatch: `-PdebugSignRelease=true` signs release with
 *  the debug key so `installRelease` works without the real keystore. Never in
 *  CI — a debug-signed release cannot be updated by a properly signed one. */
val debugSignRelease = (findProperty("debugSignRelease") as String?).toBoolean()

if (gradle.startParameter.taskNames.any { it.contains("Release", ignoreCase = true) } &&
    !hasReleaseSigning && !debugSignRelease
) {
    logger.warn(
        "\n⚠  No release signing credentials found (RELEASE_KEYSTORE_PATH, " +
            "RELEASE_KEYSTORE_PASSWORD, RELEASE_KEY_ALIAS, RELEASE_KEY_PASSWORD).\n" +
            "   The release build will be UNSIGNED and will not install on a watch.\n" +
            "   To profile locally: ./gradlew installRelease -PdebugSignRelease=true\n"
    )
}

android {
    namespace = "com.spedatox.atomixwear"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.spedatox.atomixwear"
        // Wear OS 4 (API 33) is the floor for the Galaxy Watch 6 line. It is
        // also the floor for Health Connect on the watch, which §3.1 uses as
        // the secondary source for anything Health Services does not derive.
        minSdk = 33
        targetSdk = 36
        versionCode = (findProperty("atomixVersionCode") as String?)?.toIntOrNull() ?: 1
        versionName = (findProperty("atomixVersionName") as String?) ?: "0.1-phase1"

        buildConfigField("String", "IGOR_BASE_URL", "\"${localProp("IGOR_BASE_URL")}\"")
        buildConfigField("String", "IGOR_API_KEY", "\"${localProp("IGOR_API_KEY")}\"")
        buildConfigField("boolean", "HAS_FIREBASE", "$hasFirebaseConfig")
    }

    signingConfigs {
        // Created only when every credential is present — declaring it
        // unconditionally makes `file(null)` explode at configuration time,
        // which breaks `./gradlew tasks` on a fresh clone.
        if (hasReleaseSigning) {
            create("release") {
                storeFile = file(releaseStoreFile!!)
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
                enableV1Signing = true
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            signingConfig = when {
                hasReleaseSigning -> signingConfigs.getByName("release")
                debugSignRelease -> signingConfigs.getByName("debug")
                else -> null
            }
        }
        debug {
            // Compose in a debuggable build is 2–5× slower than release;
            // never judge watch smoothness from a debug APK.
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += setOf(
                "/META-INF/{AL2.0,LGPL2.1}",
                "DebugProbesKt.bin",
                "kotlin-tooling-metadata.json",
            )
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        freeCompilerArgs.addAll(
            "-opt-in=kotlin.RequiresOptIn",
            "-opt-in=androidx.wear.compose.foundation.ExperimentalWearFoundationApi",
        )
    }
}

dependencies {
    constraints {
        // This app contains no Fragments, but Play Services transitively
        // declares androidx.fragment 1.1.0 (2019), which trips lint's
        // InvalidFragmentVersionForActivityResult and fails lintVitalRelease.
        // A constraint raises the transitive version without adding Fragment to
        // the graph on its own.
        implementation(libs.fragment)
    }

    // ── Compose ──────────────────────────────────────────────────────────────
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.graphics)
    implementation(libs.compose.foundation)

    // ── Wear Compose (Material 3) ────────────────────────────────────────────
    implementation(libs.wear.compose.material3)
    implementation(libs.wear.compose.foundation)
    implementation(libs.wear.compose.navigation)

    // ── Wear platform surfaces ───────────────────────────────────────────────
    // `androidx.wear:wear` is deliberately absent: it exists for View-system
    // widgets a Compose app never touches and transitively pins an ancient
    // androidx.fragment. See Ultron Wear's build file for the full history.
    implementation(libs.tiles)
    implementation(libs.tiles.material)
    implementation(libs.watchface.complications.data.source.ktx)
    implementation(libs.guava)

    // ── Health ───────────────────────────────────────────────────────────────
    implementation(libs.health.services.client)
    implementation(libs.health.connect.client)

    // ── AndroidX ─────────────────────────────────────────────────────────────
    implementation(libs.activity.compose)
    implementation(libs.core.ktx)
    implementation(libs.core.splashscreen)
    implementation(libs.lifecycle.runtime.ktx)
    implementation(libs.lifecycle.runtime.compose)
    implementation(libs.lifecycle.viewmodel.compose)
    implementation(libs.work.runtime.ktx)
    implementation(libs.profileinstaller)

    // ── Firebase ─────────────────────────────────────────────────────────────
    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.messaging)
    implementation(libs.firebase.installations)
    implementation(libs.coroutines.play.services)

    // ── Kotlin ───────────────────────────────────────────────────────────────
    implementation(libs.coroutines.android)
    implementation(libs.coroutines.guava)
    implementation(libs.kotlinx.serialization.json)

    // ── Tooling (debug only) ─────────────────────────────────────────────────
    debugImplementation(libs.compose.ui.tooling)
    implementation(libs.compose.ui.tooling.preview)
}
