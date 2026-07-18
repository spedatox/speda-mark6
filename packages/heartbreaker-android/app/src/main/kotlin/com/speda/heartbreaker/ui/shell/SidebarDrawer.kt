package com.speda.heartbreaker.ui.shell

import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.designsystem.brand.Brand
import com.speda.heartbreaker.designsystem.brand.Brands
import com.speda.heartbreaker.designsystem.glass.HbGlassShape
import com.speda.heartbreaker.designsystem.glass.HbGlassState
import com.speda.heartbreaker.designsystem.glass.LocalHazeState
import com.speda.heartbreaker.designsystem.glass.hbGlass
import com.speda.heartbreaker.designsystem.glass.hbSeamBottom
import com.speda.heartbreaker.designsystem.glass.hbSeamTop
import dev.chrisbanes.haze.HazeTint
import dev.chrisbanes.haze.hazeEffect
import com.speda.heartbreaker.designsystem.motion.Motion
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.theme.ThemeEngine
import com.speda.heartbreaker.designsystem.icons.HbGlyphs
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.domain.Session
import com.speda.heartbreaker.domain.groupSessions
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.delay

/** The mobile drawer sheet fill — `rgba(8, 14, 20, 0.55)` over its own blur. */
private val DRAWER_FILL = Color(0xFF080E14).copy(alpha = 0.55f)
/** No backdrop available (previews): occlude instead, since nothing will frost. */
private val DRAWER_FILL_OPAQUE = Color(0xFF080E14).copy(alpha = 0.94f)

/**
 * The off-canvas sidebar drawer — port of Sidebar.tsx in mobile mode:
 * `min(84vw, 330px)`, sliding on the 0.28s cubic-bezier(0.32,0.72,0.33,1) curve
 * behind a frosted backdrop sheet, and starting closed. Carries the brand header
 * (which opens the agent switcher), search, NEW CONVERSATION, the time-grouped
 * session list with live-run jewels, and the footer profile row.
 */
@Composable
fun SidebarDrawer(
    open: Boolean,
    brand: Brand,
    config: AppConfig,
    api: IgorApi,
    sessions: List<Session>,
    activeSessionId: Int?,
    userName: String,
    onSelectSession: (Int) -> Unit,
    onNewChat: () -> Unit,
    onClose: () -> Unit,
    onAgentChange: (String) -> Unit,
    onResetUplink: () -> Unit,
    onOpenSettings: () -> Unit,
    onOpenWarRoom: () -> Unit,
    onToggleComms: () -> Unit,
    onToggleBoard: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val palette = LocalHbPalette.current
    // The drawer sits OVER the transcript (outside its backdrop), so it refracts it.
    val haze = LocalHazeState.current
    var footerMenuOpen by remember { mutableStateOf(false) }

    // Which sessions still have a turn running server-side (light 8s poll).
    var running by remember { mutableStateOf<Set<Int>>(emptySet()) }
    LaunchedEffect(config, open) {
        if (!open) return@LaunchedEffect
        while (true) {
            running = api.fetchActiveRuns(config).map { it.sessionId }.toSet()
            delay(8_000)
        }
    }

    var searchOpen by remember { mutableStateOf(false) }
    var search by remember { mutableStateOf("") }
    var agentMenuOpen by remember { mutableStateOf(false) }

    val filtered = remember(sessions, search) {
        if (search.isBlank()) sessions
        else sessions.filter { (it.title ?: "").contains(search, ignoreCase = true) }
    }
    val groups = remember(filtered) { groupSessions(filtered) }

    BoxWithConstraints(modifier.fillMaxSize()) {
        val drawerWidth = minOf(maxWidth * 0.84f, 330.dp)
        val offsetX by animateDpAsState(
            targetValue = if (open) 0.dp else -(drawerWidth + 16.dp),
            animationSpec = tween(Motion.DRAWER_MS, easing = Motion.DrawerEasing),
            label = "drawerSlide",
        )
        val scrim by animateFloatAsState(
            targetValue = if (open) 0.55f else 0f,
            animationSpec = tween(Motion.DRAWER_MS, easing = Motion.DrawerEasing),
            label = "drawerScrim",
        )

        // Frosted backdrop sheet — tap to dismiss (only when actually open).
        if (scrim > 0.01f) {
            Box(
                Modifier
                    .fillMaxSize()
                    .background(Color.Black.copy(alpha = scrim))
                    .clickable(
                        interactionSource = remember { MutableInteractionSource() },
                        indication = null,
                        onClick = onClose,
                    ),
            )
        }

        Column(
            Modifier
                .offset(x = offsetX)
                .width(drawerWidth)
                .fillMaxHeight()
                // A frosted SHEET, not an opaque panel: rgba(8,14,20,0.55) over a
                // blur(28px) of the chat behind it, as the web has it. Without the
                // blur the only way to stop the transcript ghosting through was to
                // crank the fill to ~94% — which reads as a tinted panel, not glass.
                .then(
                    if (haze != null) {
                        Modifier.hazeEffect(state = haze) {
                            blurRadius = 28.dp
                            backgroundColor = palette.void
                            tints = listOf(HazeTint(DRAWER_FILL))
                            noiseFactor = 0f
                        }
                    } else {
                        Modifier.background(DRAWER_FILL_OPAQUE)
                    },
                )
                // AFTER the fill, so the glass still runs to the screen edge
                // while the footer profile row clears the system nav bar.
                .navigationBarsPadding()
                .clickable(
                    interactionSource = remember { MutableInteractionSource() },
                    indication = null,
                    onClick = {},
                ),
        ) {
            // ── Brand header (opens the agent switcher) ──────────────────────
            Box {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .height(40.dp)
                        .hbSeamBottom()
                        .padding(start = 12.dp, end = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Row(
                        Modifier.weight(1f).clickable { agentMenuOpen = !agentMenuOpen },
                        verticalAlignment = Alignment.Bottom,
                        horizontalArrangement = Arrangement.spacedBy(5.dp),
                    ) {
                        HbText(
                            brand.name,
                            style = HbType.headerBar.copy(fontSize = 17.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = 0.14.em),
                            color = Color.White,
                            caps = true,
                            maxLines = 1,
                        )
                        HbText(
                            brand.modelNumber,
                            style = HbType.headerBar.copy(fontSize = 12.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = 0.14.em),
                            color = palette.accent,
                            caps = true,
                            maxLines = 1,
                        )
                        Box(Modifier.padding(bottom = 3.dp)) { HbGlyphs.ChevronDown(palette.accentDim) }
                    }
                    Box(
                        Modifier.size(28.dp).clickable { searchOpen = !searchOpen; if (!searchOpen) search = "" },
                        contentAlignment = Alignment.Center,
                    ) { HbGlyphs.Search(if (searchOpen) palette.accent else palette.iconDim) }
                    Box(
                        Modifier.size(28.dp).clickable(onClick = onClose),
                        contentAlignment = Alignment.Center,
                    ) { HbGlyphs.Menu(palette.iconDim, size = 13.dp) }
                }

                if (agentMenuOpen) {
                    AgentDropdown(
                        current = brand.agentId,
                        onSelect = { agentMenuOpen = false; onAgentChange(it) },
                        onDismiss = { agentMenuOpen = false },
                    )
                }
            }

            // ── Search ───────────────────────────────────────────────────────
            if (searchOpen) {
                Row(
                    Modifier.fillMaxWidth().hbSeamBottom().padding(start = 12.dp, end = 12.dp, bottom = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    HbGlyphs.Search(palette.accent, size = 12.dp)
                    BasicTextField(
                        value = search,
                        onValueChange = { search = it },
                        singleLine = true,
                        textStyle = HbType.readout.copy(fontSize = 11.5.sp, letterSpacing = 0.1.em)
                            .merge(TextStyle(color = palette.text)),
                        cursorBrush = SolidColor(palette.accentBright),
                        modifier = Modifier.weight(1f),
                        decorationBox = { inner ->
                            if (search.isEmpty()) {
                                HbText("SEARCH SESSIONS", style = HbType.readout.copy(fontSize = 11.5.sp, letterSpacing = 0.1.em), color = palette.textFaint)
                            }
                            inner()
                        },
                    )
                }
            }

            // ── New conversation ─────────────────────────────────────────────
            Row(
                Modifier
                    .fillMaxWidth()
                    .hbSeamBottom()
                    .clickable { onNewChat(); onClose() }
                    .padding(horizontal = 12.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                HbGlyphs.Plus(palette.iconDim)
                HbText(
                    "New conversation",
                    style = HbType.headerBar.copy(fontSize = 12.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.14.em),
                    color = palette.iconBright,
                    caps = true,
                )
            }

            // ── Session list ─────────────────────────────────────────────────
            if (groups.isEmpty()) {
                Box(Modifier.fillMaxWidth().weight(1f).padding(top = 32.dp), contentAlignment = Alignment.TopCenter) {
                    HbText(
                        if (search.isNotBlank()) "// No results" else "// No sessions",
                        style = HbType.readout.copy(fontSize = 10.sp, letterSpacing = 0.12.em),
                        color = palette.iconDim,
                        caps = true,
                    )
                }
            } else {
                LazyColumn(Modifier.weight(1f).fillMaxWidth().padding(horizontal = 6.dp)) {
                    groups.forEach { group ->
                        item(key = "g:${group.label}") { GroupLabel(group.label) }
                        items(group.items, key = { it.id }) { s ->
                            SessionRow(
                                session = s,
                                active = s.id == activeSessionId,
                                running = s.id in running,
                                onClick = { onSelectSession(s.id); onClose() },
                            )
                        }
                    }
                }
            }

            // ── Footer profile row (tap → the profile menu) ──────────────────
            // Mobile-specific: the header can't carry WAR ROOM / COMMS / SYS and
            // a session title, so they live here — the slot the web gives Settings.
            if (footerMenuOpen) {
                Column(Modifier.fillMaxWidth().hbGlass(shape = HbGlassShape.R12, state = HbGlassState.Menu)) {
                    MenuItem("Settings", { footerMenuOpen = false; onOpenSettings() }) { HbGlyphs.Sliders(it, size = 13.dp) }
                    MenuItem("War room", { footerMenuOpen = false; onOpenWarRoom() }) { HbGlyphs.WarRoom(it, size = 13.dp) }
                    MenuItem("Comms", { footerMenuOpen = false; onToggleComms() }) { HbGlyphs.Comms(it, size = 13.dp) }
                    MenuItem("Systems board", { footerMenuOpen = false; onToggleBoard() }) { HbGlyphs.Sys(it, size = 13.dp) }
                    MenuItem("Reset uplink", { footerMenuOpen = false; onResetUplink() }) { HbGlyphs.Close(it, size = 13.dp) }
                }
            }
            Row(
                Modifier
                    .fillMaxWidth()
                    .hbSeamTop()
                    .clickable { footerMenuOpen = !footerMenuOpen }
                    .padding(start = 12.dp, end = 10.dp, top = 9.dp, bottom = 9.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(9.dp),
            ) {
                Box(
                    Modifier
                        .size(28.dp)
                        .hbGlass(shape = HbGlassShape.R9),
                    contentAlignment = Alignment.Center,
                ) {
                    HbText(
                        (userName.firstOrNull() ?: brand.avatarInitial.first()).uppercase(),
                        style = HbType.headerBar.copy(fontSize = 13.sp, fontWeight = FontWeight.Bold),
                        color = palette.accent,
                    )
                }
                Column(Modifier.weight(1f)) {
                    HbText(
                        userName.ifBlank { brand.userName },
                        style = HbType.read.copy(fontSize = 13.sp, fontWeight = FontWeight.Medium, lineHeight = 1.2.em),
                        color = palette.textDim,
                        maxLines = 1,
                    )
                    HbText(
                        brand.tagline,
                        style = HbType.read.copy(fontSize = 11.sp, lineHeight = 1.2.em),
                        color = palette.textFaint,
                        maxLines = 1,
                    )
                }
                HbGlyphs.ChevronUp(palette.iconDim)
            }
        }
    }
}

/** One row of the footer profile menu (the web's PopupItem). */
@Composable
private fun MenuItem(label: String, onClick: () -> Unit, glyph: @Composable (Color) -> Unit) {
    val palette = LocalHbPalette.current
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(9.dp),
    ) {
        glyph(palette.iconDim)
        HbText(
            label,
            style = HbType.headerBar.copy(fontSize = 12.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.12.em),
            color = palette.iconBright,
            caps = true,
        )
    }
}

/** `>>: TODAY ────` group heading. */
@Composable
private fun GroupLabel(label: String) {
    val palette = LocalHbPalette.current
    Row(
        Modifier.fillMaxWidth().padding(start = 8.dp, end = 8.dp, top = 10.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        HbText(">>:", style = HbType.readout.copy(fontSize = 10.sp), color = palette.accent.copy(alpha = 0.55f))
        HbText(
            label,
            style = HbType.headerBar.copy(fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.18.em),
            color = Color(0xFFA0C8D7).copy(alpha = 0.6f),
            caps = true,
        )
        Box(
            Modifier
                .weight(1f)
                .height(1.dp)
                .background(
                    Brush.horizontalGradient(
                        listOf(palette.accent.copy(alpha = 0.28f), palette.accent.copy(alpha = 0.03f)),
                    ),
                ),
        )
    }
}

/** A session row — flat and sharp; the selected row goes AMBER. */
@Composable
private fun SessionRow(session: Session, active: Boolean, running: Boolean, onClick: () -> Unit) {
    val palette = LocalHbPalette.current
    Box(
        Modifier
            .fillMaxWidth()
            .background(if (active) Color(0xFFD99C44).copy(alpha = 0.12f) else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 9.dp),
    ) {
        // Amber left rule on the active row.
        if (active) {
            Box(Modifier.width(2.dp).height(20.dp).background(palette.amber).align(Alignment.CenterStart))
        }
        HbText(
            session.title ?: "New conversation",
            style = HbType.read.copy(fontSize = 13.5.sp, fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal),
            color = if (active) Color(0xFFF3E2C4) else palette.textDim,
            maxLines = 1,
            modifier = Modifier.padding(start = if (active) 8.dp else 0.dp, end = 16.dp),
        )
        if (running) {
            Box(
                Modifier
                    .align(Alignment.CenterEnd)
                    .size(7.dp)
                    .clip(CircleShape)
                    .background(palette.accentBright),
            )
        }
    }
}

/** The agent switcher — colour dot, NAME + MARK, domain chip. Occluding glass. */
@Composable
private fun AgentDropdown(current: String, onSelect: (String) -> Unit, onDismiss: () -> Unit) {
    val palette = LocalHbPalette.current
    Column(
        Modifier
            .padding(top = 40.dp)
            .fillMaxWidth()
            .hbGlass(shape = HbGlassShape.R12, state = HbGlassState.Menu),
    ) {
        for ((id, b) in Brands.BRANDS) {
            val active = id == current
            val accent = ThemeEngine.parseHex(b.accent)
            Row(
                Modifier
                    .fillMaxWidth()
                    .background(if (active) palette.accent.copy(alpha = 0.12f) else Color.Transparent)
                    .clickable(enabled = !active) { onSelect(id) }
                    .padding(horizontal = 12.dp, vertical = 7.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(9.dp),
            ) {
                Box(Modifier.size(8.dp).clip(CircleShape).background(accent))
                Row(Modifier.weight(1f), verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    HbText(
                        b.name,
                        style = HbType.headerBar.copy(fontSize = 12.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.1.em),
                        color = if (active) Color.White else palette.textDim,
                        caps = true,
                        maxLines = 1,
                    )
                    HbText(
                        b.modelNumber,
                        style = HbType.headerBar.copy(fontSize = 10.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.08.em),
                        color = if (active) accent else palette.textFaint,
                        caps = true,
                        maxLines = 1,
                    )
                }
                HbText(
                    b.tagline.split(' ').take(2).joinToString(" "),
                    style = HbType.readout.copy(fontSize = 9.sp, letterSpacing = 0.04.em),
                    color = palette.textFaint,
                    maxLines = 1,
                )
            }
        }
    }
}
