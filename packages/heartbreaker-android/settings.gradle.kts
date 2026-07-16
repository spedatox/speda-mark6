/*
 * Heartbreaker Droid — the native Android port of the Heartbreaker desktop client.
 *
 * This is a SELF-CONTAINED Gradle build that lives inside the speda-mark6 monorepo
 * but is inert to the GitOps prod deploy: the server never runs Gradle, so nothing
 * here is built or shipped by the backend. See docs/ANDROID_PORT_PLAN.md.
 */
pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "heartbreaker-android"

include(":app")
include(":designsystem")
