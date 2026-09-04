# R8 runs in full mode (see gradle.properties).

# kotlinx.serialization generates serializers reflectively looked up by name.
# Without these the release build strips them and every DTO fails to encode at
# runtime — with no compile-time warning, which is the worst way to find out.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class com.spedatox.atomixwear.data.** {
    *** Companion;
}
-keepclasseswithmembers class com.spedatox.atomixwear.data.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.spedatox.atomixwear.data.**$$serializer { *; }

# Health Services binds the passive listener service by class name from the
# manifest, and WorkManager instantiates workers reflectively. R8 cannot see
# either reference, so both would otherwise be stripped or renamed.
-keep class com.spedatox.atomixwear.health.BiometricListenerService { *; }
-keep class * extends androidx.work.ListenableWorker {
    public <init>(android.content.Context, androidx.work.WorkerParameters);
}
