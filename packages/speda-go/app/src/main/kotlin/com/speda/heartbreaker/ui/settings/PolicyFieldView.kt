// SPDX-License-Identifier: AGPL-3.0-or-later
package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.speda.heartbreaker.domain.PolicyFields
import kotlinx.serialization.json.*

/** Native settings controls for nested policy objects, lists, maps and nullable values. */
@Composable
internal fun PolicyFieldView(
    schema: JsonObject, root: JsonObject, value: JsonElement?, path: String, label: String,
    tr: Boolean, enabled: Boolean, onChange: (JsonElement) -> Unit,
) {
    val resolved = PolicyFields.resolve(schema, root)
    val selected = PolicyFields.selected(schema, root, value)
    val options = PolicyFields.choices(schema, root)
    val kind = PolicyFields.type(selected)
    val hint = PolicyFields.hint(path, resolved, tr)
    val objectValue = value as? JsonObject ?: JsonObject(emptyMap())
    val arrayValue = value as? JsonArray ?: JsonArray(emptyList())
    var entryKey by remember(path) { mutableStateOf("") }
    var keyError by remember(path) { mutableStateOf("") }
    Column(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionHeader(label)
        if (hint.isNotEmpty()) Hint(hint)
        if ((resolved["anyOf"] as? JsonArray)?.any { PolicyFields.type(it.jsonObject) == "null" } == true) {
            ToggleRow(if (tr) "Varsayılanı kullan / geçersiz kılma yok" else "Use default / no override", "", value == JsonNull, enabled) {
                onChange(if (it) JsonNull else PolicyFields.default(schema, root))
            }
        }
        if (value != JsonNull) {
            if (options.size > 1) PolicySelect(
                options.indexOf(selected).coerceAtLeast(0),
                options.map { if (PolicyFields.type(it) == "integer") (if (tr) "Token sayısı" else "Token count") else (if (tr) "Düzey adı" else "Named level") }, enabled,
            ) { onChange(PolicyFields.default(options[it], root)) }
            when (kind) {
                "object" -> Column(Modifier.fillMaxWidth().padding(start = 12.dp)) {
                    val properties = selected["properties"] as? JsonObject ?: JsonObject(emptyMap())
                    properties.forEach { (key, childSchema) -> key(path, key) {
                        PolicyFieldView(childSchema.jsonObject, root, objectValue[key], "$path.$key", PolicyFields.label(key, tr), tr, enabled) {
                            onChange(JsonObject(objectValue + (key to it)))
                        }
                    } }
                    val entrySchema = selected["additionalProperties"] as? JsonObject
                    if (entrySchema != null) {
                        objectValue.filterKeys { it !in properties }.forEach { (name, childValue) -> key(path, name) {
                            var expanded by remember { mutableStateOf(false) }
                            SettingsButton("${if (expanded) "▾" else "▸"} $name", onClick = { expanded = !expanded })
                            if (expanded) {
                                PolicyFieldView(entrySchema, root, childValue, "$path.$name", name, tr, enabled) {
                                    onChange(JsonObject(objectValue + (name to it)))
                                }
                                SettingsButton(if (tr) "Kaldır: $name" else "Remove $name", enabled = enabled, onClick = { onChange(JsonObject(objectValue - name)) })
                            }
                        } }
                        if (objectValue.isEmpty()) Hint(if (tr) "Henüz bir kayıt yok." else "No entries yet.")
                        GlassField(entryKey, { entryKey = it; keyError = "" }, if (tr) "Model, ajan veya kaynak adı" else "Model, agent or resource name", true, enabled = enabled)
                        SettingsButton(if (tr) "Kayıt ekle" else "Add entry", enabled = enabled && entryKey.isNotBlank(), onClick = {
                            val name = entryKey.trim()
                            if (name in objectValue) keyError = if (tr) "Bu kayıt zaten var." else "That entry already exists."
                            else { onChange(JsonObject(objectValue + (name to PolicyFields.default(entrySchema, root)))); entryKey = "" }
                        })
                        if (keyError.isNotEmpty()) Hint(keyError)
                    }
                }
                "array" -> Column(Modifier.fillMaxWidth().padding(start = 12.dp)) {
                    val items = selected["items"] as? JsonObject ?: JsonObject(emptyMap())
                    arrayValue.forEachIndexed { index, childValue -> key(path, index) {
                        PolicyFieldView(items, root, childValue, "$path.$index", "$label ${index + 1}", tr, enabled) { next ->
                            onChange(JsonArray(arrayValue.mapIndexed { i, old -> if (i == index) next else old }))
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            SettingsButton(if (tr) "Yukarı taşı" else "Move up", enabled = enabled && index > 0, onClick = {
                                val list = arrayValue.toMutableList(); val previous = list[index - 1]; list[index - 1] = list[index]; list[index] = previous
                                onChange(JsonArray(list))
                            })
                            SettingsButton(if (tr) "Kaldır" else "Remove", enabled = enabled && arrayValue.size > ((selected["minItems"] as? JsonPrimitive)?.intOrNull ?: 0), onClick = {
                                onChange(JsonArray(arrayValue.filterIndexed { i, _ -> i != index }))
                            })
                        }
                    } }
                    SettingsButton(if (tr) "Öğe ekle" else "Add item", enabled = enabled && arrayValue.size < ((selected["maxItems"] as? JsonPrimitive)?.intOrNull ?: Int.MAX_VALUE), onClick = {
                        onChange(JsonArray(arrayValue + PolicyFields.default(items, root)))
                    })
                }
                "boolean" -> HbToggle((value as? JsonPrimitive)?.booleanOrNull == true, enabled = enabled) { onChange(JsonPrimitive(it)) }
                else -> {
                    val enum = selected["enum"] as? JsonArray
                    if (enum != null) PolicySelect(enum.indexOf(value).coerceAtLeast(0), enum.map { it.jsonPrimitive.content.replace('_', ' ') }, enabled) { onChange(enum[it]) }
                    else {
                        val numeric = kind == "integer" || kind == "number"
                        GlassField(
                            value = (value as? JsonPrimitive)?.content.orEmpty(),
                            onValueChange = { onChange(if (numeric) PolicyFields.number(it, kind == "integer") else JsonPrimitive(it)) },
                            placeholder = label, singleLine = true, enabled = enabled,
                            keyboardOptions = KeyboardOptions(keyboardType = if (numeric) KeyboardType.Decimal else KeyboardType.Text),
                        )
                        val min = selected["minimum"] ?: selected["exclusiveMinimum"]
                        val max = selected["maximum"] ?: selected["exclusiveMaximum"]
                        if (min != null || max != null) Hint("${if (tr) "Aralık" else "Range"}: ${min ?: "—"} – ${max ?: "—"}")
                    }
                }
            }
        }
    }
}

@Composable
private fun PolicySelect(selected: Int, labels: List<String>, enabled: Boolean, onChange: (Int) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Column {
        SettingsButton("${labels.getOrNull(selected).orEmpty()} ▾", enabled = enabled, onClick = { expanded = !expanded })
        if (expanded) labels.forEachIndexed { index, label ->
            SettingsButton(label, enabled = enabled, onClick = { onChange(index); expanded = false })
        }
    }
}
