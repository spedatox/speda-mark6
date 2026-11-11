# Inline renderer preview

From the renovated checkout, run:

```sh
npx vite --config packages/heartbreaker/vite.renderharness.config.ts
```

Open http://localhost:5375/. This uses the real message renderer and widget host,
without a backend connection or API key. The dev nonce permits Vite's refresh
bootstrap; authored widget scripts still run only through the isolated host.

Fixtures cover legacy text tables/timelines, titled fences, long calendar names,
agenda/week switching, independent calculators, SVG, actual source code, and
unfinished HTML. A slider value of 60 or more reveals more content to exercise
automatic frame height. Reset restores only that widget. The partial-widget
control must show a placeholder and no iframe for the unfinished script.

Regression tests: `node --test packages/heartbreaker/tests/*.test.cjs`.

Load `/#mentions` for the real agent mention editor without a backend. Verify
typing `@` opens the logo/name list, filtering and arrow/Enter/Tab selection,
Escape dismissal, inline bold/logo chips, atomic deletion and undo, plain-text
copy/paste, Shift+Enter, and the exact transport text after sending. The room
toggle limits suggestions to current members; the dictation fixture exercises
external draft updates. Check both 320px and 780px widths.

Choose **Canvas preview** (or load `/#canvas`) to test the same content in the
real VoiceCanvas. The board must continue below the viewport without shrinking
windows. Scroll down/up with the toolbar, resize from a panel's corner, or expand
one panel. Restore returns to the previous board position. A calculator's value
must survive expansion, restoration, and board scrolling. Narration opens at the
start; labels and course names must wrap at words. Test at 320, 1440 and 1920px.

The canvas includes the real WebGL orb. Drag it and scroll over it to scale.
The saved-gesture counter should increment once per drag or settled wheel burst,
not on every pointer/wheel event. While zooming, the compositor transform changes
but the canvas dimensions stay fixed; the buffer resizes once after settling.
These controls affect only the preview's local state.

Load `/#side-pane` for the Heartbreaker side pane. Click **Preview** on the
workspace brief, **Workspace tour** on the worker row, or the workspace map
image. The worker tabs show **Autobot 1** and **Autobot 2**, with the task
title inside each tab. Switch between the tabs, resize the pane and type in the composer while
it is open. The fixture uses a local PDF and simulated worker events.
