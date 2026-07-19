package com.speda.heartbreaker.ui.prose

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.speda.heartbreaker.designsystem.theme.HbPalette
import com.speda.heartbreaker.designsystem.theme.LocalHbPalette
import com.speda.heartbreaker.designsystem.type.HbFonts
import com.speda.heartbreaker.designsystem.type.HbType
import org.commonmark.ext.autolink.AutolinkExtension
import org.commonmark.ext.gfm.strikethrough.Strikethrough
import org.commonmark.ext.gfm.strikethrough.StrikethroughExtension
import org.commonmark.ext.gfm.tables.TableBlock
import org.commonmark.ext.gfm.tables.TableCell
import org.commonmark.ext.gfm.tables.TableRow
import org.commonmark.ext.gfm.tables.TablesExtension
import org.commonmark.node.BlockQuote
import org.commonmark.node.BulletList
import org.commonmark.node.Code
import org.commonmark.node.Emphasis
import org.commonmark.node.FencedCodeBlock
import org.commonmark.node.HardLineBreak
import org.commonmark.node.Heading
import org.commonmark.node.IndentedCodeBlock
import org.commonmark.node.Link
import org.commonmark.node.ListItem
import org.commonmark.node.Node
import org.commonmark.node.OrderedList
import org.commonmark.node.Paragraph
import org.commonmark.node.SoftLineBreak
import org.commonmark.node.StrongEmphasis
import org.commonmark.node.ThematicBreak
import org.commonmark.parser.Parser
import org.commonmark.node.Text as MdText

/**
 * ════════════════════════════════════════════════════════════════════════════
 *  The Stark prose renderer — commonmark AST → Compose, styled from the `.prose`
 *  rules in heartbreaker.css. Owning the rendering is deliberate (plan §2): the
 *  heading plates, the MAIN_SUB split, the ▸ markers and the fence interception
 *  can't be expressed through an off-the-shelf markdown renderer.
 * ════════════════════════════════════════════════════════════════════════════
 */

private val PARSER: Parser = Parser.builder()
    .extensions(
        listOf(
            TablesExtension.create(),
            StrikethroughExtension.create(),
            AutolinkExtension.create(),
        ),
    )
    .build()

/** `.prose` — Inter 0.95rem, line-height 1.7, no tracking. */
private val ProseBase = TextStyle(
    fontFamily = HbFonts.Read,
    fontSize = 15.sp,
    lineHeight = 1.7.em,
    letterSpacing = 0.em,
)

@Composable
fun ProseText(markdown: String, modifier: Modifier = Modifier) {
    val palette = LocalHbPalette.current
    val doc = remember(markdown) { PARSER.parse(markdown) }
    Column(modifier.fillMaxWidth()) { Blocks(doc, palette) }
}

@Composable
private fun Blocks(parent: Node, palette: HbPalette) {
    var child = parent.firstChild
    while (child != null) {
        Block(child, palette)
        child = child.next
    }
}

@Composable
private fun Block(node: Node, palette: HbPalette) {
    when (node) {
        is Heading -> HeadingPlate(node, palette)
        is Paragraph -> BasicText(
            text = inlines(node, palette),
            style = ProseBase.merge(TextStyle(color = palette.text)),
            modifier = Modifier.padding(vertical = 8.dp), // .prose p { margin: 0.5rem 0 }
        )
        is BulletList -> ListBlock(node, palette, ordered = false)
        is OrderedList -> ListBlock(node, palette, ordered = true)
        is BlockQuote -> Quote(node, palette)
        is FencedCodeBlock -> Fence(language = node.info.orEmpty().trim(), code = node.literal.trimEnd('\n'))
        is IndentedCodeBlock -> CodeBlockView(language = "", code = node.literal.trimEnd('\n'))
        is ThematicBreak -> Hr()
        is TableBlock -> TableView(node, palette)
        else -> Blocks(node, palette) // containers we don't style directly
    }
}

/* ── Fence dispatch ──────────────────────────────────────────────────────── */

/**
 * Which renderer a ``` fence gets, mirroring the `code()` branch in Message.tsx.
 * Anything unclaimed falls through to the glass code block.
 */
@Composable
private fun Fence(language: String, code: String) {
    when (language.lowercase()) {
        "chart" -> ChartBlock(code)
        "calendar" -> CalendarBlock(code)
        "map" -> MapBlock(code)
        "svg" -> SvgBlock(code)
        else -> CodeBlockView(language = language, code = code)
    }
}

/* ── Headings — frosted accent plates with a left rule ───────────────────── */

private data class HeadSpec(
    val minHeight: Dp,
    val radius: Dp,
    val bgAlpha: Float,
    val ruleWidth: Dp,
    val ruleAlpha: Float,
    val fontSize: androidx.compose.ui.unit.TextUnit,
    val weight: FontWeight,
    val tracking: androidx.compose.ui.unit.TextUnit,
    val color: Color,
    val top: Dp,
    val bottom: Dp,
    val subRatio: Float,
)

@Composable
private fun HeadingPlate(node: Heading, palette: HbPalette) {
    val s = when (node.level) {
        1 -> HeadSpec(32.dp, 10.dp, 0.12f, 2.dp, 1f, 24.sp, FontWeight.ExtraBold, 0.14.em, Color.White, 26.dp, 10.dp, 0.90f)
        2 -> HeadSpec(30.dp, 9.dp, 0.10f, 3.dp, 1f, 21.sp, FontWeight.ExtraBold, 0.04.em, Color.White, 24.dp, 8.dp, 0.88f)
        3 -> HeadSpec(26.dp, 8.dp, 0.07f, 2.dp, 0.6f, 18.sp, FontWeight.ExtraBold, 0.04.em, Color(0xFFDCEDF3), 19.dp, 6.dp, 0.88f)
        else -> HeadSpec(24.dp, 7.dp, 0.05f, 2.dp, 0.4f, 16.sp, FontWeight.Bold, 0.04.em, Color(0xFF9BBAC5), 16.dp, 5.dp, 0.88f)
    }

    // "MAIN_SUB" → main in white, _SUB in the accent. Only when the heading is a
    // single plain string; headings with nested bold/links pass through unchanged.
    val only = node.firstChild
    val plain = if (only is MdText && only.next == null) only.literal else null
    val text = if (plain != null && plain.indexOf('_') > -1) {
        val i = plain.indexOf('_')
        buildAnnotatedString {
            withStyle(SpanStyle(color = s.color)) { append(plain.substring(0, i)) }
            withStyle(SpanStyle(color = palette.accent, fontSize = s.fontSize * s.subRatio)) {
                append("_" + plain.substring(i + 1))
            }
        }
    } else {
        inlines(node, palette)
    }

    Box(
        Modifier
            .fillMaxWidth()
            .padding(top = s.top, bottom = s.bottom)
            .clip(RoundedCornerShape(s.radius))
            .background(palette.accent.copy(alpha = s.bgAlpha))
            .drawBehind {
                // border-left: Npx solid accent
                drawRect(
                    color = palette.accent.copy(alpha = s.ruleAlpha),
                    topLeft = Offset.Zero,
                    size = Size(s.ruleWidth.toPx(), size.height),
                )
            }
            .heightIn(min = s.minHeight)
            .padding(start = 11.dp, end = 12.dp, top = 4.dp, bottom = 4.dp),
        contentAlignment = Alignment.CenterStart,
    ) {
        BasicText(
            text = text,
            style = TextStyle(
                fontFamily = HbFonts.Ui,
                fontSize = s.fontSize,
                fontWeight = s.weight,
                letterSpacing = s.tracking,
                color = s.color,
            ),
        )
    }
}

/* ── Lists — angular teal markers ────────────────────────────────────────── */

@Composable
private fun ListBlock(node: Node, palette: HbPalette, ordered: Boolean, depth: Int = 0) {
    Column(Modifier.padding(vertical = 8.dp)) {
        var item = node.firstChild
        var index = if (node is OrderedList) node.markerStartNumber ?: 1 else 1
        while (item != null) {
            if (item is ListItem) {
                Row(Modifier.padding(vertical = 3.dp)) {
                    Box(Modifier.width(21.dp), contentAlignment = Alignment.TopEnd) {
                        if (ordered) {
                            BasicText(
                                text = AnnotatedString("$index."),
                                style = ProseBase.merge(
                                    TextStyle(color = palette.accent, fontSize = 12.sp, fontFamily = HbFonts.Read),
                                ),
                                modifier = Modifier.padding(end = 6.dp),
                            )
                        } else {
                            // ▸ at depth 0; nested lists dim to ·
                            BasicText(
                                text = AnnotatedString(if (depth == 0) "▸" else "·"),
                                style = ProseBase.merge(
                                    TextStyle(
                                        color = if (depth == 0) palette.accent else palette.accentDim,
                                        fontSize = if (depth == 0) 11.sp else 15.sp,
                                    ),
                                ),
                                modifier = Modifier.padding(end = 6.dp),
                            )
                        }
                    }
                    Column(Modifier.weight(1f)) { ListItemBody(item, palette, depth) }
                }
            }
            item = item.next
        }
    }
}

@Composable
private fun ListItemBody(item: ListItem, palette: HbPalette, depth: Int) {
    var child = item.firstChild
    while (child != null) {
        when (child) {
            // A list item's paragraph carries no extra vertical margin.
            is Paragraph -> BasicText(
                text = inlines(child, palette),
                style = ProseBase.merge(TextStyle(color = palette.text)),
            )
            is BulletList -> ListBlock(child, palette, ordered = false, depth = depth + 1)
            is OrderedList -> ListBlock(child, palette, ordered = true, depth = depth + 1)
            else -> Block(child, palette)
        }
        child = child.next
    }
}

/* ── Blockquote — panel inset with a gradient top hairline ───────────────── */

@Composable
private fun Quote(node: BlockQuote, palette: HbPalette) {
    Column(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 12.dp)
            .background(palette.accentDim.copy(alpha = 0.45f))
            .drawBehind {
                // border-left: 2px solid cyan-dim
                drawRect(palette.accentDim, Offset.Zero, Size(2.dp.toPx(), size.height))
                // ::before — a hairline that fades out by 70%
                drawRect(
                    brush = Brush.horizontalGradient(
                        0f to palette.lineBright,
                        0.7f to Color.Transparent,
                    ),
                    topLeft = Offset.Zero,
                    size = Size(size.width, 1.dp.toPx()),
                )
            }
            .padding(start = 14.dp, end = 12.dp, top = 6.dp, bottom = 6.dp),
    ) {
        var child = node.firstChild
        while (child != null) {
            if (child is Paragraph) {
                BasicText(inlines(child, palette), style = ProseBase.merge(TextStyle(color = palette.textDim)))
            } else {
                Block(child, palette)
            }
            child = child.next
        }
    }
}

/* ── HR — the etched glass seam ──────────────────────────────────────────── */

@Composable
private fun Hr() {
    Box(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 20.dp)
            .height(2.dp)
            .drawBehind {
                val fade = { c: Color ->
                    Brush.horizontalGradient(
                        0f to Color.Transparent,
                        0.12f to c,
                        0.88f to c,
                        1f to Color.Transparent,
                    )
                }
                drawRect(fade(Color.White.copy(alpha = 0.16f)), Offset.Zero, Size(size.width, 1.dp.toPx()))
                drawRect(
                    fade(Color.Black.copy(alpha = 0.35f)),
                    Offset(0f, 1.dp.toPx()),
                    Size(size.width, 1.dp.toPx()),
                )
            },
    )
}

/* ── Table — data grid ───────────────────────────────────────────────────── */

@Composable
private fun TableView(node: TableBlock, palette: HbPalette) {
    // Wide tables scroll inside their own container rather than stretching the row.
    Column(
        Modifier
            .padding(vertical = 12.dp)
            .horizontalScroll(rememberScrollState())
            .border(1.dp, palette.line),
    ) {
        var section = node.firstChild
        var rowIndex = 0
        while (section != null) {
            var row = section.firstChild
            while (row != null) {
                if (row is TableRow) {
                    val header = rowIndexIsHeader(row)
                    Row(Modifier.background(if (!header && rowIndex % 2 == 0) palette.accent.copy(alpha = 0.04f) else Color.Transparent)) {
                        var cell = row.firstChild
                        while (cell != null) {
                            if (cell is TableCell) TableCellView(cell, palette, header)
                            cell = cell.next
                        }
                    }
                    if (!header) rowIndex++
                }
                row = row.next
            }
            section = section.next
        }
    }
}

private fun rowIndexIsHeader(row: TableRow): Boolean =
    (row.firstChild as? TableCell)?.isHeader == true

@Composable
private fun TableCellView(cell: TableCell, palette: HbPalette, header: Boolean) {
    Box(
        Modifier
            .width(150.dp)
            .background(if (header) palette.accent.copy(alpha = 0.12f) else Color.Transparent)
            .border(1.dp, if (header) palette.line else palette.accent.copy(alpha = 0.14f))
            .padding(horizontal = 10.dp, vertical = 6.dp),
    ) {
        BasicText(
            text = inlines(cell, palette),
            style = if (header) {
                HbType.headerBar.copy(fontSize = 10.sp, letterSpacing = 0.14.em, color = Color(0xFFCFE7EE))
            } else {
                ProseBase.merge(TextStyle(color = palette.textDim, fontSize = 13.sp))
            },
        )
    }
}

/* ── Inlines ─────────────────────────────────────────────────────────────── */

private fun inlines(parent: Node, palette: HbPalette): AnnotatedString =
    buildAnnotatedString { appendInlines(parent, palette) }

private fun AnnotatedString.Builder.appendInlines(parent: Node, palette: HbPalette) {
    var child = parent.firstChild
    while (child != null) {
        when (val n = child) {
            is MdText -> append(n.literal)
            // strong → white
            is StrongEmphasis -> withStyle(SpanStyle(color = Color.White, fontWeight = FontWeight.Bold)) {
                appendInlines(n, palette)
            }
            // em → amber, and NOT italic (the CSS kills font-style)
            is Emphasis -> withStyle(SpanStyle(color = palette.amber, fontStyle = FontStyle.Normal)) {
                appendInlines(n, palette)
            }
            // inline code → cyan text on an accent-tinted chip
            is Code -> withStyle(
                SpanStyle(
                    background = palette.accent.copy(alpha = 0.10f),
                    color = palette.accentBright,
                    fontWeight = FontWeight.SemiBold,
                    fontSize = 13.sp,
                ),
            ) { append(n.literal) }
            is Link -> {
                // A real, tappable link: LinkAnnotation.Url is opened by the
                // platform UriHandler when the span is clicked (BasicText handles
                // the hit-testing). Autolinked bare URLs land here too.
                val href = n.destination.orEmpty()
                val linkStyle = SpanStyle(color = palette.accentBright, textDecoration = TextDecoration.Underline)
                if (href.isNotBlank()) {
                    withLink(LinkAnnotation.Url(href, TextLinkStyles(style = linkStyle))) {
                        appendInlines(n, palette)
                    }
                } else {
                    withStyle(linkStyle) { appendInlines(n, palette) }
                }
            }
            is Strikethrough -> withStyle(SpanStyle(textDecoration = TextDecoration.LineThrough)) {
                appendInlines(n, palette)
            }
            is SoftLineBreak -> append(' ')
            is HardLineBreak -> append('\n')
            else -> appendInlines(n, palette)
        }
        child = child.next
    }
}
