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
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.speda.heartbreaker.designsystem.theme.HbPalette
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.domain.MathSpan
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  KaTeX — the same renderer the desktop client uses (rehype-katex), running
 *  offline from `assets/katex/`. A paragraph reaches this composable only when
 *  it actually contains math; every other paragraph stays on the native Compose
 *  path in Prose.kt, so the WebView cost is paid per formula-bearing paragraph
 *  and nowhere else.
 *
 *  The whole paragraph goes in, not just the formula: inline math has to sit in
 *  the text flow with a shared baseline, and nothing that lives outside the same
 *  layout box can do that. The CSS below therefore restates the `.prose` rules
 *  from Prose.kt — same Inter face (pulled straight out of res/font, not copied),
 *  same 15px/1.7, same strong-is-white / em-is-amber / code-is-a-cyan-chip.
 *
 *  The page is sealed: `blockNetworkLoads` is on and navigation is refused, so
 *  model-authored TeX can render but can never reach the network. The only
 *  bridge is a one-way height report.
 * ════════════════════════════════════════════════════════════════════════════
 */

private const val ASSET_BASE = "file:///android_asset/katex/"
private val TEX_JSON = Json { encodeDefaults = true }

@Composable
fun MathProse(bodyHtml: String, spans: List<MathSpan>, modifier: Modifier = Modifier) {
    val palette = LocalHbPalette.current
    val uriHandler = LocalUriHandler.current
    val doc = remember(bodyHtml, palette) { buildDocument(bodyHtml, spans, palette) }

    // KaTeX decides the height, and it can only do so after the fonts land — so
    // the page measures itself and reports back. Until then we hold one line of
    // space, which is the common case anyway (a sentence with inline math).
    //
    // Deliberately NOT keyed on `doc`: the WebView is created once and the bridge
    // closes over this state object, so re-keying would leave the page reporting
    // into an orphan and freeze the height. Keeping the last measurement through a
    // reload also stops the message jumping on every streamed token.
    var heightDp by remember { mutableFloatStateOf(0f) }

    AndroidView(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp) // .prose p { margin: 0.5rem 0 } — as in Prose.kt
            .let { if (heightDp > 0f) it.height(heightDp.dp) else it.heightIn(min = 26.dp) },
        factory = { ctx ->
            WebView(ctx).apply {
                configure { reported ->
                    Handler(Looper.getMainLooper()).post { heightDp = reported }
                }
                webViewClient = object : WebViewClient() {
                    // A link inside a math paragraph opens where every other link
                    // in the app opens — the platform handler, not this WebView.
                    override fun shouldOverrideUrlLoading(v: WebView, req: WebResourceRequest): Boolean {
                        runCatching { uriHandler.openUri(req.url.toString()) }
                        return true
                    }
                }
            }
        },
        update = { web ->
            // AndroidView#update runs on every recomposition; reloading the page
            // each time would restart the render and flicker mid-stream.
            if (web.tag != doc) {
                web.tag = doc
                web.loadDataWithBaseURL(ASSET_BASE, doc, "text/html", "utf-8", null)
            }
        },
    )
}

@SuppressLint("SetJavaScriptEnabled")
private fun WebView.configure(onHeight: (Float) -> Unit) {
    settings.javaScriptEnabled = true            // KaTeX is a JS renderer
    settings.blockNetworkLoads = true            // sealed: assets only, never the network
    settings.allowFileAccessFromFileURLs = false
    settings.allowUniversalAccessFromFileURLs = false
    settings.textZoom = 100                      // Compose already applies the system scale
    setBackgroundColor(AndroidColor.TRANSPARENT) // the glass panel behind shows through
    isVerticalScrollBarEnabled = false
    isHorizontalScrollBarEnabled = false
    overScrollMode = View.OVER_SCROLL_NEVER
    addJavascriptInterface(HeightBridge(onHeight), "HbMath")
}

/** The page's only channel back into the app: how tall it turned out to be. */
private class HeightBridge(private val onHeight: (Float) -> Unit) {
    @JavascriptInterface
    fun report(px: Float) = onHeight(px)
}

private fun buildDocument(bodyHtml: String, spans: List<MathSpan>, palette: HbPalette): String {
    val mounted = com.speda.heartbreaker.domain.MathExtract.toMountPoints(bodyHtml, spans)
    val tex = TEX_JSON.encodeToString(ListSerializer(String.serializer()), spans.map { it.tex })
    return """
<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="katex.min.css">
<style>
@font-face { font-family: Inter; src: url(file:///android_res/font/inter_variable.ttf); }
html, body { margin: 0; padding: 0; background: transparent; }
#c {
  font-family: Inter, sans-serif;
  font-size: 15px;
  line-height: 1.7;
  color: ${palette.text.css()};
  overflow-wrap: break-word;
}
#c p { margin: 0; }
strong { color: #fff; font-weight: 700; }
em { color: ${palette.amber.css()}; font-style: normal; }
code {
  background: ${palette.accent.css(0.10f)};
  color: ${palette.accentBright.css()};
  font-weight: 600;
  font-size: 13px;
}
a { color: ${palette.accentBright.css()}; }
/* A wide matrix scrolls inside its own box rather than stretching the message. */
.katex-display { margin: 0.6em 0; overflow-x: auto; overflow-y: hidden; }
.hb-math-error { color: ${palette.red.css()}; font-family: monospace; font-size: 13px; }
</style>
<script src="katex.min.js"></script>
</head><body>
<div id="c">$mounted</div>
<script>
(function () {
  var TEX = $tex;
  var host = document.getElementById('c');
  document.querySelectorAll('span.hb-math').forEach(function (el) {
    var src = TEX[+el.getAttribute('data-i')];
    if (src === undefined) return;
    try {
      katex.render(src, el, {
        displayMode: el.getAttribute('data-d') === '1',
        throwOnError: false,
        output: 'html'
      });
    } catch (e) {
      // Mid-stream TeX is routinely incomplete; show the source, never a crash.
      el.className = 'hb-math-error';
      el.textContent = src;
    }
  });
  var last = -1;
  function report() {
    var h = host.getBoundingClientRect().height;
    if (Math.abs(h - last) < 0.5) return;
    last = h;
    HbMath.report(h);
  }
  report();
  window.addEventListener('load', report);
  // The KaTeX faces load async — height is wrong until they land.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(report);
  if (window.ResizeObserver) new ResizeObserver(report).observe(host);
})();
</script>
</body></html>
""".trimIndent()
}

/** Compose colour → a CSS `rgba()`, optionally overriding alpha. */
private fun Color.css(alpha: Float = -1f): String {
    val argb = toArgb()
    val a = if (alpha >= 0f) alpha else ((argb ushr 24) and 0xFF) / 255f
    return "rgba(${(argb shr 16) and 0xFF}, ${(argb shr 8) and 0xFF}, ${argb and 0xFF}, $a)"
}
