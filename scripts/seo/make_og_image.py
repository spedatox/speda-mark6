#!/usr/bin/env python3
"""Render site/assets/og.png — the 1200x630 social card for Speda Mark VI.

Every crawler that builds a link preview (Google, X, Slack, Discord, Telegram,
LinkedIn, iMessage) reads og:image, and none of them accept SVG. So the card is
rendered here, checked into the repo, and served from a stable absolute URL.

Run:  .venv/Scripts/python.exe scripts/seo/make_og_image.py

Re-run it whenever the wordmark, tagline or agent accents change. The accents
below mirror packages/heartbreaker/src/renderer/src/profile/brands.ts, which
stays the single source of truth for colour.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 630
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "site" / "assets" / "og.png"

INK = (4, 7, 10)

# Agent accents, roster order — mirrors brands.ts.
ACCENTS = [
    (0x36, 0xAB, 0xCA),  # Speda
    (0xD9, 0x9C, 0x44),  # Sentinel
    (0x91, 0x65, 0xE6),  # NightCrawler
    (0x8A, 0x93, 0xA6),  # Ultron
    (0xD8, 0x48, 0x3C),  # Centurion
    (0x3F, 0xAE, 0x74),  # Atomix
    (0x2F, 0x4F, 0x8F),  # Optimus
    (0xE0, 0x70, 0x3A),  # Orion
]

# Candidate faces, best first. Bahnschrift ships with Windows 10 and reads as a
# technical grotesque; DejaVu is vendored in the repo so the render is portable.
DISPLAY_FACES = [
    r"C:\Windows\Fonts\bahnschrift.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "packages/igor/app/skills/fonts/DejaVuSans-Bold.ttf",
]
MONO_FACES = [
    r"C:\Windows\Fonts\consola.ttf",
    "packages/igor/app/skills/fonts/DejaVuSansMono.ttf",
]
BODY_FACES = [
    r"C:\Windows\Fonts\segoeui.ttf",
    "packages/igor/app/skills/fonts/DejaVuSans.ttf",
]


def load(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for name in candidates:
        path = Path(name)
        if not path.is_absolute():
            path = ROOT / path
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def bloom(size: tuple[int, int], center: tuple[int, int], radius: int,
          colour: tuple[int, int, int], strength: float) -> Image.Image:
    """One soft radial glow, built as a blurred disc on its own layer."""
    layer = Image.new("RGB", size, (0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x, y = center
    draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                 fill=tuple(int(c * strength) for c in colour))
    return layer.filter(ImageFilter.GaussianBlur(radius * 0.62))


def add(base: Image.Image, glow: Image.Image) -> Image.Image:
    """Additive blend, clamped — keeps the ground black rather than washing grey."""
    return ImageChops.add(base, glow)


def tracked(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
            font: ImageFont.FreeTypeFont, fill, tracking: float) -> int:
    """Draw text with letter-spacing; PIL has no tracking of its own."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return int(x - tracking)


def tracked_width(draw: ImageDraw.ImageDraw, text: str,
                  font: ImageFont.FreeTypeFont, tracking: float) -> int:
    """Width `tracked` will occupy — needed to right-align it."""
    return int(sum(draw.textlength(ch, font=font) for ch in text)
               + tracking * (len(text) - 1))


def main() -> None:
    canvas = Image.new("RGB", (W, H), INK)

    # Aurora — the same three bleeds the site uses behind its fold.
    for center, radius, colour, strength in [
        ((190, 40), 400, (0x36, 0xAB, 0xCA), 0.15),
        ((1060, 120), 340, (0x91, 0x65, 0xE6), 0.13),
        ((680, 660), 430, (0x2F, 0x4F, 0x8F), 0.15),
    ]:
        canvas = add(canvas, bloom((W, H), center, radius, colour, strength))

    draw = ImageDraw.Draw(canvas)

    pad = 84
    f_eyebrow = load(MONO_FACES, 21)
    f_display = load(DISPLAY_FACES, 132)
    f_sub = load(BODY_FACES, 33)
    f_foot = load(MONO_FACES, 22)

    # Eyebrow rule + label
    y = 108
    draw.line((pad, y + 12, pad + 46, y + 12), fill=(0x36, 0xAB, 0xCA), width=2)
    tracked(draw, (pad + 66, y), "SPECIALIZED PERSONAL EXECUTIVE DIGITAL ASSISTANT",
            f_eyebrow, (0x9A, 0xA7, 0xB6), 3.4)

    # Wordmark
    y = 168
    end = tracked(draw, (pad, y), "SPEDA", f_display, (0xFF, 0xFF, 0xFF), 2.0)
    tracked(draw, (end + 30, y + 30), "MARK VI", load(DISPLAY_FACES, 92),
            (0x36, 0xAB, 0xCA), 2.0)

    # Tagline
    draw.text((pad, 352), "A private multi-agent AI assistant",
              font=f_sub, fill=(0xE8, 0xED, 0xF3))
    draw.text((pad, 396), "that acts before you ask.",
              font=f_sub, fill=(0x9A, 0xA7, 0xB6))

    # Agent jewels — one dot per roster member, each with its own halo.
    cx, cy, gap, r = pad + 6, 486, 46, 9
    for i, colour in enumerate(ACCENTS):
        x = cx + i * gap
        canvas = add(canvas, bloom((W, H), (x, cy), 24, colour, 0.42))
        draw = ImageDraw.Draw(canvas)
        draw.ellipse((x - r, cy - r, x + r, cy + r), fill=colour)

    # Footer hairline + source line
    draw.rectangle((pad, H - 96, W - pad, H - 95), fill=(0x22, 0x2A, 0x33))
    tracked(draw, (pad, H - 70), "GITHUB.COM/SPEDATOX/SPEDA-MARK6",
            f_foot, (0x64, 0x70, 0x7E), 2.6)

    right = "EIGHT AGENTS  ·  ONE MEMORY"
    tracked(draw, (W - pad - tracked_width(draw, right, f_foot, 2.6), H - 70),
            right, f_foot, (0x64, 0x70, 0x7E), 2.6)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
