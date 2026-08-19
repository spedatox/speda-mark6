#!/usr/bin/env python3
"""Build the Speda Mark VI public site into site/.

Why a generator instead of 23 hand-written files: every page needs a correct
canonical, OpenGraph block, Twitter card, breadcrumb, JSON-LD graph and nav
state. Maintained by hand across two dozen pages that drifts within a week,
and a wrong canonical is worse than no canonical. Content is authored per page
in scripts/site/content_*.py; everything structural is generated here exactly
once.

The output is plain static HTML with no runtime dependency on this script —
site/ is committed and GitHub Pages serves it verbatim.

Run:  .venv/Scripts/python.exe scripts/site/build.py
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "site"
EMBLEM_DIR = ROOT / "logos" / "svg"

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = "https://spedatox.github.io/speda-mark6"
REPO = "https://github.com/spedatox/speda-mark6"
AUTHOR = "Ahmet Erol Bayrak"
AUTHOR_URL = "https://github.com/spedatox"
TODAY = date.today().isoformat()

# The roster, in command order. Accent values mirror the client brand tables
# (brands.ts / Brands.kt / agents.ts), which agree with each other; the backend
# DocTheme for optimus and orion has drifted and is NOT the source used here.
ROSTER = [
    ("speda",        "Speda",        "Mark VI",  "Chief of Staff"),
    ("sentinel",     "Sentinel",     "Mark II",  "Finance & Budget"),
    ("nightcrawler", "NightCrawler", "Mark III", "OSINT & Surveillance"),
    ("ultron",       "Ultron",       "Mark III", "Academic Life"),
    ("centurion",    "Centurion",    "Mark I",   "Cyber Security"),
    ("atomix",       "Atomix",       "Mark I",   "Health & Wellness"),
    ("optimus",      "Optimus",      "Mark II",  "Systems & Code"),
    ("orion",        "Orion",        "Mark I",   "Maintenance & Memory"),
]

NAV = [
    ("agents",       "Agents"),
    ("systems",      "Systems"),
    ("heartbreaker", "Heartbreaker"),
    ("speda-go",     "Speda GO"),
    ("igor",         "Igor"),
    ("faq",          "FAQ"),
]

FOOTER = [
    ("The roster", [
        ("agents", "All eight agents"),
        ("agents/speda", "Speda — Chief of Staff"),
        ("agents/sentinel", "Sentinel — finance"),
        ("agents/nightcrawler", "NightCrawler — OSINT"),
        ("agents/ultron", "Ultron — academic life"),
        ("agents/centurion", "Centurion — security"),
        ("agents/atomix", "Atomix — health"),
        ("agents/optimus", "Optimus — code"),
        ("agents/orion", "Orion — custodian"),
    ]),
    ("The systems", [
        ("systems", "All systems"),
        ("systems/legion", "The Legion"),
        ("systems/memory", "Memory & recall"),
        ("systems/automations", "Proactive automations"),
        ("systems/news", "The News Desk"),
        ("systems/forge", "The Forge"),
        ("systems/maps", "Maps & navigation"),
        ("systems/house-party", "House Party Protocol"),
        ("systems/capabilities", "The capability arsenal"),
    ]),
    ("The software", [
        ("heartbreaker", "Heartbreaker — desktop"),
        ("speda-go", "Speda GO — Android"),
        ("igor", "Igor — the backend"),
        ("faq", "Frequently asked"),
    ]),
    ("Source", [
        (REPO, "speda-mark6 on GitHub"),
        ("https://github.com/spedatox/speda-go", "Speda GO repository"),
        ("https://github.com/spedatox/forge-mark1", "The Forge repository"),
        (AUTHOR_URL, "All projects by spedatox"),
    ]),
]


@dataclass
class Page:
    slug: str                 # "" for the root; no leading or trailing slash
    title: str                # <title> — keep under 60 characters
    description: str          # meta description — aim for 140-160 characters
    keywords: str
    body: str                 # inner HTML of <main>
    nav: str = ""             # which NAV entry is current
    accent: str = "speda"     # a --var name from the roster palette
    crumbs: list = field(default_factory=list)   # [(slug, label), ...]
    jsonld: list = field(default_factory=list)   # extra @graph nodes
    priority: str = "0.7"
    changefreq: str = "monthly"
    rig: bool = False         # include the orbital-rig script
    og_type: str = "article"


# ── Emblems ──────────────────────────────────────────────────────────────────

_emblem_cache: dict[str, str] = {}


def emblem(agent_id: str) -> str:
    """Inline an agent's SVG mark.

    Inlined rather than <img> because the marks use fill="currentColor" — that
    is the whole reason they can be re-tinted per agent from CSS, and it also
    saves eight requests in the hero.
    """
    if agent_id not in _emblem_cache:
        path = EMBLEM_DIR / f"{agent_id}.svg"
        svg = path.read_text(encoding="utf-8")
        # Strip the XML-only attributes and the <title>; the surrounding markup
        # supplies the accessible name, and a duplicate is noise for a reader.
        svg = svg.replace('xmlns="http://www.w3.org/2000/svg" ', "")
        svg = svg.replace(' width="100" height="100"', "")
        start = svg.find("<title>")
        if start != -1:
            end = svg.find("</title>") + len("</title>")
            svg = svg[:start] + svg[end:]
        svg = svg.replace(' role="img"', ' aria-hidden="true" focusable="false"')
        _emblem_cache[agent_id] = " ".join(svg.split())
    return _emblem_cache[agent_id]


# ── Rendering ────────────────────────────────────────────────────────────────

def rel(slug: str) -> str:
    """Prefix that walks from `slug` back to the site root."""
    depth = len([p for p in slug.split("/") if p])
    return "../" * depth if depth else ""


def url(slug: str) -> str:
    return f"{BASE}/" if not slug else f"{BASE}/{slug}/"


def href(from_slug: str, target: str) -> str:
    """Link from one page to another, or pass an absolute URL straight through."""
    if target.startswith("http"):
        return target
    return (rel(from_slug) + (target + "/" if target else "")) or "./"


def render_nav(page: Page) -> str:
    items = []
    for slug, label in NAV:
        current = ' aria-current="page"' if page.nav == slug else ""
        items.append(f'<a href="{href(page.slug, slug)}"{current}>{label}</a>')
    items.append(f'<a href="{REPO}">GitHub</a>')
    return "\n      ".join(items)


def render_crumbs(page: Page) -> str:
    if not page.crumbs:
        return ""
    parts = [f'<a href="{href(page.slug, "")}">Speda Mark VI</a>']
    for slug, label in page.crumbs:
        parts.append(f'<a href="{href(page.slug, slug)}">{label}</a>')
    joined = "<span>/</span>".join(parts)
    return f'<p class="crumb">{joined}<span>/</span></p>'


def crumb_jsonld(page: Page) -> dict | None:
    if not page.crumbs:
        return None
    items = [{"@type": "ListItem", "position": 1, "name": "Speda Mark VI", "item": url("")}]
    for i, (slug, label) in enumerate(page.crumbs, start=2):
        items.append({"@type": "ListItem", "position": i, "name": label, "item": url(slug)})
    return {"@type": "BreadcrumbList", "itemListElement": items}


def render_footer(page: Page) -> str:
    cols = []
    for heading, links in FOOTER:
        lis = "".join(
            f'<li><a href="{href(page.slug, t)}">{label}</a></li>' for t, label in links
        )
        cols.append(f"<div><h4>{heading}</h4><ul>{lis}</ul></div>")
    return "\n      ".join(cols)


def json_dump(obj) -> str:
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False)


def esc(text: str) -> str:
    """Escape for an HTML text node or a double-quoted attribute.

    Titles and descriptions are authored as plain text — "Memory & Recall", not
    "Memory &amp; Recall" — because the same strings are also emitted into
    JSON-LD, where an HTML entity would be stored literally and read back by a
    crawler as the characters a-m-p. Escaping happens here, once, per context.
    """
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical}">

<meta name="author" content="{author}">
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
<meta name="keywords" content="{keywords}">

<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="Speda Mark VI">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{base}/assets/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Speda Mark VI — Specialized Personal Executive Digital Assistant">
<meta property="og:locale" content="en_US">

<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{base}/assets/og.png">
<meta name="twitter:creator" content="@spedatox">

<meta name="theme-color" content="#03060a">
<link rel="icon" href="{rel}assets/favicon.svg" type="image/svg+xml">

<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400&display=swap">
<link rel="stylesheet" href="{rel}assets/site.css">

<script type="application/ld+json">
{jsonld}
</script>
</head>

<body style="--a: var(--{accent})">
<a class="skip" href="#main">Skip to content</a>

<header class="masthead">
  <div class="wrap">
    <a class="wordmark" href="{home}"><span class="jewel"></span>S.P.E.D.A.<span class="mk">Mark VI</span></a>
    <nav class="nav" aria-label="Primary">
      {nav}
    </nav>
  </div>
</header>

<main id="main">
{body}
</main>

<footer class="footer">
  <div class="wrap">
    <div class="footer-grid">
      {footer}
    </div>
    <p class="colophon">
      <span><strong>S.P.E.D.A.</strong> — Specialized Personal Executive Digital Assistant · Mark VI</span>
      <span>Built by <a href="{author_url}">{author}</a> · Private project, not licensed for redistribution.</span>
    </p>
  </div>
</footer>
{scripts}
</body>
</html>
"""


def build_page(page: Page) -> str:
    graph = list(page.jsonld)
    crumbs = crumb_jsonld(page)
    if crumbs:
        graph.insert(0, crumbs)

    # The author node is attached to every page: it is how an answer engine
    # resolves "who built Speda Mark VI" without guessing.
    graph.append({
        "@type": "Person",
        "@id": f"{BASE}/#author",
        "name": AUTHOR,
        "alternateName": "spedatox",
        "url": AUTHOR_URL,
        "sameAs": [AUTHOR_URL, "https://x.com/spedatox"],
    })

    scripts = ""
    if page.rig:
        base = rel(page.slug)
        # three.js is pinned by exact version — a floating tag would let a
        # future release silently change how the hero renders.
        scripts = (
            '<script type="importmap">\n'
            '{"imports":{'
            '"three":"https://cdn.jsdelivr.net/npm/three@0.169.0/build/three.module.js",'
            '"three/addons/":"https://cdn.jsdelivr.net/npm/three@0.169.0/examples/jsm/"'
            "}}\n</script>\n"
            f'<script type="module" src="{base}assets/hero3d.js"></script>'
        )

    return TEMPLATE.format(
        title=esc(page.title),
        description=esc(page.description),
        keywords=esc(page.keywords),
        canonical=url(page.slug),
        og_type=page.og_type,
        base=BASE,
        rel=rel(page.slug),
        home=href(page.slug, ""),
        author=AUTHOR,
        author_url=AUTHOR_URL,
        accent=page.accent,
        jsonld=json_dump({"@context": "https://schema.org", "@graph": graph}),
        nav=render_nav(page),
        body=page.body,
        footer=render_footer(page),
        scripts=scripts,
    )


def write_sitemap(pages: list[Page]) -> None:
    rows = []
    for p in pages:
        rows.append(
            "  <url>\n"
            f"    <loc>{url(p.slug)}</loc>\n"
            f"    <lastmod>{TODAY}</lastmod>\n"
            f"    <changefreq>{p.changefreq}</changefreq>\n"
            f"    <priority>{p.priority}</priority>\n"
            "  </url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(rows)
        + "\n</urlset>\n"
    )
    (OUT / "sitemap.xml").write_text(xml, encoding="utf-8")


def main() -> None:
    from content_home import home_page, faq_page
    from content_agents import agent_pages, roster_page
    from content_systems import system_pages, systems_hub, client_pages

    pages: list[Page] = [home_page()]
    pages.append(roster_page())
    pages.extend(agent_pages())
    pages.extend(client_pages())
    pages.append(systems_hub())
    pages.extend(system_pages())
    pages.append(faq_page())

    # Clean only generated HTML — assets/, robots.txt, llms.txt and .nojekyll
    # are hand-maintained and must survive a rebuild.
    for old in OUT.rglob("index.html"):
        old.unlink()
    for d in sorted((p for p in OUT.rglob("*") if p.is_dir()), reverse=True):
        if d.name != "assets" and not any(d.iterdir()):
            d.rmdir()

    for page in pages:
        target = OUT / page.slug / "index.html" if page.slug else OUT / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(build_page(page), encoding="utf-8")

    write_sitemap(pages)

    print(f"built {len(pages)} pages into {OUT.relative_to(ROOT)}")
    for p in pages:
        flag = "  [rig]" if p.rig else ""
        print(f"  /{p.slug or ''}{'' if not p.slug else '/'}  —  {p.title}{flag}")


if __name__ == "__main__":
    main()
