// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later
package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.layout.Column
import androidx.compose.runtime.*
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.ConfigPolicy
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.i18n.Tr
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject

/** Full native policy documents; each save delegates to its existing revisioned writer. */
@Composable
fun ConfigPoliciesView(config: AppConfig, graph: AppGraph) {
    val scope = rememberCoroutineScope()
    val tr = LocalStrings.current == Tr
    val pretty = remember { Json { prettyPrint = true } }
    var policies by remember { mutableStateOf<List<ConfigPolicy>>(emptyList()) }
    val drafts = remember { mutableStateMapOf<String, String>() }
    val expanded = remember { mutableStateMapOf<String, Boolean>() }
    val schemas = remember { mutableStateMapOf<String, Boolean>() }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf("") }
    suspend fun load() {
        try { policies = graph.api.getConfigPolicies(config); message = "" }
        catch (e: Exception) { message = e.message ?: "Policies unavailable" }
    }
    LaunchedEffect(config) { load() }
    Column {
        SectionHeader(if (tr) "Gelişmiş çalışma politikaları" else "Advanced runtime policies")
        Hint(if (tr) "Tüm model, düşünme, bütçe, çalışan ve Iacon alanları. Politika kayıtları yeniden başlatmadan etkinleşir." else "Every model, thinking, budget, worker and Iacon policy field. Policy saves apply without a restart.")
        if (message.isNotEmpty()) Hint(message)
        SettingsButton(if (tr) "Politikaları yeniden yükle (taslak korunur)" else "Reload policies (keep drafts)", enabled = !busy, onClick = { scope.launch { load() } })
        policies.forEach { policy ->
            SettingsButton("${policy.label} · v${policy.revision}", onClick = { expanded[policy.id] = expanded[policy.id] != true })
            if (expanded[policy.id] == true) {
                GlassField(
                    value = drafts[policy.id] ?: pretty.encodeToString(JsonObject.serializer(), policy.value),
                    onValueChange = { if (!busy) drafts[policy.id] = it },
                    singleLine = false, mono = true, placeholder = "JSON",
                )
                SettingsButton(if (tr) "Politikayı kaydet" else "Save policy", enabled = !busy && policy.id in drafts, onClick = {
                    scope.launch {
                        busy = true; message = ""
                        try {
                            val value = Json.parseToJsonElement(drafts.getValue(policy.id)).jsonObject
                            policies = graph.api.saveConfigPolicy(config, policy, value)
                            drafts.remove(policy.id)
                            message = if (tr) "Kaydedildi" else "Saved"
                        } catch (e: Exception) { message = e.message ?: "Policy save failed" }
                        finally { busy = false }
                    }
                })
                SettingsButton(if (tr) "Tüm alanlar ve sınırlar" else "All fields and constraints", onClick = { schemas[policy.id] = schemas[policy.id] != true })
                if (schemas[policy.id] == true) Hint(pretty.encodeToString(JsonObject.serializer(), policy.schema))
            }
        }
    }
}
