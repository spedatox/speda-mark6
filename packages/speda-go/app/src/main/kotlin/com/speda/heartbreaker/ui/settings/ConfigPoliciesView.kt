// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later
package com.speda.heartbreaker.ui.settings

import androidx.compose.foundation.layout.Column
import androidx.compose.runtime.*
import com.speda.heartbreaker.AppGraph
import com.speda.heartbreaker.data.ConfigPolicy
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.domain.PolicyFields
import com.speda.heartbreaker.i18n.LocalStrings
import com.speda.heartbreaker.i18n.Tr
import kotlinx.coroutines.launch
import kotlinx.serialization.json.*

/** Normal settings controls; saves retain the original draft revision to prevent lost updates. */
@Composable
fun ConfigPoliciesView(config: AppConfig, graph: AppGraph) {
    val scope = rememberCoroutineScope()
    val tr = LocalStrings.current == Tr
    var policies by remember { mutableStateOf<List<ConfigPolicy>>(emptyList()) }
    val drafts = remember { mutableStateMapOf<String, JsonObject>() }
    val revisions = remember { mutableStateMapOf<String, Int>() }
    val expanded = remember { mutableStateMapOf<String, Boolean>() }
    var busy by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf("") }
    suspend fun load() {
        loading = true
        try { policies = graph.api.getConfigPolicies(config); message = "" }
        catch (e: Exception) { message = e.message ?: "Settings unavailable" }
        finally { loading = false }
    }
    LaunchedEffect(config) { drafts.clear(); revisions.clear(); load() }
    Column {
        SectionHeader(if (tr) "Model ve çalışma ayarları" else "Model and runtime settings")
        Hint(if (tr) "Bu ayarlar kaydedildiğinde yeni çalıştırmalara uygulanır. Sunucuyu yeniden başlatmanız gerekmez." else "These settings apply to new runs when saved. No server restart is needed.")
        if (message.isNotEmpty()) Hint(message)
        SettingsButton(if (tr) "Ayarları yenile (taslak korunur)" else "Reload settings (keep drafts)", enabled = !busy && !loading, onClick = { scope.launch { load() } })
        if (loading) Hint(if (tr) "Yükleniyor…" else "Loading…")
        policies.forEach { policy -> key(policy.id) {
            val title = when (policy.id) {
                "models" -> if (tr) "Modeller ve düşünme" else "Models and thinking"
                "coordination" -> if (tr) "Çalıştırma ve çalışan sınırları" else "Run and worker limits"
                "global" -> if (tr) "Genel harcama sınırları" else "Overall spending limits"
                "iacon" -> if (tr) "Iacon bakım ayarları" else "Iacon maintenance"
                else -> policy.label
            }
            val conflict = policy.id in drafts && revisions[policy.id] != policy.revision
            SettingsButton(title + (if (policy.id in drafts) (if (tr) " · Kaydedilmedi" else " · Unsaved") else ""), onClick = { expanded[policy.id] = expanded[policy.id] != true })
            if (expanded[policy.id] == true) {
                if (conflict) Hint(if (tr) "Bu ayarlar başka bir yerde değişti. Son kayıtlı değerleri kullanmak için değişiklikleri geri alın." else "These settings changed elsewhere. Discard your edits to use the latest saved values.")
                (policy.schema["properties"] as? JsonObject)?.forEach { (name, schema) -> key(name) {
                    PolicyFieldView(schema.jsonObject, policy.schema, (drafts[policy.id] ?: policy.value)[name], "${policy.id}.$name", PolicyFields.label(name, tr), tr, !busy && !loading) {
                        if (policy.id !in revisions) revisions[policy.id] = policy.revision
                        drafts[policy.id] = JsonObject((drafts[policy.id] ?: policy.value) + (name to it))
                        message = ""
                    }
                } }
                SettingsButton(if (tr) "Ayarları kaydet" else "Save settings", enabled = !busy && !loading && !conflict && policy.id in drafts, onClick = {
                    val value = drafts.getValue(policy.id)
                    val errors = PolicyFields.validate(policy.schema, policy.schema, value)
                    if (errors.isNotEmpty()) message = (if (tr) "Alanları kontrol edin: " else "Check these fields: ") + errors.joinToString(" ")
                    else scope.launch {
                        busy = true; message = ""
                        try {
                            policies = graph.api.saveConfigPolicy(config, policy.copy(revision = revisions.getValue(policy.id)), value)
                            drafts.remove(policy.id); revisions.remove(policy.id)
                            message = if (tr) "Kaydedildi" else "Saved"
                        } catch (e: Exception) { message = e.message ?: "Settings save failed" }
                        finally { busy = false }
                    }
                })
                if (policy.id in drafts) SettingsButton(if (tr) "Değişiklikleri geri al" else "Discard changes", enabled = !busy && !loading, onClick = {
                    drafts.remove(policy.id); revisions.remove(policy.id); message = ""
                })
            }
        } }
    }
}
