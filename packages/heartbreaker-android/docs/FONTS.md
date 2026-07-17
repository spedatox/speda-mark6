# res/font — bundled type

The shipping web stack (heartbreaker.css, after the killed display-monospace and
the missing SamsungOne) is **Rajdhani + Inter + JetBrains Mono** — all OFL, so
they bundle here. These TTF **binaries are not in the repo yet** (the web pulls
Rajdhani/Inter from the Google CDN). Until they land, `HbFonts` in the
designsystem falls back to the platform families so the app compiles and runs.

## Drop-in

Add these files (lowercase, digits-and-underscores only — Android resource
naming), then point `HbFonts` at the `R.font` families:

```
rajdhani_light.ttf        (300)
rajdhani_regular.ttf      (400)
rajdhani_medium.ttf       (500)
rajdhani_semibold.ttf     (600)
rajdhani_bold.ttf         (700)
inter_regular.ttf         (400)
inter_medium.ttf          (500)
inter_semibold.ttf        (600)
inter_bold.ttf            (700)
jetbrains_mono_regular.ttf
```

Sources: Rajdhani & JetBrains Mono — Google Fonts (OFL). Inter — rsms.me/inter
(OFL). If the SamsungOne / SamsungSharpSans TTFs are ever recovered (separately
flagged missing), add them the same way and extend `HbFonts`.

No metric changes are needed when the fonts land — only the `FontFamily` in
`HbFonts` (size / weight / tracking / line-height are already ported from the
CSS).
