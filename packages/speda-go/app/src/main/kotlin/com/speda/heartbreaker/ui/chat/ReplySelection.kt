// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

package com.speda.heartbreaker.ui.chat

import com.speda.heartbreaker.ui.HbGlassButton

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.runtime.*
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.platform.ClipboardManager
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalTextToolbar
import androidx.compose.ui.platform.TextToolbar
import androidx.compose.ui.platform.TextToolbarStatus
import androidx.compose.ui.text.AnnotatedString

/** Selection's copy callback supplies the exact selected text, including spans
 * across paragraphs. Capturing a reply does not change the system clipboard. */
@Composable
fun ReplySelection(onSelect: (String) -> Unit, turkish: Boolean, content: @Composable () -> Unit) {
    val clipboard = LocalClipboardManager.current
    val select by rememberUpdatedState(onSelect)
    var capture by remember { mutableStateOf(false) }
    var copySelection by remember { mutableStateOf<(() -> Unit)?>(null) }
    val selectionClipboard = remember(clipboard) {
        object : ClipboardManager by clipboard {
            override fun setText(annotatedString: AnnotatedString) {
                if (capture) annotatedString.text.trim().takeIf { it.isNotEmpty() }?.let(select)
                else clipboard.setText(annotatedString)
            }
        }
    }
    val toolbar = remember {
        object : TextToolbar {
            override val status: TextToolbarStatus
                get() = if (copySelection == null) TextToolbarStatus.Hidden else TextToolbarStatus.Shown
            override fun hide() { copySelection = null }
            override fun showMenu(rect: Rect, onCopyRequested: (() -> Unit)?, onPasteRequested: (() -> Unit)?,
                onCutRequested: (() -> Unit)?, onSelectAllRequested: (() -> Unit)?) {
                copySelection = onCopyRequested
            }
        }
    }
    Column {
        CompositionLocalProvider(LocalClipboardManager provides selectionClipboard, LocalTextToolbar provides toolbar) {
            SelectionContainer { content() }
        }
        copySelection?.let { copy ->
            Column {
                HbGlassButton(if (turkish) "Kopyala" else "Copy", onClick = { copy(); toolbar.hide() })
                HbGlassButton(if (turkish) "Seçimi bağlam olarak ekle" else "Add selection context", onClick = {
                    capture = true
                    try { copy() } finally { capture = false; toolbar.hide() }
                })
            }
        }
    }
}
