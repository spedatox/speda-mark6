// Top-level build file. Configuration common to all sub-projects lives here.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
    // Requested here (unapplied) so its version/classpath resolves.
    // app/build.gradle.kts applies it imperatively, only when
    // google-services.json is present — see the note there.
    alias(libs.plugins.google.services) apply false
}
