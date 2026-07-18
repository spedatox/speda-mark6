package com.speda.heartbreaker.ui.switcher

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.theme.ThemeEngine
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * AGENT SWITCHER — the armoury, ported from AgentSwitcherOverlay.tsx for touch.
 * The room glows in the focused agent's hue, the roster is a scrollable bay of
 * avatar pods.
 *
 * Transition: a clean radial bloom (light diffusing through glass) masks the
 * agent swap. No particles, no shards, no rotation — just colour, depth, and
 * deliberate timing. The UI pulls back gently, the bloom rises, the agent
 * changes behind full coverage, and the bloom dissolves to reveal the new theme.
 */
@Composable
fun AgentSwitcherOverlay(
    currentAgentId: String,
    onSelect: (String) -> Unit,
    onClose: () -> Unit,
) {
    val roster = Brands.ROSTER
    var selected by remember { mutableIntStateOf(roster.indexOf(currentAgentId).coerceAtLeast(0)) }
    var confirming by remember { mutableStateOf<Int?>(null) }
    
    // Internal animation state to manage the cinematic sequence
    val flashAlpha = remember { Animatable(0f) }
    val engageProgress by animateFloatAsState(
        targetValue = if (confirming != null) 1f else 0f,
        animationSpec = tween(1200, easing = FastOutSlowInEasing),
        label = "engageProgress"
    )

    val selId = roster[selected]
    val selColor by animateColorAsState(ThemeEngine.parseHex(Brands.agentColor(selId)), tween(600), label = "selColor")
    val selBrand = Brands.BRANDS[selId]

    // CINEMATIC SEQUENCE:
    // 1. User confirms agent — bloom rises from center.
    // 2. UI gently pulls back (scale + blur).
    // 3. At ~90% bloom, the agent swap fires behind full coverage.
    // 4. Brief hold for the theme engine to re-hue the palette.
    // 5. Bloom dissolves; switcher closes.
    LaunchedEffect(confirming) {
        val c = confirming ?: return@LaunchedEffect

        launch {
            flashAlpha.animateTo(1f, tween(600, easing = FastOutSlowInEasing))
        }

        delay(650)
        onSelect(roster[c])

        delay(500)

        flashAlpha.animateTo(0f, tween(450, easing = FastOutSlowInEasing))
        onClose()
    }

    Box(
        Modifier
            .fillMaxSize()
            .background(Color(0xFF020406))
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClose),
    ) {
        // Ambient background glow
        Box(Modifier.fillMaxSize().background(Brush.radialGradient(listOf(selColor.copy(alpha = 0.25f), Color.Transparent))))

        Column(
            Modifier
                .fillMaxSize()
                .padding(vertical = 28.dp)
                .graphicsLayer {
                    // Spatial pull-away effect
                    val p = engageProgress
                    scaleX = 1f - (p * 0.12f)
                    scaleY = 1f - (p * 0.12f)
                    alpha = 1f - (p * 0.8f)
                    translationY = -p * 40.dp.toPx()
                }
                .blur(if (confirming != null) (30.dp * engageProgress) else 0.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            // ── Title ─────────────────────────────────────────────────────────
            HbText("ARMOURY // SPEDA MARK VI", style = HbType.readout.copy(fontSize = 10.sp, letterSpacing = 0.28.em), color = selColor, caps = true)
            Spacer(Modifier.height(10.dp))
            HbText("SELECT YOUR AGENT", style = HbType.headerBar.copy(fontSize = 26.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.24.em), color = Color(0xFFEEF7FA), caps = true)
            Spacer(Modifier.height(10.dp))
            Box(
                Modifier.width(200.dp).height(2.dp)
                    .background(Brush.horizontalGradient(listOf(Color.Transparent, selColor, Color.Transparent))),
            )

            Spacer(Modifier.height(28.dp))

            // ── The bay ───────────────────────────────────────────────────────
            BoxWithConstraints(Modifier.fillMaxWidth()) {
                val density = LocalDensity.current
                val vpPx = with(density) { maxWidth.toPx() }
                val itemPx = with(density) { 124.dp.toPx() }
                val listState = rememberLazyListState()
                LaunchedEffect(selected) {
                    listState.animateScrollToItem(selected, (-((vpPx - itemPx) / 2f)).toInt())
                }
                LazyRow(
                    state = listState,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth().height(230.dp),
                ) {
                    itemsIndexed(roster) { index, id ->
                        Pod(
                            id = id,
                            selected = index == selected,
                            confirming = confirming == index,
                            dimmed = confirming != null && confirming != index,
                            onTap = { 
                                if (index == selected) { 
                                    if (confirming == null) confirming = index 
                                } else {
                                    selected = index 
                                }
                            },
                        )
                    }
                }
            }

            Spacer(Modifier.height(20.dp))

            // ── Designation panel ─────────────────────────────────────────────
            AnimatedContent(
                targetState = selBrand,
                transitionSpec = {
                    (slideInVertically { height -> height / 2 } + fadeIn(tween(300)))
                        .togetherWith(slideOutVertically { height -> -height / 2 } + fadeOut(tween(300)))
                        .using(SizeTransform(clip = false))
                },
                label = "infoTransition"
            ) { targetBrand ->
                Column(
                    Modifier
                        .padding(horizontal = 24.dp)
                        .clip(RoundedCornerShape(12.dp))
                        .background(Color(0xFF081018).copy(alpha = 0.72f))
                        .padding(horizontal = 22.dp, vertical = 12.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    HbText(
                        "${targetBrand?.name} · ${targetBrand?.modelNumber}",
                        style = HbType.headerBar.copy(fontSize = 16.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.18.em),
                        color = Color(0xFFEEF7FA),
                        caps = true,
                        maxLines = 1,
                    )
                    Spacer(Modifier.height(4.dp))
                    HbText(
                        targetBrand?.tagline ?: "",
                        style = HbType.readout.copy(fontSize = 11.sp, letterSpacing = 0.06.em),
                        color = LocalHbPalette.current.textDim,
                        caps = true,
                        maxLines = 1,
                    )
                }
            }

            Spacer(Modifier.height(18.dp))

            // ── Hint ──────────────────────────────────────────────────────────
            HbText(
                "TAP TO SELECT · TAP AGAIN TO ENGAGE",
                style = HbType.readout.copy(fontSize = 10.sp, letterSpacing = 0.14.em),
                color = LocalHbPalette.current.amberBright,
                caps = true,
            )
        }

        // Full-screen bloom + reactor shockwave — light detonating through glass
        if (confirming != null) {
            Box(Modifier.fillMaxSize().alpha(flashAlpha.value)) {
                RadialBloom(selColor, engageProgress)
            }
        }
    }
}

@Composable
private fun Pod(
    id: String,
    selected: Boolean,
    confirming: Boolean,
    dimmed: Boolean,
    onTap: () -> Unit,
) {
    val palette = LocalHbPalette.current
    val color = ThemeEngine.parseHex(Brands.agentColor(id))
    val brand = Brands.BRANDS[id] ?: return

    val scale by animateFloatAsState(if (selected) 1.12f else 0.9f, tween(450), label = "podScale")
    val alpha by animateFloatAsState(if (dimmed) 0.15f else if (selected) 1f else 0.45f, tween(400), label = "podAlpha")
    val flare by animateFloatAsState(if (confirming) 1.8f else 1f, tween(800, easing = FastOutSlowInEasing), label = "podFlare")

    Column(
        Modifier
            .width(120.dp)
            .alpha(alpha)
            .padding(vertical = 6.dp)
            .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onTap),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(Modifier.size(100.dp).scale(scale * (if (confirming) flare else 1f)), contentAlignment = Alignment.Center) {
            if (selected) Rings(color)
            // Avatar
            Box(
                Modifier
                    .size(58.dp)
                    .scale(if (selected) 1.14f else 1f)
                    .clip(CircleShape)
                    .background(color.copy(alpha = if (selected) 0.28f else 0.16f)),
                contentAlignment = Alignment.Center,
            ) {
                HbText(
                    Brands.monogram(id),
                    style = HbType.headerBar.copy(fontSize = 17.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.06.em),
                    color = if (selected) Color.White else color,
                )
            }
        }
        Spacer(Modifier.height(12.dp))
        HbText(
            brand.name,
            style = HbType.headerBar.copy(fontSize = if (selected) 15.sp else 13.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.12.em),
            color = if (selected) Color.White else palette.textDim,
            caps = true,
            maxLines = 1,
        )
        HbText(
            brand.modelNumber,
            style = HbType.readout.copy(fontSize = 9.5.sp, letterSpacing = 0.14.em),
            color = if (selected) color else palette.textFaint,
            caps = true,
            maxLines = 1,
        )
    }
}

/** Dual counter-rotating HUD arcs behind the selected avatar. */
@Composable
private fun Rings(color: Color) {
    val t = rememberInfiniteTransition(label = "rings")
    val outer by t.animateFloat(0f, 360f, infiniteRepeatable(tween(14000, easing = LinearEasing), RepeatMode.Restart), label = "outer")
    val inner by t.animateFloat(360f, 0f, infiniteRepeatable(tween(6000, easing = LinearEasing), RepeatMode.Restart), label = "inner")

    Box(Modifier.size(100.dp), contentAlignment = Alignment.Center) {
        Box(Modifier.size(74.dp).clip(CircleShape).background(Brush.radialGradient(listOf(color.copy(alpha = 0.35f), Color.Transparent))))
        Canvas(Modifier.size(100.dp)) {
            val d = size.minDimension
            // outer dashed full ring
            rotate(outer) {
                drawCircle(
                    color = color.copy(alpha = 0.7f),
                    radius = d / 2f - 3.dp.toPx(),
                    style = Stroke(width = 1.4.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(2f * density, 9f * density))),
                )
            }
            // inner bright arc
            rotate(inner) {
                val r = d / 2f - 16.dp.toPx()
                drawArc(
                    color = color,
                    startAngle = 0f,
                    sweepAngle = 100f,
                    useCenter = false,
                    topLeft = Offset((size.width - 2 * r) / 2f, (size.height - 2 * r) / 2f),
                    size = Size(2 * r, 2 * r),
                    style = Stroke(width = 2.4.dp.toPx(), cap = StrokeCap.Round),
                )
            }
        }
    }
}

/**
 * Layered radial bloom — light diffusing through glass.
 * No particles, no rotation, no shards. Just clean colour blooming outward
 * like a lens flare melting into the screen. Premium and deliberate.
 */
@Composable
private fun RadialBloom(color: Color, progress: Float) {
    Box(Modifier.fillMaxSize().background(Color(0xFF020406).copy(alpha = 0.3f))) {
        // Outer wash — the agent's accent bleeding into the void
        Box(Modifier.fillMaxSize().background(
            Brush.radialGradient(
                0.0f to color.copy(alpha = 0.22f),
                0.35f to color.copy(alpha = 0.10f),
                0.7f to color.copy(alpha = 0.02f),
                1.0f to Color.Transparent,
                radius = 2200f,
            )
        ))
        // Inner core — a soft point of light at centre
        Box(Modifier.fillMaxSize().background(
            Brush.radialGradient(
                0.0f to Color.White.copy(alpha = 0.12f),
                0.12f to color.copy(alpha = 0.18f),
                0.35f to Color.Transparent,
                radius = 900f,
            )
        ))
        // Reactor shockwave — twin rings (accent + white spark) detonating from
        // centre and fading as they grow. The pulse that re-hues the whole app.
        Canvas(Modifier.fillMaxSize()) {
            val center = Offset(size.width / 2f, size.height / 2f)
            val maxR = kotlin.math.hypot(size.width.toDouble(), size.height.toDouble()).toFloat() / 2f
            val fade = (1f - progress).coerceIn(0f, 1f)
            drawCircle(
                color = color.copy(alpha = 0.55f * fade),
                radius = progress * maxR,
                center = center,
                style = Stroke(width = 3.dp.toPx()),
            )
            drawCircle(
                color = Color.White.copy(alpha = 0.32f * fade),
                radius = progress * 0.82f * maxR,
                center = center,
                style = Stroke(width = 1.5.dp.toPx()),
            )
        }
    }
}
