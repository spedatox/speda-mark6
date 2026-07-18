package com.speda.heartbreaker.ui.chat

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.speda.heartbreaker.domain.ToolBadge
import com.speda.heartbreaker.domain.ToolStatus
import com.speda.heartbreaker.domain.inputRows
import com.speda.heartbreaker.domain.str
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.ui.HbText

// Fixed diff colours (not themed — same in the web).
private val ADD_BG = Color(red = 74, green = 222, blue = 128).copy(alpha = 0.09f)
private val ADD_FG = Color(red = 134, green = 239, blue = 172, alpha = 242)
private val REM_BG = Color(red = 248, green = 113, blue = 113).copy(alpha = 0.09f)
private val REM_FG = Color(red = 252, green = 165, blue = 165, alpha = 235)

/** Live tool feed — one row per tool, click to expand diff / command / detail. */
@Composable
fun ToolFeed(tools: List<ToolBadge>, streaming: Boolean, modifier: Modifier = Modifier) {
    if (tools.isEmpty()) return
    val palette = LocalHbPalette.current
    Column(
        modifier = modifier
            .padding(vertical = 6.dp)
            .clip(RoundedCornerShape(topEnd = 6.dp, bottomEnd = 6.dp))
            .background(palette.void.copy(alpha = 0.35f))
            .padding(start = 10.dp, top = 6.dp, bottom = 6.dp, end = 8.dp),
    ) {
        tools.forEachIndexed { i, t ->
            ToolRow(tool = t, live = streaming && i == tools.lastIndex)
        }
    }
}

@Composable
private fun ToolRow(tool: ToolBadge, live: Boolean) {
    val palette = LocalHbPalette.current
    var open by remember { mutableStateOf(false) }
    val (verb, target) = ToolStatus.toolSummary(tool)

    val action = tool.str("action")
    val isEdit = tool.name == "edit_file"
    val isWrite = tool.name == "write_file" || (tool.name == "system_ops" && action == "write_file")
    val isCmd = tool.name == "run_command" || (tool.name == "system_ops" && action != "read_file" && action != "write_file")
    val hasDetail = isEdit || isWrite || isCmd || tool.result != null || tool.inputRows().isNotEmpty()
    val pending = live && tool.result == null

    Column {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier
                .then(if (hasDetail) Modifier.clickable { open = !open } else Modifier)
                .padding(vertical = 3.dp),
        ) {
            if (pending) Spinner(size = 12.dp) else Box(
                Modifier.size(6.dp).clip(CircleShape).background(palette.accentDim),
            )
            HbText(verb, style = HbType.code, color = palette.iconBright, maxLines = 1)
            if (target != null) {
                HbText(
                    target,
                    style = HbType.code,
                    color = palette.textDim,
                    maxLines = 1,
                    modifier = Modifier.weight(1f, fill = false),
                )
            }
        }
        if (open && hasDetail) {
            Box(Modifier.padding(start = 16.dp, top = 4.dp, bottom = 6.dp)) {
                when {
                    isEdit -> DiffBlock(removed = tool.str("old_string"), added = tool.str("new_string"))
                    isWrite -> DiffBlock(added = tool.str("content"))
                    isCmd -> CommandBlock(command = tool.str("command"), result = tool.result)
                    else -> GenericDetail(tool)
                }
            }
        }
    }
}

@Composable
private fun DiffBlock(removed: String? = null, added: String? = null) {
    val rows = buildList {
        removed?.split('\n')?.forEach { add('-' to it) }
        added?.split('\n')?.forEach { add('+' to it) }
    }
    if (rows.isEmpty()) return
    Column(
        Modifier
            .clip(RoundedCornerShape(6.dp))
            // Bounded height BEFORE verticalScroll: this block lives inside the
            // transcript LazyColumn, which measures children with an infinite max
            // height — an unbounded vertical scroller under that constraint throws
            // and crashes the app the moment the detail expands (web: max-height 180).
            .heightIn(max = 240.dp)
            .verticalScroll(rememberScrollState()),
    ) {
        rows.forEach { (sign, text) ->
            Row(
                Modifier
                    .background(if (sign == '+') ADD_BG else REM_BG)
                    .padding(horizontal = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                HbText(sign.toString(), style = HbType.code, color = (if (sign == '+') ADD_FG else REM_FG).copy(alpha = 0.55f))
                HbText(text.ifEmpty { " " }, style = HbType.code, color = if (sign == '+') ADD_FG else REM_FG)
            }
        }
    }
}

@Composable
private fun CommandBlock(command: String?, result: String?) {
    val palette = LocalHbPalette.current
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        if (command != null) {
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                HbText("$", style = HbType.code, color = palette.iconDim)
                HbText(command, style = HbType.code, color = palette.accentBright)
            }
        }
        if (result != null) {
            HbText(
                result,
                style = HbType.code,
                color = palette.iconBright,
                modifier = Modifier
                    .heightIn(max = 200.dp)
                    .verticalScroll(rememberScrollState()),
            )
        }
    }
}

@Composable
private fun GenericDetail(tool: ToolBadge) {
    val palette = LocalHbPalette.current
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        tool.inputRows().forEach { (k, v) ->
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                HbText("$k:", style = HbType.code, color = palette.iconDim)
                HbText(v, style = HbType.code, color = palette.icon, maxLines = 6)
            }
        }
        tool.result?.let {
            HbText(
                it,
                style = HbType.code,
                color = palette.iconBright,
                modifier = Modifier
                    .heightIn(max = 200.dp)
                    .verticalScroll(rememberScrollState()),
            )
        }
    }
}

/** Rotating dashed-ring spinner (CSS `spin` keyframe). */
@Composable
fun Spinner(size: androidx.compose.ui.unit.Dp = 15.dp) {
    val palette = LocalHbPalette.current
    val transition = rememberInfiniteTransition(label = "spinner")
    val angle by transition.animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(tween(1000, easing = LinearEasing), RepeatMode.Restart),
        label = "spin",
    )
    androidx.compose.foundation.Canvas(Modifier.size(size).rotate(angle)) {
        val stroke = 1.6.dp.toPx()
        drawArc(
            color = palette.accent,
            startAngle = 0f,
            sweepAngle = 300f,
            useCenter = false,
            style = androidx.compose.ui.graphics.drawscope.Stroke(width = stroke, cap = androidx.compose.ui.graphics.StrokeCap.Round),
            topLeft = androidx.compose.ui.geometry.Offset(stroke, stroke),
            size = androidx.compose.ui.geometry.Size(this.size.width - stroke * 2, this.size.height - stroke * 2),
        )
    }
}
