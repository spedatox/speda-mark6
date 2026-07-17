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
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
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
        Spacer(Modifier.height(32.dp))

        // Agent hero — name + mark
        Row(verticalAlignment = Alignment.Bottom) {
            HbText(
                brand.name.uppercase(Locale.ENGLISH),
                style = HbType.headerBar.copy(
                    fontSize = 40.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = 0.3.em, lineHeight = 1.0.em,
                ),
                color = palette.accent,
            )
            Spacer(Modifier.size(10.dp))
            HbText(
                brand.modelNumber.uppercase(Locale.ENGLISH),
                style = HbType.headerBar.copy(
                    fontSize = 18.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.24.em, lineHeight = 1.0.em,
                ),
                color = palette.accentDim,
            )
        }
        Spacer(Modifier.height(10.dp))

        // Domain tagline
        HbText(
            brand.tagline.uppercase(Locale.ENGLISH),
            style = HbType.readout.copy(fontSize = 11.sp, letterSpacing = 0.22.em),
            color = palette.textFaint,
        )
        Spacer(Modifier.height(34.dp))

        // Greeting typewriter + blinking block caret
        Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.Center) {
            HbText(
                typed,
                style = HbType.headerBar.copy(
                    fontSize = 18.sp, fontWeight = FontWeight.Medium, letterSpacing = 0.22.em,
                ),
                color = androidx.compose.ui.graphics.Color(0xFFECF6F9),
            )
            if (!greetingDone) BlinkCaret()
        }

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

/** The greeting's blinking block caret (CSS `blink 0.8s step-end`). */
@Composable
private fun BlinkCaret() {
    val palette = LocalHbPalette.current
    val transition = rememberInfiniteTransition(label = "greetCaret")
    val alpha by transition.animateFloat(
        initialValue = 1f,
        targetValue = 0f,
        animationSpec = infiniteRepeatable(tween(800, easing = { if (it < 0.5f) 0f else 1f }), RepeatMode.Restart),
        label = "blink",
    )
    Box(
        Modifier
            .padding(start = 3.dp, bottom = 2.dp)
            .size(width = 9.dp, height = 17.dp)
            .background(palette.accentBright.copy(alpha = 0.55f * alpha)),
    )
}

private val CLOCK_FMT: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm:ss", Locale.ENGLISH)
private val DATE_FMT: DateTimeFormatter = DateTimeFormatter.ofPattern("EEEE, dd MMMM yyyy", Locale.ENGLISH)
