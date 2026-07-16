# kotlinx.serialization — keep @Serializable metadata + generated serializers.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class **$$serializer { *; }
-keepclasseswithmembers class * {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.speda.heartbreaker.**$$serializer { *; }
-keepclassmembers @kotlinx.serialization.Serializable class com.speda.heartbreaker.** {
    *** Companion;
    <fields>;
}

# OkHttp / Okio.
-dontwarn okhttp3.**
-dontwarn okio.**
-dontwarn org.conscrypt.**
