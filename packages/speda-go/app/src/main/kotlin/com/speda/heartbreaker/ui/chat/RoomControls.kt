package com.speda.heartbreaker.ui.chat

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.TextButton
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.graphics.Color
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.designsystem.brand.AgentMark
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.RoomInfo
import com.speda.heartbreaker.domain.canonicalRoomAgent
import com.speda.heartbreaker.ui.HbText
import com.speda.heartbreaker.ui.HbGlassButton

fun roomName(id: String) = Brands.BRANDS[id]?.name ?: id
fun roomColor(id: String) = Color(0xff000000L or Brands.agentColor(id).removePrefix("#").toLong(16))

@Composable
fun RoomMembers(room: RoomInfo, target: String?, onTarget: (String?) -> Unit, onManage: () -> Unit, turkish: Boolean) {
    val palette = LocalHbPalette.current
    var choosingTarget by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Box {
            HbGlassButton(if (turkish) "Kime: ${roomName(target ?: room.lead)} ▾" else "To: ${roomName(target ?: room.lead)} ▾", onClick = { choosingTarget = true })
            DropdownMenu(expanded = choosingTarget, onDismissRequest = { choosingTarget = false }) {
                (listOf<String?>(null) + room.members.filter { it != room.lead }).forEach { id ->
                    DropdownMenuItem(text = { HbText(roomName(id ?: room.lead), style = HbType.read, color = palette.text) },
                        onClick = { onTarget(id); choosingTarget = false })
                }
            }
        }
        room.members.forEach { id ->
            AgentMark(id, roomColor(id), 24.dp)
            HbGlassButton(roomName(id) + if (id == room.lead) " · ${if (turkish) "Lider" else "Lead"}" else "", onClick = onManage)
        }
    }
}

@Composable
fun RoomActions(room: RoomInfo?, busy: Boolean, error: String?, turkish: Boolean, onChange: (String, String) -> Unit, onClose: () -> Unit) {
    val palette = LocalHbPalette.current
    AlertDialog(onDismissRequest = onClose,
        title = { HbText(if (turkish) "Katılımcılar" else "Participants", style = HbType.read, color = palette.text) },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (room == null) HbText(if (turkish) "Önce bir sohbet başlatın." else "Start a conversation first.", style = HbType.read, color = palette.text)
                else {
                    room.members.forEach { id ->
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            HbText(roomName(id), style = HbType.read, color = palette.text)
                            if (id == room.lead) HbText(if (turkish) "Lider" else "Lead", style = HbType.read, color = palette.textDim)
                            else {
                                TextButton(enabled = !busy, onClick = { onChange("dismiss", id) }) { HbText(if (turkish) "Çıkar" else "Dismiss", style = HbType.label, color = palette.text) }
                                TextButton(enabled = !busy, onClick = { onChange("handoff", id) }) { HbText(if (turkish) "Lider yap" else "Make lead", style = HbType.label, color = palette.text) }
                            }
                        }
                    }
                    Brands.ROSTER.map(::canonicalRoomAgent).filter { it !in room.members }.forEach { id ->
                        TextButton(enabled = !busy, onClick = { onChange("invite", id) }) {
                            HbText((if (turkish) "Çağır: " else "Summon: ") + roomName(id), style = HbType.read, color = palette.accentBright)
                        }
                    }
                }
                error?.let { HbText(it, style = HbType.read, color = palette.red) }
            }
        },
        confirmButton = { TextButton(onClick = onClose) { HbText(if (turkish) "Kapat" else "Close", style = HbType.label, color = palette.text) } },
        containerColor = palette.void,
    )
}
