package com.speda.heartbreaker.domain

import kotlinx.serialization.json.*

/** Typed policy form operations shared by the native controls and their tests. */
object PolicyFields {
    fun pickerOptions(schema: JsonObject, name: String = "x-options"): List<JsonObject>? =
        (schema[name] as? JsonArray)?.map { it.jsonObject }
    fun thinkingOptions(path: String, root: JsonObject, document: JsonObject): List<String>? {
        val match = Regex("^models\\.(models|agents|workers|roles)\\.(.+)$").matchEntire(path) ?: return null
        val catalog = root["x-thinking-options"] as? JsonObject ?: return null
        val (group, key) = match.destructured
        fun text(value: JsonElement?) = (value as? JsonPrimitive)?.takeIf { it != JsonNull }?.content.orEmpty()
        fun map(name: String) = (document[name] as? JsonObject).orEmpty().mapValues { text(it.value) }
        val defaults = root["x-route-defaults"] as? JsonObject
        val main = text(document["main_model"]).ifEmpty { text(defaults?.get("main")) }
        val agentRoutes = map("agent_models").values + map("telegram_models").values
        val refs = when (group) {
            "agents" -> listOf(map("agent_models")[key] ?: main, map("telegram_models")[key]).filterNotNull()
            "workers" -> map("worker_models")[key]?.let { listOf(it) } ?: (listOf(main) + agentRoutes)
            "roles" -> if (key == "background") listOf(text(document["background_model"]).ifEmpty { text(defaults?.get("background")).ifEmpty { main } })
                else agentRoutes.ifEmpty { listOf(main) } + (if (key == "worker") map("worker_models").values else emptyList())
            else -> listOf(key)
        }
        val lists = refs.map { ref -> (catalog[ref] as? JsonArray)?.map { it.jsonPrimitive.content } ?: listOf("default") }
        val result = lists.firstOrNull()?.filter { option -> lists.all { option in it } } ?: listOf("default")
        return if (group == "workers" || (group == "roles" && key == "worker")) result + "inherit" else result
    }
    fun thinkingValue(option: String) = buildJsonObject {
        put("mode", when {
            option == "default" -> "provider_default"
            option.startsWith("budget:") -> "budget"
            option in listOf("off", "enabled", "inherit") -> option
            else -> "effort"
        })
        put("value", when {
            option.startsWith("budget:") -> JsonPrimitive(option.substringAfter(':').toInt())
            option in listOf("default", "off", "enabled", "inherit") -> JsonNull
            else -> JsonPrimitive(option)
        })
    }
    fun thinkingSelection(value: JsonElement?): String {
        val request = value as? JsonObject ?: return "default"
        return when (val mode = (request["mode"] as? JsonPrimitive)?.content) {
            "budget" -> "budget:${request["value"]?.jsonPrimitive?.content}"
            "effort" -> request["value"]?.jsonPrimitive?.content.orEmpty()
            "provider_default", null -> "default"
            else -> mode
        }
    }
    fun resolve(schema: JsonObject, root: JsonObject): JsonObject {
        val ref = (schema["\$ref"] as? JsonPrimitive)?.content ?: return schema
        val target = (root["\$defs"] as? JsonObject)?.get(ref.substringAfterLast('/')) as? JsonObject ?: JsonObject(emptyMap())
        return JsonObject(target + schema.filterKeys { it != "\$ref" })
    }
    fun choices(schema: JsonObject, root: JsonObject): List<JsonObject> {
        val s = resolve(schema, root)
        return ((s["anyOf"] as? JsonArray)?.map { resolve(it.jsonObject, root) } ?: listOf(s)).filter { type(it) != "null" }
    }
    fun type(schema: JsonObject) = (schema["type"] as? JsonPrimitive)?.content.orEmpty()
    fun selected(schema: JsonObject, root: JsonObject, value: JsonElement?): JsonObject {
        val options = choices(schema, root)
        val kind = when (value) {
            is JsonObject -> "object"
            is JsonArray -> "array"
            is JsonPrimitive -> when {
                value.isString -> "string"
                value.booleanOrNull != null -> "boolean"
                value.doubleOrNull != null -> "number"
                else -> "null"
            }
            else -> "null"
        }
        return options.firstOrNull { type(it) == kind || (type(it) == "integer" && kind == "number") } ?: options.first()
    }
    fun default(schema: JsonObject, root: JsonObject): JsonElement {
        val s = choices(schema, root).first()
        s["default"]?.takeIf { it != JsonNull }?.let { return it }
        (s["enum"] as? JsonArray)?.firstOrNull()?.let { return it }
        pickerOptions(s)?.firstOrNull()?.get("value")?.let { return it }
        return when (type(s)) {
            "object" -> JsonObject((s["properties"] as? JsonObject).orEmpty().mapValues {
                if (resolve(it.value.jsonObject, root)["default"] == JsonNull) JsonNull else default(it.value.jsonObject, root)
            })
            "array" -> JsonArray(emptyList())
            "boolean" -> JsonPrimitive(false)
            "integer", "number" -> JsonPrimitive((s["minimum"] as? JsonPrimitive)?.doubleOrNull
                ?: ((s["exclusiveMinimum"] as? JsonPrimitive)?.doubleOrNull?.plus(if (type(s) == "integer") 1.0 else 0.001) ?: 0.0))
            else -> JsonPrimitive("")
        }
    }
    fun number(text: String, integer: Boolean): JsonPrimitive {
        val number = text.toDoubleOrNull()?.takeIf { it.isFinite() } ?: return JsonPrimitive(text)
        return if (integer && number % 1.0 == 0.0 && kotlin.math.abs(number) <= 9007199254740991.0) JsonPrimitive(number.toLong()) else JsonPrimitive(number)
    }
    fun validate(schema: JsonObject, root: JsonObject, value: JsonElement?, path: String = ""): List<String> {
        val s = resolve(schema, root)
        val variants = s["anyOf"] as? JsonArray
        if (value == JsonNull && variants?.any { type(it.jsonObject) == "null" } == true) return emptyList()
        fun fail(reason: String) = listOf("$path: $reason")
        if (type(s) == "null") return if (value == JsonNull) emptyList() else fail("Choose a valid value.")
        if (variants != null) return if (variants.any { validate(it.jsonObject, root, value, path).isEmpty() }) emptyList() else fail("Choose a valid value.")
        if ((s["enum"] as? JsonArray)?.let { value !in it } == true) return fail("Choose an available option.")
        fun limit(key: String, fallback: Double) = (s[key] as? JsonPrimitive)?.doubleOrNull ?: fallback
        when (type(s)) {
            "object" -> {
                if (value !is JsonObject) return fail("Complete this group.")
                val properties = s["properties"] as? JsonObject
                val missing = (s["required"] as? JsonArray).orEmpty().filter { it.jsonPrimitive.content !in value }.map { "$path: Missing ${it.jsonPrimitive.content}." }
                return missing + value.flatMap { (key, child) ->
                    val childSchema = (properties?.get(key) ?: s["additionalProperties"]) as? JsonObject
                    if (childSchema == null) emptyList() else validate(childSchema, root, child, "$path / ${label(key, false)}")
                }
            }
            "array" -> {
                if (value !is JsonArray) return fail("Enter a list.")
                if (value.size < limit("minItems", 0.0) || value.size > limit("maxItems", Double.POSITIVE_INFINITY)) return fail("Check the number of entries.")
                return value.flatMapIndexed { index, child -> validate(s["items"] as? JsonObject ?: JsonObject(emptyMap()), root, child, "$path / ${index + 1}") }
            }
            "number", "integer" -> {
                val p = value as? JsonPrimitive
                val n = p?.takeIf { !it.isString }?.doubleOrNull
                if (n == null || !n.isFinite() || (type(s) == "integer" && (n % 1.0 != 0.0 || kotlin.math.abs(n) > 9007199254740991.0))) return fail("Enter a valid number.")
                if (n < limit("minimum", Double.NEGATIVE_INFINITY) || n > limit("maximum", Double.POSITIVE_INFINITY)
                    || n <= limit("exclusiveMinimum", Double.NEGATIVE_INFINITY) || n >= limit("exclusiveMaximum", Double.POSITIVE_INFINITY)) return fail("Value is outside the allowed range.")
            }
            "string" -> {
                val p = value as? JsonPrimitive
                if (p == null || !p.isString || p.content.length < limit("minLength", 0.0) || p.content.length > limit("maxLength", Double.POSITIVE_INFINITY)) return fail("Enter valid text.")
            }
            "boolean" -> if ((value as? JsonPrimitive)?.takeIf { !it.isString }?.booleanOrNull == null) return fail("Choose on or off.")
        }
        return emptyList()
    }
    fun hint(path: String, schema: JsonObject, tr: Boolean): String = when {
        path == "coordination.run.tokens" -> if (tr) "Bir çalıştırmadaki tüm çağrıların toplam ayrılmış token sınırı. İstek, azami yanıt ve düşünme payını içerir. Harcama sınırları kapalıyken de uygulanır." else "Total token reservations across all calls in one run, including request size, maximum response and reasoning allowance. Still applies when spending limits are off."
        path == "coordination.enforce_limits" -> if (tr) "Fiyat ve harcama denetimlerini açar. Çalıştırma başına çağrı, token ve süre sınırları her zaman uygulanır." else "Controls price and spending checks. Per-run call, token and time limits always apply."
        path.endsWith("microdollars") || path.endsWith("microdollars_per_token") -> if (tr) "1.000.000 mikrodolar = 1 ABD doları." else "1,000,000 microdollars = US\$1."
        else -> (schema["description"] as? JsonPrimitive)?.content.orEmpty()
    }
    fun label(key: String, tr: Boolean): String = when (key) {
        "off" -> if (tr) "Kapalı" else "Off"
        "enabled" -> if (tr) "Açık" else "Enabled"
        "minimal" -> if (tr) "En düşük" else "Minimal"
        "low" -> if (tr) "Düşük" else "Low"
        "medium" -> if (tr) "Orta" else "Medium"
        "high" -> if (tr) "Yüksek" else "High"
        "xhigh" -> if (tr) "Çok yüksek" else "Extra high"
        "max" -> if (tr) "Azami" else "Maximum"
        else -> labels[key]?.let { if (tr) it.second else it.first }
        ?: key.replace('_', ' ').replaceFirstChar { it.uppercase() }
    }
    private val labels = mapOf(
        "run" to ("Per-run limits" to "Çalıştırma sınırları"),
        "tokens" to ("Reserved token limit" to "Ayrılan token sınırı"),
        "calls" to ("Model call limit" to "Model çağrısı sınırı"),
        "microdollars" to ("Spending limit (microdollars)" to "Harcama sınırı (mikrodolar)"),
        "seconds" to ("Time limit (seconds)" to "Süre sınırı (saniye)"),
        "contributions" to ("Agent contribution limit" to "Ajan katkısı sınırı"),
        "concurrency" to ("Simultaneous model calls" to "Eşzamanlı model çağrıları"),
        "paused" to ("Pause spending" to "Harcamayı duraklat"),
        "enforce_limits" to ("Enforce spending limits" to "Harcama sınırlarını uygula"),
        "prices" to ("Model prices" to "Model fiyatları"),
        "microdollars_per_token" to ("Price per token (microdollars)" to "Token başına fiyat (mikrodolar)"),
        "extra_output_ceiling" to ("Additional reasoning token allowance" to "Ek düşünme token hakkı"),
        "image_token_ceiling" to ("Token allowance per image" to "Görsel başına token hakkı"),
        "main_model" to ("Main model" to "Ana model"),
        "background_model" to ("Background model" to "Arka plan modeli"),
        "agent_models" to ("Models by agent" to "Ajana göre modeller"),
        "telegram_models" to ("Telegram models" to "Telegram modelleri"),
        "worker_models" to ("Models by worker" to "Çalışana göre modeller"),
        "workers" to ("Worker thinking" to "Çalışan düşünme ayarları"),
        "models" to ("Model thinking" to "Model düşünme ayarları"),
        "roles" to ("Role thinking" to "Rol düşünme ayarları"),
        "agents" to ("Agent thinking" to "Ajan düşünme ayarları"),
        "descriptors" to ("Model capabilities" to "Model yetenekleri"),
        "fallbacks" to ("Fallback models (in order)" to "Yedek modeller (sıralı)"),
        "show_thinking" to ("Show thinking" to "Düşünmeyi göster"),
        "budget_mode" to ("Budget mode" to "Tasarruf modu"),
        "daily_calls" to ("Daily model call limit" to "Günlük model çağrısı sınırı"),
        "daily_tokens" to ("Daily reserved token limit" to "Günlük ayrılan token sınırı"),
        "daily_microdollars" to ("Daily spending limit (microdollars)" to "Günlük harcama sınırı (mikrodolar)"),
        "lifetime_calls" to ("Lifetime model call limit" to "Toplam model çağrısı sınırı"),
        "lifetime_tokens" to ("Lifetime token limit" to "Toplam token sınırı"),
        "lifetime_microdollars" to ("Lifetime spending limit (microdollars)" to "Toplam harcama sınırı (mikrodolar)"),
        "concurrent_calls" to ("Simultaneous model calls" to "Eşzamanlı model çağrıları"),
        "external_prices" to ("Speech resource prices" to "Konuşma kaynak fiyatları"),
        "run_seconds" to ("Run time limit (seconds)" to "Çalıştırma süresi sınırı (saniye)"),
        "units_per_run" to ("Units per maintenance run" to "Bakım çalıştırması başına birim"),
        "telegram_enabled" to ("Enable Telegram notifications" to "Telegram bildirimlerini etkinleştir"),
        "quiet_start" to ("Quiet hours start (0–23)" to "Sessiz saat başlangıcı (0–23)"),
        "quiet_end" to ("Quiet hours end (0–23)" to "Sessiz saat bitişi (0–23)"),
    )
}
