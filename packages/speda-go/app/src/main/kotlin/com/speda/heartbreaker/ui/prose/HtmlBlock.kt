package com.speda.heartbreaker.ui.prose

import android.annotation.SuppressLint
import android.graphics.Color as AndroidColor
import android.os.Handler
import android.os.Looper
import android.view.View
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.speda.heartbreaker.designsystem.theme.HbPalette
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette

/**
 * ```html fences — the Android counterpart to WidgetFrame.tsx.
 *
 * The model writes a live widget (a gauge, a comparison table, a small
 * interactive readout) and the desktop hands it to a sandboxed iframe with the
 * app's base styles and a resize script injected. Same contract here, with a
 * WebView standing in for the iframe: the page reports its own height back over
 * a one-way bridge and the composable takes that height, so a widget is never
 * boxed into a guess.
 *
 * `svg` keeps its own native path (SvgBlock, AndroidSVG) — vectors render
 * crisper and cheaper without a browser. This is only for real markup. An
 * `html` fence that turns out to *be* an SVG is handed straight back to it.
 *
 * Sealed exactly like the math renderer: JS on (widgets are scripted), network
 * loads blocked, navigation refused and re-routed to the platform handler. The
 * markup is model-authored, so it renders but can never phone anywhere.
 */

private val WIDGET_JSON_BASE = "file:///android_asset/"

@Composable
fun HtmlBlock(raw: String, modifier: Modifier = Modifier) {
    // An `html` fence whose body is a vector belongs on the native path — the
    // desktop makes the same redirect (`isSvg` in WidgetFrame.tsx).
    if (raw.trimStart().startsWith("<svg", ignoreCase = true)) {
        SvgBlock(raw, modifier)
        return
    }

    val palette = LocalHbPalette.current
    val uriHandler = LocalUriHandler.current

    // Only COMMIT complete markup. While the fence streams, the body grows every
    // chunk; loading each partial document is what makes a widget blink and
    // redraw itself half-built. Hold the last complete version and show that.
    val complete = remember(raw) { looksComplete(raw) }
    var stable by remember { mutableStateOf(if (complete) raw else "") }
    if (complete && stable != raw) stable = raw

    if (stable.isBlank()) {
        Materializing("WIDGET", modifier)
        return
    }

    val doc = remember(stable, palette) { buildWidgetDocument(stable, palette) }
    var heightDp by remember { mutableFloatStateOf(0f) }

    Box(
        modifier
            .fillMaxWidth()
            .padding(vertical = 12.dp)
            .clip(RoundedCornerShape(12.dp))
            .border(1.dp, palette.edge, RoundedCornerShape(12.dp)),
    ) {
        AndroidView(
            modifier = Modifier
                .fillMaxWidth()
                // Reported height wins once it lands; until then hold a sane
                // slab rather than collapsing to nothing and jumping.
                .let { if (heightDp > 0f) it.height(heightDp.dp) else it.heightIn(min = 180.dp) },
            factory = { ctx ->
                WebView(ctx).apply {
                    configureWidget { reported ->
                        Handler(Looper.getMainLooper()).post { heightDp = reported }
                    }
                    webViewClient = object : WebViewClient() {
                        override fun shouldOverrideUrlLoading(v: WebView, req: WebResourceRequest): Boolean {
                            runCatching { uriHandler.openUri(req.url.toString()) }
                            return true
                        }
                    }
                }
            },
            update = { web ->
                // AndroidView#update fires on every recomposition; reloading each
                // time would restart the widget's own scripts mid-stream.
                if (web.tag != doc) {
                    web.tag = doc
                    web.loadDataWithBaseURL(WIDGET_JSON_BASE, doc, "text/html", "utf-8", null)
                }
            },
        )
    }
}

/**
 * Has the markup reached a point where loading it is not going to show half a
 * widget? Ported from WidgetFrame.tsx's `isComplete`: a full document needs its
 * closing tag, a bare fragment has no terminator to wait for.
 */
private fun looksComplete(raw: String): Boolean {
    val t = raw.trim()
    if (t.isEmpty()) return false
    val isDoc = t.startsWith("<!doctype", ignoreCase = true) || t.startsWith("<html", ignoreCase = true)
    if (isDoc) return t.contains("</html>", ignoreCase = true)
    return true
}

@SuppressLint("SetJavaScriptEnabled")
private fun WebView.configureWidget(onHeight: (Float) -> Unit) {
    settings.javaScriptEnabled = true             // widgets are scripted by design
    settings.blockNetworkLoads = true             // sealed: never reaches the network
    settings.allowFileAccessFromFileURLs = false
    settings.allowUniversalAccessFromFileURLs = false
    settings.textZoom = 100                       // Compose already applies the system scale
    setBackgroundColor(AndroidColor.TRANSPARENT)  // the glass slab behind shows through
    isVerticalScrollBarEnabled = false
    overScrollMode = View.OVER_SCROLL_NEVER
    addJavascriptInterface(WidgetHeightBridge(onHeight), "HbWidget")
}

/** The page's only channel back into the app: how tall it turned out to be. */
private class WidgetHeightBridge(private val onHeight: (Float) -> Unit) {
    @JavascriptInterface
    fun report(px: Float) = onHeight(px)
}

/**
 * The injected shell — the Android half of WidgetFrame.tsx's BASE_STYLES +
 * RESIZE_SCRIPT. The custom properties are handed the LIVE palette rather than
 * fixed hexes, so a widget re-hues with the agent exactly like every other
 * surface: model-authored markup that says `var(--accent)` is Centurion red
 * under Centurion.
 */
private fun buildWidgetDocument(body: String, palette: HbPalette): String {
    fun css(c: androidx.compose.ui.graphics.Color): String =
        "rgba(${(c.red * 255).toInt()},${(c.green * 255).toInt()},${(c.blue * 255).toInt()},${c.alpha})"

    val trimmed = body.trim()
    val isFullDoc = trimmed.startsWith("<!doctype", ignoreCase = true) ||
        trimmed.startsWith("<html", ignoreCase = true)

    val head = """
<style>
  *, *::before, *::after { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: transparent; }
  body {
    font-family: -apple-system, 'Segoe UI', Roboto, sans-serif;
    font-size: 14px; line-height: 1.6; color: ${css(palette.text)};
    -webkit-font-smoothing: antialiased;
  }
  :root {
    --bg-primary: ${css(palette.base)}; --bg-sidebar: ${css(palette.petrol)};
    --text-primary: ${css(palette.text)}; --text-secondary: ${css(palette.textDim)};
    --text-muted: ${css(palette.textFaint)};
    --border: ${css(palette.edge)}; --border-focus: ${css(palette.edgeBright)};
    --accent: ${css(palette.accent)}; --accent-hover: ${css(palette.accentBright)};
  }
</style>"""

    val script = """
<script>
  (function () {
    function measure() {
      var b = document.body, d = document.documentElement;
      return Math.max(b ? b.scrollHeight : 0, b ? b.offsetHeight : 0,
                      d ? d.scrollHeight : 0, d ? d.offsetHeight : 0);
    }
    var last = 0;
    function post() {
      var h = measure();
      if (h && h !== last) { last = h; try { HbWidget.report(h); } catch (e) {} }
    }
    window.addEventListener('load', post);
    window.addEventListener('resize', post);
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(post);
      ro.observe(document.documentElement);
      if (document.body) ro.observe(document.body);
    }
    // Charts and images render after load; catch them without polling forever.
    [50, 150, 300, 600, 1000, 1600, 2400].forEach(function (t) { setTimeout(post, t); });
  })();
</script>"""

    if (isFullDoc) {
        var doc = trimmed
        doc = when {
            doc.contains("<head>", ignoreCase = true) -> doc.replaceFirst(Regex("(?i)<head>"), "<head>$head")
            doc.contains("<body", ignoreCase = true) ->
                doc.replaceFirst(Regex("(?i)<body"), "<head>$head</head><body")
            else -> head + doc
        }
        return when {
            doc.contains("</body>", ignoreCase = true) -> doc.replaceFirst(Regex("(?i)</body>"), "$script</body>")
            doc.contains("</html>", ignoreCase = true) -> doc.replaceFirst(Regex("(?i)</html>"), "$script</html>")
            else -> doc + script
        }
    }
    return "<!doctype html><html><head><meta charset=\"utf-8\">" +
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
        "$head</head><body>$trimmed$script</body></html>"
}
