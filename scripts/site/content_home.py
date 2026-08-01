"""Home page and FAQ for the SPEDA Mark VI site.

Copy is written against the code, not the README: the roster domains come from
app/profiles/*.py, the doctrine lines from app/prompts/agents/*/01_identity.md,
and the architecture claims from app/core/ and docs/REFERENCE.md.
"""

from build import BASE, REPO, ROSTER, Page, emblem, url


# GLASS is refraction, not translucency. Earlier passes here built the mark out
# of stacked translucent copies, which can only ever produce a tinted silhouette
# — no matter how the colour is tuned it reads as moulded plastic, because the
# background behind it is not being BENT.
#
# The real effect needs two things, and the second is the one usually missed:
#   1. A displacement map driving feDisplacementMap through backdrop-filter,
#      built at runtime in assets/glass.js from this exact silhouette.
#   2. Something worth refracting. Glass over a flat dark background is
#      invisible however good the filter is, so .mono-field puts a genuine
#      colour field behind the mark for the glass to distort.


def _mark_mask() -> str:
    """The SPEDA path as a CSS mask, so glass can be clipped to the mark itself.

    `backdrop-filter` frosts a rectangle by default. Masking the element to the
    logo's own silhouette is what makes the *mark* the glass object rather than
    a logo sitting on a glass card — there is no slab behind it to give away.
    """
    from pathlib import Path
    from urllib.parse import quote
    import re

    raw = (Path(__file__).resolve().parents[2] / "logos" / "svg" / "speda.svg").read_text(encoding="utf-8")
    d = re.search(r'\sd="([^"]+)"', raw).group(1)
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
        f"<path fill='#fff' d='{d}'/></svg>"
    )
    # Single quotes: this lands inside a double-quoted HTML style attribute, and
    # url("…") would terminate the attribute at the first inner quote. The
    # payload is fully percent-encoded, so it contains no quotes of its own.
    return f"url('data:image/svg+xml,{quote(svg, safe='')}')"


def _monolith() -> str:
    """The hero object: the SPEDA mark itself, cut from glass and extruded.

    Three parts stacked in Z — the extrusion behind (the same mark repeated,
    each copy deeper and darker, which is what gives the object a real side
    when scroll rotates it), the frosted glass body masked to the mark's own
    outline, and a lit rim drawn as an SVG stroke because a box-shadow cannot
    follow a masked silhouette.
    """
    mark = emblem("speda")
    return (
        '<div class="monolith">\n'
        '        <span class="mono-field"></span>\n'
        '        <canvas class="mono-canvas" data-glass3d '
        'aria-label="The SPEDA Mark VI emblem rendered in glass" role="img"></canvas>\n'
        '        <div class="mono-fallback" data-glass-path aria-hidden="true">'
        f"{mark}</div>\n"
        "      </div>"
    )


def _roster_cards() -> str:
    blurbs = {
        "speda": "Plans, routes, arms automations and commands the roster. The only agent whose recall reaches across every other agent's history.",
        "sentinel": "Numbers first, narrative second. Pulls the actual quote before it opines, and states the downside every time.",
        "nightcrawler": "Corroborates, never trusts. A single source is a lead, not a fact — and everything carries its trail back.",
        "ultron": "Owns the collision between university and a job. Plans over pep talks, with honest cut-lines.",
        "centurion": "Defence and offence in balance, on authorized targets only. Severity lives in the finding, never in the delivery.",
        "atomix": "The owner's body, on evidence rather than fads — and it says plainly when the evidence is weak.",
        "optimus": "Builds to ship. Runs on a standalone execution engine with a real shell when the Forge is up.",
        "orion": "The custodian. Moves, merges and timestamps existing memory — and never invents a fact.",
    }
    cards = []
    for agent_id, name, mark, domain in ROSTER:
        cards.append(f"""      <a class="holo pad agent-card rise" style="--a: var(--{agent_id})" href="agents/{agent_id}/">
        <span class="emblem">{emblem(agent_id)}</span>
        <span class="mark">{mark}</span>
        <span class="name">{name}</span>
        <span class="domain">{domain}</span>
        <p class="card-body">{blurbs[agent_id]}</p>
      </a>""")
    return "\n".join(cards)


HOME_BODY = """
<section class="hero hero-home">
  <div class="hero-copy">
    <p class="eyebrow rise">Specialized Personal Executive Digital Assistant</p>
    <h1 class="rise">SPEDA<br>Mark VI
      <span class="hero-sub">Eight agents. One memory. Zero prompts required.</span>
    </h1>
    <p class="lede rise">A private, self-hosted AI assistant built as a <strong>standing staff</strong>
      rather than a chatbot. Eight domain specialists share one persistent memory of a single owner,
      run senses of their own, act while he sleeps, and answer through a holographic command deck.</p>
    <div class="actions rise">
      <a class="btn btn-primary" href="agents/">Meet the roster</a>
      <a class="btn" href="systems/">How it works</a>
      <a class="btn" href="{repo}">Source on GitHub</a>
    </div>
  </div>
  {monolith}
  <p class="scroll-cue">Scroll</p>
</section>

<section id="premise">
  <div class="wrap">
    <div class="section-head">
      <p class="eyebrow">The premise</p>
      <h2>An assistant that forgets you is not an assistant.</h2>
      <p class="lede">Every general chatbot begins each conversation as a stranger with no stake in your
        week. SPEDA Mark VI inverts all three assumptions: it serves <strong>one owner</strong>, it keeps a
        <strong>structured memory</strong> of him that outlives any session, and it has
        <strong>senses that fire on their own schedule</strong> instead of waiting to be asked.</p>
    </div>

    <div class="stats rise">
      <div class="stat"><span class="n">8</span><span class="l">specialist agents</span></div>
      <div class="stat"><span class="n">7</span><span class="l">model providers</span></div>
      <div class="stat"><span class="n">4</span><span class="l">capability tiers</span></div>
      <div class="stat"><span class="n">3</span><span class="l">client surfaces</span></div>
      <div class="stat"><span class="n">0</span><span class="l">accounts or tenancy</span></div>
    </div>

    <div class="grid g3" style="margin-top:1.1rem">
      <article class="holo pad rise">
        <h3 class="card-title">It knows you</h3>
        <p class="card-body">A closed set of eight Markdown memory files, each answering exactly one question,
          with a dedicated custodian agent enforcing the boundaries. <a href="systems/memory/">How memory works →</a></p>
      </article>
      <article class="holo pad rise">
        <h3 class="card-title">It acts unprompted</h3>
        <p class="card-body">Watchers on pages, feeds, markets and mail — gated by zero-token probes so a poll
          never costs a turn. <a href="systems/automations/">The automation layer →</a></p>
      </article>
      <article class="holo pad rise">
        <h3 class="card-title">It delegates</h3>
        <p class="card-body">Agents dispatch to each other, and any of them can deploy disposable workers for
          genuine fan-out. <a href="systems/legion/">The Legion →</a></p>
      </article>
      <article class="holo pad rise">
        <h3 class="card-title">It survives you</h3>
        <p class="card-body">Turns run detached from your connection. Close the window mid-answer; the work
          finishes server-side and re-attaches when you return. <a href="igor/">Inside Igor →</a></p>
      </article>
      <article class="holo pad rise">
        <h3 class="card-title">It runs anywhere</h3>
        <p class="card-body">One router speaks seven providers including local Ollama. Pin a model per agent
          or run the whole system offline. <a href="systems/capabilities/">The arsenal →</a></p>
      </article>
      <article class="holo pad rise">
        <h3 class="card-title">It stays yours</h3>
        <p class="card-body">Self-hosted, single-key, no accounts. Conversations, memory and files never leave
          hardware you control. <a href="faq/">Questions answered →</a></p>
      </article>
    </div>
  </div>
</section>

<section id="roster">
  <div class="wrap">
    <div class="section-head">
      <p class="eyebrow">The roster</p>
      <h2>Eight specialists, not eight installs</h2>
      <p class="lede">Seven of them are <strong>in-process agent profiles</strong> inside a single backend —
        one event loop, one database, one capability registry, one owner's memory — addressed by
        <code>agent_id</code> on every request. Optimus is the architectural exception, and Centurion can
        join it. Each carries its own identity, accent and doctrine.</p>
    </div>
    <div class="grid g4">
{roster}
    </div>
  </div>
</section>

<section id="systems">
  <div class="wrap">
    <div class="section-head">
      <p class="eyebrow">The systems</p>
      <h2>Every part, sold separately</h2>
      <p class="lede">Eight subsystems do the actual work. None of them is a wrapper around a single API call.</p>
    </div>

    <div class="split">
      <div>
        <p class="eyebrow" style="--a: var(--nightcrawler)">01 — The Legion</p>
        <h3 style="font-size:1.9rem">Disposable workers, on whatever model you're already paying for</h3>
        <p>When a job genuinely needs six independent searches across six subtopics, an agent deploys
          anonymous single-purpose workers: a <strong>scout</strong> triages, <strong>researchers</strong>
          each take one subtopic in parallel, an <strong>analyst</strong> synthesises, a
          <strong>judge</strong> audits the draft claim by claim and returns a verdict per claim.</p>
        <p>Worker models resolve provider-agnostically — low and medium effort drop to the cheap tier
          <em>of the provider the parent turn is already on</em>, never a hardcoded model ID. The tool's own
          description talks the model <em>out</em> of using it: expensive and rare, never for lookups.</p>
        <p><a href="systems/legion/">Read the Legion doctrine →</a></p>
      </div>
      <div class="holo pad-lg" style="--a: var(--nightcrawler)">
        <table style="min-width:0">
          <thead><tr><th>Legionnaire</th><th>Effort</th><th>Budget</th></tr></thead>
          <tbody>
            <tr><td><strong>scout</strong></td><td>low</td><td>6 iterations</td></tr>
            <tr><td><strong>researcher</strong></td><td>medium</td><td>15 iterations</td></tr>
            <tr><td><strong>analyst</strong></td><td>high</td><td>20 iterations</td></tr>
            <tr><td><strong>judge</strong></td><td>low</td><td>8 iterations</td></tr>
            <tr><td><strong>general</strong></td><td>inherit</td><td>15 iterations</td></tr>
          </tbody>
        </table>
        <ul class="chips">
          <li>no recursive spawning</li>
          <li>no dispatch surface</li>
          <li>12k char result cap</li>
          <li>max 3 background</li>
        </ul>
      </div>
    </div>

    <div class="split flip" style="margin-top:clamp(3rem,6vw,5.5rem)">
      <div class="holo pad-lg" style="--a: var(--orion)">
        <table style="min-width:0">
          <thead><tr><th>File</th><th>The one question it answers</th></tr></thead>
          <tbody>
            <tr><td><strong>current.md</strong></td><td>What is true right now?</td></tr>
            <tr><td><strong>owner.md</strong></td><td>Who is he, before Mark VI existed?</td></tr>
            <tr><td><strong>dossier.md</strong></td><td>What has he been observed to prefer?</td></tr>
            <tr><td><strong>projects.md</strong></td><td>What is he building, and where does it stand?</td></tr>
            <tr><td><strong>social.md</strong></td><td>Who matters to him, and who are they to him?</td></tr>
            <tr><td><strong>sessions.md</strong></td><td>What happened in the gym, day by day?</td></tr>
            <tr><td><strong>history.md</strong></td><td>What happened that no longer applies?</td></tr>
            <tr><td><strong>log.md</strong></td><td>Rolling one-line session trail.</td></tr>
          </tbody>
        </table>
      </div>
      <div>
        <p class="eyebrow" style="--a: var(--orion)">02 — Memory</p>
        <h3 style="font-size:1.9rem">A closed file taxonomy, and one agent whose entire job is enforcing it</h3>
        <p>Memory rots when hygiene is everyone's job and therefore nobody's. So Mark VI declares
          <strong>exactly eight files</strong>, one question each — creating any other top-level file is a
          protocol violation — and hands the whole problem to <a href="agents/orion/">Orion</a>, a custodian
          agent that merges strays, demotes ended states and timestamps everything.</p>
        <p>One rule governs conflicts: <strong><code>current.md</code> outranks every other file for the
          present tense.</strong> When history says "works an IT job" and current says otherwise, current
          wins and the other is demoted rather than deleted.</p>
        <p><a href="systems/memory/">Read the memory contract →</a></p>
      </div>
    </div>

    <div class="split" style="margin-top:clamp(3rem,6vw,5.5rem)">
      <div>
        <p class="eyebrow" style="--a: var(--sentinel)">03 — Proactive automations</p>
        <h3 style="font-size:1.9rem">A poll must not cost a turn</h3>
        <p>Tell it what to watch in plain language — <em>"track this page for a month and tell me when the
          results are up"</em> — and the agent composes the watcher itself, arms it, and writes you a proper
          message on Telegram when it fires.</p>
        <p>The expensive part is what most systems get wrong. A watcher that fires a full agentic turn every
          tick spends real money to learn that nothing happened: at a ten-minute cadence that is roughly
          <strong>144 turns a day to answer "no"</strong>. So every watcher is split in two — a deterministic
          zero-token <strong>probe</strong>, and a <strong>trigger</strong> that only runs when the probe
          returns a hit.</p>
        <p><a href="systems/automations/">How the probes work →</a></p>
      </div>
      <div class="holo pad-lg" style="--a: var(--sentinel)">
        <div class="doctrine" style="margin:0">
          Exactly-once is the probe's job, and it commits last. The scan never marks its own findings as
          handled; the acknowledgement happens only after the trigger was accepted.
          <cite>A duplicate push is recoverable. A swallowed exam result is not.</cite>
        </div>
      </div>
    </div>

    <div class="grid g3" style="margin-top:clamp(3rem,6vw,5.5rem)">
      <a class="holo pad agent-card rise" style="--a: var(--nightcrawler)" href="systems/news/">
        <span class="mark">04</span><span class="name">The News Desk</span>
        <span class="domain">Two-tier intelligence</span>
        <p class="card-body">An always-on zero-cost RSS layer with cross-outlet dedup and keyword alerts,
          plus a quota-budgeted analyst tier spent only when the free one cannot answer.</p>
      </a>
      <a class="holo pad agent-card rise" style="--a: var(--optimus)" href="systems/forge/">
        <span class="mark">05</span><span class="name">The Forge</span>
        <span class="domain">Real code on real machines</span>
        <p class="card-body">A standalone execution engine that connects back as a WebSocket peer, giving
          Optimus a privileged shell inside its own isolated cell — with graceful fallback when it's down.</p>
      </a>
      <a class="holo pad agent-card rise" style="--a: var(--speda)" href="systems/maps/">
        <span class="mark">06</span><span class="name">Maps &amp; navigation</span>
        <span class="domain">Cartography, inline</span>
        <p class="card-body">Traffic-aware routing rendered inside the conversation. The origin defaults to
          your live position, so "how do I get home?" never triggers a follow-up question.</p>
      </a>
      <a class="holo pad agent-card rise" style="--a: var(--warroom)" href="systems/house-party/">
        <span class="mark">07</span><span class="name">House Party Protocol</span>
        <span class="domain">All hands, passphrase-gated</span>
        <p class="card-body">The whole roster in parallel at full interactive model grade with domain
          boundaries relaxed — and a War Room takeover of the client until you stand down.</p>
      </a>
      <a class="holo pad agent-card rise" style="--a: var(--centurion)" href="systems/capabilities/">
        <span class="mark">08</span><span class="name">The arsenal</span>
        <span class="domain">Four tiers, one array</span>
        <p class="card-body">Skills, MCP servers and wrapped open-source applications — all identical to the
          model, with lazy loading so a huge toolset costs almost nothing to carry.</p>
      </a>
      <a class="holo pad agent-card rise" style="--a: var(--ultron)" href="systems/">
        <span class="mark">↗</span><span class="name">All systems</span>
        <span class="domain">The index</span>
        <p class="card-body">Every subsystem in one place, with the architectural rules each one is built
          to obey.</p>
      </a>
    </div>
  </div>
</section>

<section id="surfaces">
  <div class="wrap">
    <div class="section-head">
      <p class="eyebrow">Where it lives</p>
      <h2>Three surfaces, one assistant</h2>
      <p class="lede">All three speak the same HTTP, SSE and WebSocket surface. The clients render the
        network; <a href="igor/">Igor</a> runs it, and holds every line of business logic.</p>
    </div>
    <figure style="margin-top:0">
      <img src="https://github.com/user-attachments/assets/3691eed2-f8a2-4922-b188-2fe3c24b3402"
           width="1920" height="1014"
           alt="Heartbreaker, the SPEDA Mark VI desktop command deck, showing a live agent conversation in fluid-glass panels">
      <figcaption>Heartbreaker — the primary interface, themed to the active agent</figcaption>
    </figure>
    <div class="grid g3" style="margin-top:2.6rem">
      <a class="holo pad agent-card rise" href="heartbreaker/">
        <span class="name">Heartbreaker</span><span class="domain">Desktop · Electron + React</span>
        <p class="card-body">The fluid-glass command deck: agent switcher, a live Systems Board of routing and
          budget telemetry, and a comms tray where you watch agents talk to each other.</p>
      </a>
      <a class="holo pad agent-card rise" style="--a: var(--atomix)" href="speda-go/">
        <span class="name">SPEDA GO</span><span class="domain">Android · Kotlin + Compose</span>
        <p class="card-body">Native, not a wrapped web view. Full chat core, inline maps and charts, offline
          transcript cache — verified against the desktop theme engine by fixture tests.</p>
      </a>
      <a class="holo pad agent-card rise" style="--a: var(--optimus)" href="igor/">
        <span class="name">Igor</span><span class="domain">Backend · FastAPI</span>
        <p class="card-body">One orchestrator owning the loop and the system prompt, one registry owning every
          capability, and a written contract the code is not allowed to break.</p>
      </a>
    </div>
  </div>
</section>

<section id="run">
  <div class="wrap">
    <div class="section-head">
      <p class="eyebrow">See it running</p>
      <h2>Three commands to a live deck</h2>
      <p class="lede">SQLite by default, so a first run needs no services at all.</p>
    </div>
    <pre><code><span class="c"># 1. Configure</span>
cp .env.example packages/igor/.env      <span class="c"># one provider key + SPEDA_API_KEY</span>

<span class="c"># 2. Igor, the backend</span>
cd packages/igor &amp;&amp; uv sync &amp;&amp; uv run uvicorn app.main:app --port 8000 --reload

<span class="c"># 3. The command deck (repo root, new terminal)</span>
npm install &amp;&amp; npm run heartbreaker:dev</code></pre>
    <p style="margin-top:1.6rem;color:var(--fg-dim)">On Windows <code>speda.ps1</code> boots the whole system —
      backend, sandbox, Forge link and app — in one command. <code>docker compose up -d</code> brings up the
      full stack with Postgres, the isolated sandbox and n8n.</p>
    <div class="actions">
      <a class="btn btn-primary" href="{repo}">Read the source</a>
      <a class="btn" href="faq/">Frequently asked questions</a>
    </div>
  </div>
</section>
"""


def home_page() -> Page:
    body = (HOME_BODY
            .replace("{monolith}", _monolith())
            .replace("{roster}", _roster_cards())
            .replace("{repo}", REPO))
    return Page(
        slug="",
        title="SPEDA Mark VI — Self-Hosted Multi-Agent AI Assistant",
        description=(
            "SPEDA Mark VI is a private, self-hosted multi-agent AI assistant: eight specialist agents, "
            "persistent memory, proactive watchers, desktop and Android clients."
        ),
        keywords=(
            "SPEDA Mark VI, SPEDA, Specialized Personal Executive Digital Assistant, multi-agent AI "
            "assistant, self-hosted AI assistant, proactive AI assistant, Heartbreaker, SPEDA GO, Igor, "
            "The Legion, House Party Protocol, personal AI agent"
        ),
        body=body,
        nav="",
        rig=True,
        priority="1.0",
        changefreq="weekly",
        og_type="website",
        jsonld=[
            {
                "@type": "WebSite",
                "@id": f"{BASE}/#website",
                "url": url(""),
                "name": "SPEDA Mark VI",
                "alternateName": ["S.P.E.D.A. Mark VI", "SPEDA", "Specialized Personal Executive Digital Assistant"],
                "inLanguage": "en",
                "publisher": {"@id": f"{BASE}/#author"},
            },
            {
                "@type": "SoftwareApplication",
                "@id": f"{BASE}/#app",
                "name": "SPEDA Mark VI",
                "alternateName": "Specialized Personal Executive Digital Assistant Mark VI",
                "applicationCategory": "BusinessApplication",
                "applicationSubCategory": "AI personal assistant",
                "operatingSystem": "Windows, macOS, Linux, Android",
                "url": url(""),
                "downloadUrl": REPO,
                "softwareVersion": "Mark VI",
                "author": {"@id": f"{BASE}/#author"},
                "description": (
                    "A private, proactive, self-hosted multi-agent AI assistant. Eight domain-specialist "
                    "agents share one persistent memory of a single owner, run their own watchers, dispatch "
                    "work to each other, and answer through a holographic desktop client, a native Android "
                    "client and Telegram."
                ),
                "featureList": [
                    "Eight specialist AI agents with individual identities and model policies",
                    "Closed eight-file structured memory with a dedicated custodian agent",
                    "Episodic session recaps and semantic vector recall",
                    "Proactive watchers gated by zero-token probes",
                    "Provider-agnostic routing across Anthropic, OpenAI, Gemini, GLM, DeepSeek, NVIDIA NIM and Ollama",
                    "The Legion parallel sub-agent worker system",
                    "House Party Protocol full-roster mode",
                    "Detached turns that survive client disconnects",
                    "Holographic desktop command deck and native Android client",
                    "Self-hosted with single-key authentication",
                ],
            },
            {
                "@type": "SoftwareSourceCode",
                "@id": f"{BASE}/#code",
                "name": "speda-mark6",
                "description": (
                    "Monorepo for SPEDA Mark VI: the Igor FastAPI backend, the Heartbreaker desktop client, "
                    "the SPEDA GO Android client, the Striker single-agent build and the isolated sandbox."
                ),
                "codeRepository": REPO,
                "programmingLanguage": ["Python", "TypeScript", "Kotlin"],
                "runtimePlatform": ["FastAPI", "Electron", "React", "Jetpack Compose"],
                "author": {"@id": f"{BASE}/#author"},
                "about": {"@id": f"{BASE}/#app"},
            },
        ],
    )


# ── FAQ ──────────────────────────────────────────────────────────────────────

FAQ = [
    ("What is SPEDA Mark VI?",
     "SPEDA Mark VI is a private, self-hosted, proactive multi-agent AI assistant built for a single owner. "
     "Eight domain-specialist agents share one persistent memory, one event loop and one capability registry, "
     "run their own watchers over the web and the owner's accounts, and answer through a holographic desktop "
     "client called Heartbreaker, a native Android client called SPEDA GO, and Telegram.",
     'SPEDA Mark VI is a private, self-hosted, proactive multi-agent AI assistant built for a single owner. '
     'Eight domain-specialist agents share one persistent memory, one event loop and one capability registry, '
     'run their own watchers, and answer through <a href="../heartbreaker/">Heartbreaker</a> on desktop, '
     '<a href="../speda-go/">SPEDA GO</a> on Android, and Telegram.'),

    ("What does SPEDA stand for?",
     "SPEDA stands for Specialized Personal Executive Digital Assistant. Mark VI is the sixth generation; "
     "earlier generations were published as speda-mark1 through speda-mark5.",
     "<strong>Specialized Personal Executive Digital Assistant.</strong> Mark VI is the sixth generation; "
     "earlier generations were published as <code>speda-mark1</code> through <code>speda-mark5</code>."),

    ("Which agents are in SPEDA Mark VI?",
     "Eight: SPEDA (Chief of Staff), Sentinel (finance and budget), NightCrawler (OSINT and web surveillance), "
     "Ultron (academic life and university-work balance), Centurion (cyber security), Atomix (the owner's "
     "personal health), Optimus (systems, code and infrastructure) and Orion (maintenance and memory custodian). "
     "Six run purely in-process; Optimus and Centurion can additionally be backed by an external execution peer.",
     'Eight — <a href="../agents/speda/">SPEDA</a>, <a href="../agents/sentinel/">Sentinel</a>, '
     '<a href="../agents/nightcrawler/">NightCrawler</a>, <a href="../agents/ultron/">Ultron</a>, '
     '<a href="../agents/centurion/">Centurion</a>, <a href="../agents/atomix/">Atomix</a>, '
     '<a href="../agents/optimus/">Optimus</a> and <a href="../agents/orion/">Orion</a>. Six run purely '
     'in-process as agent profiles; Optimus and Centurion can additionally be backed by an external '
     'execution peer. <a href="../agents/">Full roster →</a>'),

    ("Is SPEDA Mark VI self-hosted, and where does my data go?",
     "Yes. It runs entirely on hardware you control. There are no accounts and no tenancy; authentication is a "
     "single API key validated in constant time on every request. Conversations, memory and generated files stay "
     "in your own database and filesystem. The only data leaving your machine is what you send to whichever model "
     "provider you configure, and routing to a local Ollama model avoids even that.",
     "Yes. It runs entirely on hardware you control. There are no accounts and no tenancy; authentication is a "
     "single API key validated in constant time on every request. Conversations, memory and generated files stay "
     "in your own database and filesystem. The only data leaving your machine is what you send to whichever model "
     "provider you configure — and routing to a local Ollama model avoids even that."),

    ("Which AI models does SPEDA Mark VI support?",
     "Seven providers through one router: Anthropic, OpenAI, Google Gemini, z.ai (GLM), DeepSeek, NVIDIA NIM and "
     "local Ollama. Model references are provider:model strings, you can pin a different model per agent from the "
     "UI, and a fallback chain retries the next provider on failure. Translation happens only at the wire "
     "boundary — internally everything speaks one content-block format.",
     "Seven providers through one router: <strong>Anthropic, OpenAI, Google Gemini, z.ai (GLM), DeepSeek, NVIDIA "
     "NIM and local Ollama</strong>. Model references are <code>provider:model</code> strings, you can pin a "
     "different model per agent from the UI, and a fallback chain retries the next provider on failure. "
     "Translation happens only at the wire boundary — internally everything speaks one content-block format."),

    ("Is SPEDA Mark VI open source?",
     "The source is publicly readable on GitHub, but it is a private project and is not licensed for "
     "redistribution. You can read the code, the architecture and the documentation.",
     "The source is publicly readable on GitHub, but it is a private project and is "
     "<strong>not licensed for redistribution</strong>. You are welcome to read the code, the architecture "
     "and the documentation."),

    ("What makes it 'proactive'?",
     "Watchers run on an external scheduler and reach the owner without being asked. The design constraint is "
     "cost: a watcher that fires a full agentic turn every tick would spend around 144 turns a day answering "
     "'nothing happened'. So each watcher is split into a deterministic zero-token probe and a trigger that only "
     "fires on a hit, with acknowledgement committed last so a failed notification repeats rather than vanishes.",
     'Watchers run on an external scheduler and reach the owner without being asked. The design constraint is '
     'cost: firing a full agentic turn every tick would spend roughly <strong>144 turns a day answering '
     '"nothing happened"</strong>. So each watcher splits into a deterministic zero-token probe and a trigger '
     'that only fires on a hit. <a href="../systems/automations/">The automation layer →</a>'),

    ("What is The Legion?",
     "The sub-agent worker system. When a job needs genuine fan-out, an agent deploys anonymous single-purpose "
     "workers: a scout triages sources, researchers deep-dive one subtopic each in parallel, an analyst "
     "synthesises and a judge audits the draft claim by claim. Workers run on the cheap tier of whatever provider "
     "the parent turn is already using, have no identity and no memory, and cannot spawn further workers.",
     'The sub-agent worker system: a <em>scout</em> triages, <em>researchers</em> take one subtopic each in '
     'parallel, an <em>analyst</em> synthesises and a <em>judge</em> audits the draft claim by claim. Workers '
     'run on the cheap tier of whatever provider the parent turn already uses, have no identity and no memory, '
     'and cannot spawn further workers. <a href="../systems/legion/">The Legion →</a>'),

    ("What is the House Party Protocol?",
     "An all-hands mode that runs every in-process agent at once at full interactive model grade with domain "
     "boundaries relaxed. It is passphrase-gated and deliberately expensive, so it defaults to off, and while "
     "engaged the client becomes a War Room dashboard until the owner stands it down.",
     'An all-hands mode running every in-process agent at once at full interactive model grade with domain '
     'boundaries relaxed. Passphrase-gated and deliberately expensive, so it defaults to off. '
     '<a href="../systems/house-party/">How it engages →</a>'),

    ("What do I need to run it?",
     "Python 3.11 or newer, Node.js, and an API key for at least one model provider. It defaults to SQLite, so no "
     "database service is required for a first run. The full stack — Postgres, the application, an isolated "
     "sandbox and n8n for scheduling — comes up with a single docker compose command, and the production target "
     "is a modest cloud VPS behind Caddy with automatic TLS.",
     "Python 3.11 or newer, Node.js, and an API key for at least one model provider. It defaults to SQLite, so a "
     "first run needs no database service. The full stack — Postgres, the app, an isolated sandbox and n8n — "
     "comes up with a single <code>docker compose up -d</code>, and production is a modest cloud VPS behind "
     "Caddy with automatic TLS."),

    ("How is it different from ChatGPT or Claude?",
     "Three differences. It is proactive: watchers fire on their own schedule and message you unprompted. It is "
     "staffed: eight specialists with separate domains and doctrines dispatch work to each other instead of one "
     "general model doing everything. And it is yours: self-hosted and single-owner, with a persistent structured "
     "memory of you that no vendor holds. It is also not a replacement for a frontier model — it routes to one.",
     "Three differences. It is <strong>proactive</strong> — watchers fire on their own schedule and message you "
     "unprompted. It is <strong>staffed</strong> — eight specialists with separate domains and doctrines "
     "dispatch to each other rather than one general model doing everything. And it is <strong>yours</strong> — "
     "self-hosted, single-owner, with a structured memory no vendor holds. It is not a replacement for a "
     "frontier model; it <em>routes</em> to one."),

    ("Who built SPEDA Mark VI?",
     "SPEDA Mark VI was built by Ahmet Erol Bayrak, who publishes on GitHub as spedatox. It is the sixth "
     "generation of a project that began with speda-mark1.",
     'Built by <strong>Ahmet Erol Bayrak</strong>, who publishes on GitHub as '
     '<a href="https://github.com/spedatox">@spedatox</a>. It is the sixth generation of a project that began '
     'with <code>speda-mark1</code>.'),
]


def faq_page() -> Page:
    items = "\n".join(
        f'      <div class="faq-item"><h3>{q}</h3><p>{html}</p></div>'
        for q, _plain, html in FAQ
    )
    body = f"""
<section class="hero" style="min-height:auto;padding:6rem 0 2rem">
  <div class="wrap">
    <p class="crumb"><a href="../">SPEDA Mark VI</a><span>/</span></p>
    <p class="eyebrow rise">Frequently asked questions</p>
    <h1 class="rise">SPEDA Mark VI,<br>answered</h1>
    <p class="lede rise">Direct answers to what people actually ask — what it is, who runs on it, where the
      data lives, and what it takes to stand one up.</p>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="prose" style="max-width:none">
{items}
    </div>
    <div class="actions">
      <a class="btn btn-primary" href="{REPO}">Read the source</a>
      <a class="btn" href="../agents/">Meet the roster</a>
      <a class="btn" href="../systems/">The systems</a>
    </div>
  </div>
</section>
"""
    return Page(
        slug="faq",
        title="SPEDA Mark VI FAQ — What It Is and How It Works",
        description=(
            "Answers about SPEDA Mark VI: what SPEDA stands for, the eight agents, self-hosting and privacy, "
            "supported AI models, licensing, and what you need to run it."
        ),
        keywords=(
            "what is SPEDA Mark VI, what does SPEDA stand for, SPEDA Mark VI FAQ, self-hosted AI assistant "
            "privacy, SPEDA Mark VI license, SPEDA Mark VI requirements"
        ),
        body=body,
        nav="faq",
        crumbs=[("faq", "FAQ")],
        jsonld=[{
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q,
                 "acceptedAnswer": {"@type": "Answer", "text": plain}}
                for q, plain, _html in FAQ
            ],
        }],
    )
