package com.speda.heartbreaker.ui.shell

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.data.IgorApi
import com.speda.heartbreaker.designsystem.brand.Brand
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbFonts
import com.speda.heartbreaker.designsystem.type.HbType
import com.speda.heartbreaker.domain.AppConfig
import com.speda.heartbreaker.ui.HbText
import kotlinx.coroutines.delay
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * The home screen — a port of WelcomeView in ChatMain.tsx. Live clock, caps date
 * line, the agent hero (name + mark), the domain tagline, the typewritten
 * greeting (42ms/char, blinking block caret), and the async JARVIS remark from
 * /welcome/{agent} typed at 26ms/char.
 *
 * The war-room profile speaks protocol, not pleasantries: the greeting becomes
 * "ALL HANDS ON DECK" and the remark is suppressed.
 */
@Composable
fun WelcomeView(
    brand: Brand,
    config: AppConfig,
    api: IgorApi,
    userName: String,
    modifier: Modifier = Modifier,
) {
    val palette = LocalHbPalette.current
    val isWarroom = brand.agentId == "warroom"
    val displayName = userName.trim().ifEmpty { brand.userName }

    // ── Live clock (1s) ──────────────────────────────────────────────────────
    var now by remember { mutableStateOf(LocalDateTime.now()) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(1000)
            now = LocalDateTime.now()
        }
    }
    val clock = remember(now.hour, now.minute, now.second) { now.format(CLOCK_FMT) }
    val dateLine = remember(now.dayOfYear) { now.format(DATE_FMT).uppercase(Locale.ENGLISH) }

    // ── Greeting typewriter (42ms/char) ──────────────────────────────────────
    val fullGreeting = remember(displayName, isWarroom, now.hour) {
        val salutation = when {
            now.hour < 12 -> "Good morning"
            now.hour < 18 -> "Good afternoon"
            else -> "Good evening"
        }
        val text = if (isWarroom) {
            if (displayName.isNotEmpty()) "All hands on deck, $displayName" else "All hands on deck"
        } else {
            if (displayName.isNotEmpty()) "$salutation, $displayName" else salutation
        }
        text.uppercase(Locale.ENGLISH)
    }
    var typed by remember { mutableStateOf("") }
    var greetingDone by remember { mutableStateOf(false) }
    LaunchedEffect(fullGreeting) {
        typed = ""; greetingDone = false
        for (i in 1..fullGreeting.length) {
            delay(42)
            typed = fullGreeting.substring(0, i)
        }
        greetingDone = true
    }

    // ── JARVIS remark — async, typed at 26ms/char ────────────────────────────
    var remark by remember { mutableStateOf("") }
    LaunchedEffect(config, brand.agentId, isWarroom) {
        remark = ""
        if (!isWarroom) remark = api.fetchWelcome(config, brand.agentId)
    }
    var remarkTyped by remember { mutableStateOf("") }
    LaunchedEffect(remark) {
        remarkTyped = ""
        for (i in 1..remark.length) {
            delay(26)
            remarkTyped = remark.substring(0, i)
        }
    }

    Column(
        modifier = modifier.fillMaxSize().padding(horizontal = 24.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        // Clock + date
        HbText(clock, style = HbType.numThin.copy(fontSize = 46.sp), color = palette.text)
        Spacer(Modifier.height(4.dp))
        HbText(
            dateLine,
            style = HbType.label.copy(fontSize = 9.5.sp, letterSpacing = 0.18.em),
            color = palette.textFaint,
        )
        Spacer(Modifier.height(18.dp))

        // Agent hero — the brand, as large as the screen allows.
        AgentHero(brand)
        Spacer(Modifier.height(8.dp))

        // Domain tagline — centred, since a long one (Centurion's) wraps.
        HbText(
            brand.tagline.uppercase(Locale.ENGLISH),
            style = HbType.readout.copy(
                fontSize = 11.sp, letterSpacing = 0.22.em, lineHeight = 1.5.em, textAlign = TextAlign.Center,
            ),
            color = palette.textFaint,
        )
        Spacer(Modifier.height(20.dp))

        // Greeting typewriter. The caret is an inline glyph rather than a Box in a
        // Row: a Row can only centre the block, so a wrapped greeting ("GOOD
        // AFTERNOON, AHMET EROL") left-aligned its second line. Inline, it flows
        // with the text and every line centres.
        val caretOn by produceState(true, greetingDone) {
            while (!greetingDone) { delay(400); value = !value }
            value = false
        }
        BasicText(
            text = buildAnnotatedString {
                append(typed)
                if (!greetingDone) {
                    withStyle(SpanStyle(color = palette.accentBright.copy(alpha = if (caretOn) 0.55f else 0f))) {
                        append("▌")
                    }
                }
            },
            style = HbType.headerBar.copy(
                fontSize = 18.sp,
                fontWeight = FontWeight.Medium,
                letterSpacing = 0.22.em,
                lineHeight = 1.45.em,
                textAlign = TextAlign.Center,
                color = Color(0xFFECF6F9),
            ),
            modifier = Modifier.fillMaxWidth(),
        )

        // JARVIS remark — reserves no space until it exists, so nothing jumps
        if (remarkTyped.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            HbText(
                remarkTyped,
                style = HbType.headerBar.copy(
                    fontSize = 13.sp, fontWeight = FontWeight.Normal, letterSpacing = 0.08.em, lineHeight = 1.5.em,
                    textAlign = TextAlign.Center,
                ),
                color = palette.textDim,
                modifier = Modifier.widthIn(max = 420.dp),
            )
        }
    }
}

/**
 * The agent hero — "SPEDA MARK VI".
 *
 * Mobile-specific: the brand is the loudest thing on the home screen, so it is
 * sized to fill the width (shrink-to-fit, since a long name like NIGHTCRAWLER
 * MARK III needs far less than SPEDA MARK VI).
 *
 * Name and mark are ONE line of styled text rather than two composables in a Row:
 * that gives them a shared baseline for free — Row's Alignment.Bottom lines up the
 * boxes, not the baselines, which is what made the mark sit low.
 */
@Composable
private fun AgentHero(brand: Brand) {
    val palette = LocalHbPalette.current
    // Reset per agent — name lengths differ, so the fitted size does too.
    var heroSize by remember(brand.agentId) { mutableStateOf(HERO_START) }

    val text = buildAnnotatedString {
        withStyle(
            SpanStyle(
                color = palette.accent,
                fontSize = heroSize,
                fontWeight = FontWeight.ExtraBold,
                letterSpacing = 0.3.em,
            ),
        ) { append(brand.name.uppercase(Locale.ENGLISH)) }
        append("  ")
        withStyle(
            SpanStyle(
                color = palette.accentDim,
                fontSize = heroSize * MARK_RATIO,
                fontWeight = FontWeight.Bold,
                letterSpacing = 0.24.em,
            ),
        ) { append(brand.modelNumber.uppercase(Locale.ENGLISH)) }
    }

    BasicText(
        text = text,
        style = TextStyle(fontFamily = HbFonts.Ui, lineHeight = 1.0.em),
        maxLines = 1,
        softWrap = false,
        onTextLayout = { result ->
            if (result.didOverflowWidth && heroSize > HERO_MIN) heroSize *= 0.94f
        },
    )
}

/** Start big and shrink to fit; the web's clamp() tops out around here anyway. */
private val HERO_START = 58.sp
private val HERO_MIN = 18.sp
/** The web's mark is half the name (clamp 1.2rem vs 2.4rem at the mobile floor). */
private const val MARK_RATIO = 0.5f

private val CLOCK_FMT: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm:ss", Locale.ENGLISH)
private val DATE_FMT: DateTimeFormatter = DateTimeFormatter.ofPattern("EEEE, dd MMMM yyyy", Locale.ENGLISH)
