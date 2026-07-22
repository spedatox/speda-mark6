"""Render Telegram bot profile pictures from the traced agent marks.

Telegram crops profile photos to a circle and re-encodes them, dropping the
alpha channel — so these are square, fully opaque, and keep the mark inside the
inscribed circle with margin. Rendered at 3x and downsampled for clean edges.
"""
import json, math, re, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT_SIZE = 1024
SS = 3                      # supersample factor
N = OUT_SIZE * SS
VIEW = 100.0
MARK_FRAC = 0.50            # mark's long edge as a fraction of the canvas
BASE = (7, 12, 17)          # the app's near-black field

META = {
    "speda":        "#36abca",
    "sentinel":     "#d99c44",
    "nightcrawler": "#9165e6",
    "ultron":       "#8a93a6",
    "centurion":    "#d8483c",
    "atomix":       "#3fae74",
    "optimus":      "#2f4f8f",
}


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lift(rgb, trigger=0.62, target=0.82):
    """Raise a too-dark accent's value so it carries against the near-black field.

    The UI accents are tuned to sit on lit glass, not on a black tile: Optimus's
    navy in particular goes muddy here. Lifting HSV value while holding hue and
    saturation keeps the brand colour recognisable — a blend toward white would
    wash the hue out instead.

    Trigger and target are deliberately separate. A blanket floor of `target`
    would also lift Ultron, Atomix and SPEDA, which already read correctly;
    only accents below `trigger` are actually in trouble, and those need a jump
    well past the trigger to fix. As tuned, Optimus is the only one that moves.
    """
    import colorsys
    r, g, b = (c / 255 for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v >= trigger:
        return rgb
    r, g, b = colorsys.hsv_to_rgb(h, s, target)
    return tuple(round(c * 255) for c in (r, g, b))


def flatten(d, steps=48):
    """Parse the M/L/C/Z subset the tracer emits into polylines."""
    subs, cur = [], []
    for cmd, args in re.findall(r"([MLCZ])([^MLCZ]*)", d):
        n = [float(v) for v in re.findall(r"-?\d*\.?\d+", args)]
        if cmd == "M":
            if cur:
                subs.append(cur)
            cur = [(n[0], n[1])]
        elif cmd == "L":
            cur.append((n[0], n[1]))
        elif cmd == "C":
            p0 = cur[-1]
            c1, c2, p1 = (n[0], n[1]), (n[2], n[3]), (n[4], n[5])
            for i in range(1, steps + 1):
                t = i / steps
                u = 1 - t
                cur.append((
                    u**3*p0[0] + 3*u*u*t*c1[0] + 3*u*t*t*c2[0] + t**3*p1[0],
                    u**3*p0[1] + 3*u*u*t*c1[1] + 3*u*t*t*c2[1] + t**3*p1[1]))
        elif cmd == "Z":
            if cur:
                subs.append(cur)
                cur = []
    if cur:
        subs.append(cur)
    return subs


def signed_area(p):
    n = len(p)
    return sum(p[i][0]*p[(i+1) % n][1] - p[(i+1) % n][0]*p[i][1] for i in range(n)) / 2


def place(subs):
    """Scale the 100-viewBox geometry into the canvas, centred."""
    k = (N * MARK_FRAC) / VIEW
    off = (N - VIEW * k) / 2
    return [[(x * k + off, y * k + off) for x, y in s] for s in subs]


def mask_of(subs):
    """Fill outers, punch holes — the nonzero winding the tracer guarantees."""
    img = Image.new("L", (N, N), 0)
    dr = ImageDraw.Draw(img)
    for s in sorted(subs, key=lambda s: -abs(signed_area(s))):
        dr.polygon(s, fill=255 if signed_area(s) > 0 else 0)
    return np.asarray(img).astype(np.float32) / 255.0


def outline(subs, width):
    img = Image.new("L", (N, N), 0)
    dr = ImageDraw.Draw(img)
    for s in subs:
        dr.line(list(s) + [s[0]], fill=255, width=int(width), joint="curve")
    return np.asarray(img).astype(np.float32) / 255.0


def blur(a, radius):
    im = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(radius))).astype(np.float32) / 255.0


def over(dst, src_rgb, alpha):
    """Composite a flat colour over dst using a per-pixel alpha map."""
    a = alpha[..., None]
    return dst * (1 - a) + np.asarray(src_rgb, np.float32) * a


def render(agent, path_d, accent_hex):
    accent = np.asarray(lift(hex_rgb(accent_hex)), np.float32)
    subs = place(flatten(path_d))
    mask = mask_of(subs)

    yy, xx = np.mgrid[0:N, 0:N].astype(np.float32)
    cx = cy = N / 2.0

    # ── background: near-black field, accent bloom above centre, vignette
    canvas = np.zeros((N, N, 3), np.float32) + np.asarray(BASE, np.float32)
    r = np.hypot(xx - cx, yy - cy * 0.82) / (N * 0.62)
    canvas = over(canvas, accent, np.clip(1 - r, 0, 1) ** 2.2 * 0.22)
    vig = np.clip(np.hypot(xx - cx, yy - cy) / (N * 0.72), 0, 1) ** 2.0
    canvas *= (1 - 0.55 * vig)[..., None]

    # ── outer glow: the mark bleeding into the field
    canvas = over(canvas, accent, blur(mask, N * 0.035) * 0.55)
    canvas = over(canvas, accent, blur(mask, N * 0.012) * 0.35)

    # ── the mark body
    canvas = over(canvas, accent, mask * 0.94)

    # ── sheen: white falling away from the top-left, clipped to the mark
    t = np.clip(((xx - 0.08 * N) * 0.54 + (yy - 0) * 1.0) / (N * 1.0), 0, 1)
    sheen = np.interp(t, [0.0, 0.42, 1.0], [0.62, 0.10, 0.0]).astype(np.float32)
    canvas = over(canvas, (255, 255, 255), sheen * mask)

    # ── specular bloom off the upper-left shoulder
    rb = np.hypot(xx - 0.34 * N, yy - 0.30 * N) / (N * 0.55)
    canvas = over(canvas, (255, 255, 255), np.clip(1 - rb, 0, 1) ** 1.6 * 0.34 * mask)

    # ── lit rim
    rim = outline(subs, max(2, N * 0.0022))
    canvas = over(canvas, (255, 255, 255), rim * 0.40)

    img = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8))
    return img.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)


if __name__ == "__main__":
    paths = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for agent, accent in META.items():
        im = render(agent, paths[agent], accent)
        p = out / f"{agent}.png"
        im.save(p, "PNG", optimize=True)
        print(f"{agent:14s} {im.size[0]}x{im.size[1]}  {p.stat().st_size // 1024} KB")
