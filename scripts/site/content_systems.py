"""The systems hub, the eight subsystem pages, and the three client pages.

Written from the implementation: app/legion/roster.py, app/core/{dispatch,
turn_runner,trigger_runner,runtime_state}.py, app/skills/navigation.py,
docs/MEMORY_ARCHITECTURE.md, docs/TELEGRAM_ARCHITECTURE.md, CLAUDE.md and
packages/heartbreaker/HEARTBREAKER.md.
"""

from build import BASE, REPO, Page, url


def shell(*, crumbs_html: str, eyebrow: str, h1: str, lede: str,
          doctrine: str = "", cite: str = "", body: str = "") -> str:
    d = f'<div class="doctrine rise">{doctrine}<cite>{cite}</cite></div>' if doctrine else ""
    return f"""
<section class="hero" style="min-height:auto;padding:6rem 0 2.5rem">
  <div class="wrap">
    {crumbs_html}
    <p class="eyebrow rise">{eyebrow}</p>
    <h1 class="rise">{h1}</h1>
    <p class="lede rise">{lede}</p>
    {d}
  </div>
</section>
{body}
"""


SYS_CRUMB = '<p class="crumb"><a href="../../">Speda Mark VI</a><span>/</span><a href="../">Systems</a><span>/</span></p>'
TOP_CRUMB = '<p class="crumb"><a href="../">Speda Mark VI</a><span>/</span></p>'


def _sys(slug, *, accent, title, meta, keywords, eyebrow, h1, crumb, lede, doctrine, cite, body,
         jsonld_kind="TechArticle", priority="0.8") -> Page:
    return Page(
        slug=f"systems/{slug}",
        title=title,
        description=meta,
        keywords=keywords + ", Speda Mark VI",
        body=shell(crumbs_html=SYS_CRUMB, eyebrow=eyebrow, h1=h1, lede=lede,
                   doctrine=doctrine, cite=cite, body=body),
        nav="systems",
        accent=accent,
        crumbs=[("systems", "Systems"), (f"systems/{slug}", crumb)],
        priority=priority,
        jsonld=[{
            "@type": jsonld_kind,
            "headline": title,
            "description": meta,
            "url": url(f"systems/{slug}"),
            "author": {"@id": f"{BASE}/#author"},
            "about": {"@type": "SoftwareApplication", "name": "Speda Mark VI", "url": url("")},
            "proficiencyLevel": "Expert",
        }],
    )


# ═══ THE LEGION ══════════════════════════════════════════════════════════════

LEGION_BODY = """
<section>
  <div class="wrap">
    <div class="prose">
      <h3>Five workers, and a tool that argues against itself</h3>
      <p>The Legion is registered as <strong>tier zero</strong> — before skills, before MCP servers, before
        adapters. Its wire name is <code>Task</code>, because models already know that delegation pattern by
        that name; The Legion is the branding the descriptions, prompts and logs carry.</p>
      <p>Its tool description is unusual in that most of it is spent talking the model <em>out</em> of using
        it. Deploy only when the owner explicitly asked for a deep report <em>and</em> it genuinely needs six or
        more independent searches across distinct subtopics. Never for news, current events, quick facts,
        lookups, writes or reminders. Running several searches yourself is stated as preferred over deploying a
        worker at all. When in doubt: do not deploy.</p>
      <p>Each legionnaire is <strong>data, not code</strong> — a role prompt, an effort level, an iteration
        budget and a tool scope. Adding a worker type means adding an entry; the runner and the tool definition
        pick it up automatically.</p>
    </div>

    <div class="table-scroll" style="margin-top:2.4rem">
      <table>
        <thead><tr><th>Legionnaire</th><th>Effort</th><th>Iterations</th><th>What it is for</th></tr></thead>
        <tbody>
          <tr><td><strong>scout</strong></td><td>low</td><td>6</td><td>Cheap triage. Surveys sources fast and returns a ranked shortlist of leads — no deep reading, no synthesis, no conclusions. A heavier worker follows it.</td></tr>
          <tr><td><strong>researcher</strong></td><td>medium</td><td>15</td><td>The default for fan-out. Takes exactly one subtopic, investigates across multiple independent sources, cross-checks, and returns dense findings with a URL for every non-obvious claim.</td></tr>
          <tr><td><strong>analyst</strong></td><td>high</td><td>20</td><td>Synthesis. Receives the raw findings of prior workers and produces the finished structured section with a clear line of argument, preserving their attributions.</td></tr>
          <tr><td><strong>judge</strong></td><td>low</td><td>8</td><td>Verification only. Checks each substantive claim against its source and returns a per-claim verdict — confirmed, wrong with the correction, or unverifiable. It audits; it does not rewrite.</td></tr>
          <tr><td><strong>general</strong></td><td>inherit</td><td>15</td><td>Anything that does not fit a specialist. Full parent toolset, parent-grade model, self-contained task.</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="split">
      <div class="prose" style="max-width:none">
        <h3>Never cross providers on the engine's own initiative</h3>
        <p>This is the part most sub-agent systems get wrong. A cheap worker tier is usually a hardcoded model
          ID, which silently drags background work back onto one vendor no matter what the owner routed the
          conversation to.</p>
        <p>Here, resolution is provider-relative and runs in a fixed priority order: a deployment-wide override
          first, then the owner's per-legionnaire pin from the UI, then an explicit tool parameter, then effort.
          Low and medium effort resolve to <strong>the cheap tier of the same provider the parent turn is
          running on</strong> — an Anthropic parent gets Haiku, a GLM parent gets GLM Air. High and inherited
          effort take the parent model unchanged.</p>
        <p>The owner's pin deliberately beats the model's own choice: it is a cost and quality policy, not a
          per-call hint.</p>
      </div>
      <div class="holo pad-lg">
        <h3 class="card-title">Hard limits</h3>
        <ul class="prose" style="margin:1rem 0 0">
          <li><strong>No recursion.</strong> Workers cannot see the delegation tool, so they cannot spawn
            workers of their own.</li>
          <li><strong>No persona.</strong> The dispatch tools are withheld too — an anonymous worker must never
            talk like a roster member.</li>
          <li><strong>Result cap.</strong> A worker that returns a novel defeats the point of context
            isolation, so output is truncated to a fixed budget.</li>
          <li><strong>Fan-out cap.</strong> Concurrent background workers are limited, as a runaway guard.</li>
          <li><strong>Read-only by default</strong> for scout, researcher and judge, scoped to the research
            surfaces.</li>
        </ul>
      </div>
    </div>

    <div class="prose" style="margin-top:clamp(2.6rem,5vw,4rem)">
      <h3>Isolation is the point</h3>
      <p>Every worker is fully isolated: it sees <em>nothing</em> of the parent conversation, which is exactly
        why the deploying agent must write a self-contained prompt. The worker's result returns only to the
        agent that deployed it — never straight to the owner — and the agent is required to summarise it and
        name which legionnaires ran, one sentence per worker.</p>
      <p>Deployed with a background flag, a worker returns a ticket immediately and lands its findings in the
        comms tray when it is done, so a long research job never blocks the conversation.</p>
      <p>Budget mode stands the entire Legion down.</p>
    </div>

    <div class="actions">
      <a class="btn btn-primary" href="{repo}/blob/main/packages/igor/app/legion/roster.py">roster.py on GitHub</a>
      <a class="btn" href="../capabilities/">The four capability tiers</a>
    </div>
  </div>
</section>
""".replace("{repo}", REPO)


# ═══ MEMORY ══════════════════════════════════════════════════════════════════

MEMORY_BODY = """
<section>
  <div class="wrap">
    <div class="prose">
      <h3>The problem it was written to fix</h3>
      <p>Version one of this memory system worked, and then eroded exactly the way filing systems do. Facts
        drifted between files — active work lingering in history, project detail leaking into the owner profile.
        Nothing <em>owned</em> hygiene, so cleanup happened only when some agent happened to notice, mid
        conversation, with conversation-priority attention. The owner could <em>see</em> memory but not correct
        it, so errors persisted until an agent stumbled on them. And new file types were added ad hoc with no
        declared schema, which accelerated the blur.</p>
      <p>Version two fixes it with three moves: a <strong>strict closed taxonomy</strong> with one question per
        file, a <strong>dedicated custodian agent</strong> whose entire job is maintenance, and an
        <strong>owner write path</strong> with audit and precedence rules.</p>
    </div>

    <div class="table-scroll" style="margin-top:2.4rem">
      <table>
        <thead><tr><th>File</th><th>Temporal nature</th><th>The one question it answers</th><th>In context</th></tr></thead>
        <tbody>
          <tr><td><code>current.md</code></td><td>Volatile present</td><td>What is true in the owner's life right now?</td><td>Always</td></tr>
          <tr><td><code>owner.md</code></td><td>The prior</td><td>Who is he, and what shaped him before Mark VI existed?</td><td>Always</td></tr>
          <tr><td><code>dossier.md</code></td><td>Observed preferences</td><td>What has he been observed to like, dislike and want — and in what manner?</td><td>Always</td></tr>
          <tr><td><code>history.md</code></td><td>Mark VI-era ledger</td><td>What happened on Mark VI's watch that no longer applies?</td><td>Always</td></tr>
          <tr><td><code>projects.md</code></td><td>Active ledger</td><td>What is he building, and where does each effort stand?</td><td>On demand</td></tr>
          <tr><td><code>social.md</code></td><td>People registry</td><td>Who matters to him, who ARE they to him, and what is the latest?</td><td>On demand</td></tr>
          <tr><td><code>sessions.md</code></td><td>Training log</td><td>What happened in the gym, day by day?</td><td>Atomix only</td></tr>
          <tr><td><code>log.md</code></td><td>System trail</td><td>Rolling one-line session summaries.</td><td>On demand</td></tr>
        </tbody>
      </table>
    </div>
    <p style="margin-top:1rem;color:var(--fg-faint);font-size:0.9rem">Creating any other top-level file is a
      protocol violation. Strays are merged into the canonical set and deleted.</p>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="split">
      <div class="prose" style="max-width:none">
        <h3>One rule settles every conflict</h3>
        <p><strong><code>current.md</code> outranks every other file for the present tense.</strong> That single
          precedence rule is what makes the rest tractable. When the historical ledger says one thing about the
          owner's job and the present-tense file says another, current wins — and the loser is
          <strong>demoted, not deleted</strong>, moving to history with its dates intact.</p>
        <p>The present-tense file is deliberately small: a handful of bullets, each date-stamped, covering only
          genuinely active states — location, employment, training regimen, immediate milestones, live concerns.
          A file that grows without bound stops disambiguating anything.</p>
        <p>Every file carries a two-line header the custodian maintains, recording when it was last audited and
          last written, and by whom.</p>
      </div>
      <div class="prose" style="max-width:none">
        <h3>Recall is tiered, not one search</h3>
        <p>Structured files are only the base layer. Above them sit three distinct recall mechanisms, because
          "what did we decide last week" and "have I ever mentioned this library" are different questions:</p>
        <ul>
          <li><strong>Episodic recaps.</strong> Every session grows a short background summary — subject,
            decisions, open threads. Opening a <em>new</em> chat pre-loads the last few recaps for that agent,
            so "what were we discussing?" is answered without a search.</li>
          <li><strong>Semantic recall</strong> over embeddings of every past conversation, for the deep cuts.</li>
          <li><strong>Literal search</strong> by keyword and date over stored messages, for when you know the
            exact phrase.</li>
        </ul>
        <p>All of it is scoped per agent. Specialists recall their own threads; the orchestrator sees across
          all of them.</p>
      </div>
    </div>

    <div class="prose" style="margin-top:clamp(2.6rem,5vw,4rem)">
      <h3>The custodian, and the one thing it may not do</h3>
      <p><a href="../../agents/orion/">Orion</a> exists because hygiene as a shared responsibility is hygiene
        nobody performs. It moves, merges, timestamps and compresses <em>existing</em> memory, runs a periodic
        audit, and reports in changelogs rather than prose.</p>
      <p>Its prompt names the prohibition explicitly: it does <strong>not author new facts</strong> about the
        owner. Inventing memory is identified as the one failure that would make a custodian worse than
        useless. If a fact is not already in memory or in what was actually said, it does not exist to Orion.</p>
      <p>The owner also gets a write path — memory is editable and auditable from the client rather than being
        a black box the agents alone can correct.</p>
    </div>

    <div class="actions">
      <a class="btn btn-primary" href="{repo}/blob/main/docs/MEMORY_ARCHITECTURE.md">The full contract</a>
      <a class="btn" href="../../agents/orion/">Orion, the custodian</a>
    </div>
  </div>
</section>
""".replace("{repo}", REPO)


# ═══ AUTOMATIONS ═════════════════════════════════════════════════════════════

AUTOMATIONS_BODY = """
<section>
  <div class="wrap">
    <div class="prose">
      <h3>The arithmetic that shapes the design</h3>
      <p>A watcher that fires a full agentic turn on every tick spends real money to discover that nothing
        happened. At a ten-minute cadence that is roughly <strong>144 turns a day to answer "no"</strong>. Run
        five watchers and the assistant costs more to say nothing than it does to work.</p>
      <p>So every watcher is split in two, and the split is a rule rather than an optimisation.</p>
    </div>

    <div class="grid g2" style="margin-top:2.2rem">
      <article class="holo pad-lg">
        <h3 class="card-title">The probe</h3>
        <p class="card-body">A plain endpoint answering one deterministic question: did anyone from this domain
          write, did a new line appear on this page, did a result get published. <strong>One HTTP call. Zero
          tokens.</strong> A probe holds no reasoning at all — if answering needs judgement, it belongs in the
          turn, not the probe.</p>
      </article>
      <article class="holo pad-lg">
        <h3 class="card-title">The trigger</h3>
        <p class="card-body">The full agentic turn, reached only when the probe returned a hit. It costs what a
          turn costs — which is now paid only on the days something actually happened.</p>
      </article>
    </div>

    <div class="prose" style="margin-top:clamp(2.6rem,5vw,4rem)">
      <h3>Four rules that keep it honest</h3>
      <ul>
        <li><strong>Exactly-once is the probe's job, and it commits last.</strong> A scan never marks its own
          findings as handled. The acknowledgement happens only <em>after</em> the trigger was accepted, so a
          failed notification repeats on the next poll instead of vanishing. A duplicate push is recoverable; a
          swallowed exam result is not.</li>
        <li><strong>Give the agent the data it already cost you to fetch.</strong> The probe's findings ride
          along in the trigger payload and the intent says so explicitly, so the turn does not re-fetch — and
          re-pay for — what the probe just read.</li>
        <li><strong>Probes authenticate twice.</strong> They read the owner's mail and browsing targets, so
          they require the API key <em>and</em> the scheduler's shared secret. The poller has to prove it is
          the poller.</li>
        <li><strong>Health failures are reported on the edge.</strong> A revoked token produces one message,
          not one every ten minutes.</li>
      </ul>

      <h3>An automated turn is a chat turn</h3>
      <p>The trigger endpoint does not run a private loop of its own. It opens a session, saves the
        automation's seed as a <em>real user message</em>, loads it back through normal history — which is the
        only way any agent knows what day it is — and launches the run on the same detached turn registry that
        chat uses. The consequences are all the good kind:</p>
      <ul>
        <li>The briefing is readable in the app afterwards, with the agent's tool calls intact.</li>
        <li>The opening turn is attributed to the <strong>trigger</strong> rather than to the owner, and the
          client renders it as such.</li>
        <li>The session is titled from the automation at launch — "Morning brief · 26 Jul" — instead of sitting
          on "New conversation".</li>
        <li>A running briefing can be tailed live, or cancelled.</li>
        <li>Post-turn work — session log, recap, compaction, embeddings — runs exactly as it does for chat.</li>
        <li>Delivery happens on settle, so a run that errors halfway still sends what it produced, carrying the
          early-exit marker.</li>
      </ul>
      <p>The one deliberate difference is model tier: automated turns run at background grade unless the owner
        has pinned that agent higher.</p>

      <h3>Composed in conversation, delivered to Telegram</h3>
      <p>The owner does not open a workflow editor. He says <em>"track this page for a month and tell me when
        the results are up"</em>, and the agent composes the watcher, arms it, and time-boxes it so it expires
        on its own. Web pages, keywords, RSS feeds, morning briefings and inbound webhooks are all the same
        shape.</p>
      <p>Delivery is per-agent: every agent has its own bot, so a finance alert arrives from the finance agent
        in its own voice rather than from a generic system account. Telegram is a full conversation channel in
        both directions — the same orchestrator, the same sessions, the same memory as the desktop app.</p>
      <p><strong>The backend never grows a scheduler.</strong> Scheduling is external, permanently. It is the
        one architectural line the project states twice.</p>
    </div>

    <div class="actions">
      <a class="btn btn-primary" href="../news/">The News Desk</a>
      <a class="btn" href="../../igor/">How turns are run</a>
    </div>
  </div>
</section>
"""


# ═══ NEWS ════════════════════════════════════════════════════════════════════

NEWS_BODY = """
<section>
  <div class="wrap">
    <div class="split">
      <div class="holo pad-lg">
        <p class="eyebrow">Tier 1</p>
        <h3 class="card-title" style="font-size:1.4rem">Always on, zero cost</h3>
        <p class="card-body">An RSS watcher across Turkish and international outlets, running continuously
          because it costs nothing to run. It deduplicates the same story across outlets, matches
          breaking-news keywords the owner has registered — "flag anything about OSTİM" — and assembles the
          daily briefing.</p>
      </div>
      <div class="holo pad-lg" style="--a: var(--sentinel)">
        <p class="eyebrow">Tier 2</p>
        <h3 class="card-title" style="font-size:1.4rem">The analyst layer, on a quota</h3>
        <p class="card-body">A paid news API used for corroboration, story timelines and historical search —
          the questions the free tier structurally cannot answer. It is quota-budgeted, and the desk spends it
          only after the free tier has failed.</p>
      </div>
    </div>

    <div class="prose" style="margin-top:clamp(2.6rem,5vw,4rem)">
      <h3>Why two tiers rather than one</h3>
      <p>A single paid source is either expensive or shallow. Splitting the desk means the constant background
        cost of <em>knowing what happened</em> is zero, and the metered budget is reserved for
        <em>understanding it</em>. Full article text is extracted for free wherever that is possible, so even
        depth often costs nothing.</p>
      <p>The desk is owned by <a href="../../agents/nightcrawler/">NightCrawler</a>, which is also why it
        inherits that agent's corroboration doctrine: a single outlet reporting something is a lead, and the
        desk is built to notice when a second one has not confirmed it.</p>

      <h3>The surface</h3>
      <ul>
        <li><strong>Headlines</strong> — read the always-on store, already deduplicated across outlets.</li>
        <li><strong>Watch</strong> — manage the breaking-news keyword list.</li>
        <li><strong>Deep dive</strong> — the metered analyst tier, for corroboration and timelines.</li>
        <li><strong>Read article</strong> — free full-text extraction from a URL.</li>
      </ul>
      <p>Collection is driven externally on a schedule, like everything else automated here, and the collection
        endpoint requires the scheduler's shared secret on top of the API key.</p>
    </div>

    <div class="actions">
      <a class="btn btn-primary" href="../../agents/nightcrawler/">NightCrawler</a>
      <a class="btn" href="../automations/">The automation layer</a>
    </div>
  </div>
</section>
"""


# ═══ FORGE ═══════════════════════════════════════════════════════════════════

FORGE_BODY = """
<section>
  <div class="wrap">
    <div class="prose">
      <h3>An agent whose engine is somewhere else</h3>
      <p>Every other agent in Mark VI is an in-process profile. <a href="../../agents/optimus/">Optimus</a> is
        the exception: its real engine is a <strong>standalone, independently deployed coding framework</strong>
        that connects back to the backend as a WebSocket peer. It is not built in this repository and it does
        not run inside the assistant's process.</p>
      <p>While that peer is online, chat turns addressed to Optimus are <strong>proxied to it</strong>, and
        inter-agent dispatches route to it external-first. It runs with a privileged shell and real code
        execution inside its own isolated <strong>Cell</strong>, understands a codebase through a graph index,
        and reads, writes, runs, tests and iterates on its own filesystem before reporting back.</p>
      <p>When it is offline, the in-process profile answers instead — same identity, same voice, reduced
        hands. <strong>There is never a hard dependency.</strong> That fallback is the entire reason the split
        is safe.</p>
    </div>

    <div class="grid g3" style="margin-top:2.4rem">
      <article class="holo pad">
        <h3 class="card-title">The Cell</h3>
        <p class="card-body">Each peer runs its own isolated container. Centurion's is a security distribution
          with outbound network for authorized scanning; the tooling never touches the host running the
          assistant.</p>
      </article>
      <article class="holo pad">
        <h3 class="card-title">Permission relay</h3>
        <p class="card-body">When the peer needs consent for something consequential, the ask is relayed back
          through the backend and surfaced to the owner in the client — an external engine does not get to
          decide on its own.</p>
      </article>
      <article class="holo pad">
        <h3 class="card-title">Visible state</h3>
        <p class="card-body">The client header carries a live engine jewel reading FORGE LINK or IN-PROCESS,
          with a workspace picker. You always know which Optimus you are talking to.</p>
      </article>
    </div>

    <div class="prose" style="margin-top:clamp(2.6rem,5vw,4rem)">
      <h3>Not only Optimus</h3>
      <p>This is worth stating because the architecture documentation describes Optimus as the single
        exception, and the code has since moved past that.
        <a href="../../agents/centurion/">Centurion</a> also declares an external backend and can be served by
        its own peer on its own socket, with the same proxy-when-online, fallback-when-offline behaviour. Two
        agents can now be externally backed; the rest remain purely in-process.</p>
      <p>Both peers are configured through the launcher rather than compiled in — where the Forge lives,
        whether to start it automatically, which socket to reach it on, and which container backend the Cell
        should use.</p>
    </div>

    <div class="actions">
      <a class="btn btn-primary" href="https://github.com/spedatox/forge-mark1">The Forge repository</a>
      <a class="btn" href="../../agents/optimus/">Optimus</a>
    </div>
  </div>
</section>
"""


# ═══ MAPS ════════════════════════════════════════════════════════════════════

MAPS_BODY = """
<section>
  <div class="wrap">
    <div class="prose">
      <h3>Never recite a coordinate</h3>
      <p>The design intent is encoded directly into the tool descriptions, so the model routes correctly without
        being reminded: <strong>never read raw coordinates to the owner.</strong> Fetch real geometry, then emit
        a map block — and the client turns that block into an inline map with a one-tap handoff to a full maps
        app for when he actually leaves.</p>
      <p>Two tools sit behind it, both read-only and network-gated. One returns A-to-B directions with
        <strong>live traffic</strong>, alternative routes and encoded polylines. The other does point-of-interest
        search for "where can I go near here". The API key lives in the skill alone; no client ever sees it.</p>

      <h3>"How do I get home from here?"</h3>
      <p>That sentence is the whole feature, and it only works because of a detail that is easy to miss: the
        route origin <strong>defaults to the owner's live position</strong>, stamped onto the turn by the chat
        router, and falls back to a configured home location when there isn't one. The assistant already knows
        which surface it is being addressed from — phone, desktop or Telegram — so it never has to ask where
        "here" is.</p>
      <p>The privacy boundary is precise, and it matters: <strong>location is stamped onto the live turn
        only.</strong> It is never written into stored history. The assistant can act on where you are right
        now without accumulating a record of everywhere you have been.</p>
    </div>

    <figure class="portrait">
      <img src="https://github.com/user-attachments/assets/f0b092e3-eb41-4ef8-bbdd-2fe3da8e1cde"
           width="738" height="1600" loading="lazy"
           alt="Speda GO rendering a traffic-aware route with alternatives on an inline map inside a conversation">
      <figcaption>Routing rendered in the conversation, not handed off to another app</figcaption>
    </figure>

    <div class="prose" style="margin-top:2.6rem">
      <h3>Part of a wider rendering contract</h3>
      <p>Maps are one case of a general rule: structured output is rendered, not described. The same mechanism
        carries inline charts and calendars, and it is documented in a prompt section that is always loaded
        precisely because no tool backs it — the model has to know the contract even when it is not calling
        anything.</p>
    </div>

    <div class="actions">
      <a class="btn btn-primary" href="../../speda-go/">Speda GO</a>
      <a class="btn" href="../capabilities/">The capability arsenal</a>
    </div>
  </div>
</section>
"""


# ═══ HOUSE PARTY ═════════════════════════════════════════════════════════════

HOUSE_BODY = """
<section>
  <div class="wrap">
    <div class="prose">
      <h3>What changes when it engages</h3>
      <ul>
        <li><strong>Broadcast is unlocked.</strong> A fan-out primitive that sends one task to every in-process
          agent at once becomes available. Outside the protocol it is <em>refused</em> — normal dispatch stays
          strictly one-to-one, forever.</li>
        <li><strong>Model policy escalates.</strong> Every agent's subtasks jump from background grade to full
          interactive grade. This is what makes the mode genuinely expensive, and it is why it defaults off.</li>
        <li><strong>The system prompt changes.</strong> An explicit active-protocol block is injected so every
          agent knows the mode is live and how to hand it back down.</li>
        <li><strong>The client transforms.</strong> The interface becomes a War Room dashboard with the whole
          roster staged, until the owner stands it down.</li>
      </ul>

      <h3>One authorization boundary, two doors</h3>
      <p>Engaging requires a passphrase, compared in constant time. There are exactly two ways in — an owner
        endpoint, and a tool the agents can call but only on the owner's explicit instruction, never at their
        own initiative. Both enforce the same gate.</p>
      <p>Standing down requires no passphrase, which is the correct asymmetry: making it easy to stop an
        expensive mode and hard to start one.</p>
      <p>The architectural rule is stated as a prohibition: <strong>do not add a third engage path.</strong>
        Any raw state write from elsewhere would bypass the only authorization boundary the protocol has.</p>

      <h3>The War Room is a scope, not an agent</h3>
      <p>Full-roster operations happen in their own conversation channel. It is the orchestrator's brain —
        identical prompts, identical tools, identical model policy — behind a distinct identifier, purely so
        that sessions scoped to it never mix into the owner's day-to-day chats.</p>
      <p>It is deliberately kept off the dispatch surface: agents dispatch to the orchestrator, never to its
        command-channel alias. It also has no bot of its own, because it is a session scope rather than
        something that notifies.</p>
      <p>State is a single persisted process-wide flag — not a session setting and not per-agent, in the same
        way budget mode is process-wide.</p>
    </div>

    <div class="actions">
      <a class="btn btn-primary" href="../../agents/speda/">Speda, the mission commander</a>
      <a class="btn" href="../../agents/">The roster it rallies</a>
    </div>
  </div>
</section>
"""


# ═══ CAPABILITIES ════════════════════════════════════════════════════════════

CAP_BODY = """
<section>
  <div class="wrap">
    <div class="prose">
      <h3>Four tiers the model cannot tell apart</h3>
      <p>Capabilities come from four structurally different places, and the model sees a single flat array.
        Only the registry knows the difference — which is what lets a capability change tier without the
        orchestrator changing at all.</p>
    </div>
    <div class="table-scroll" style="margin-top:2.2rem">
      <table>
        <thead><tr><th>Tier</th><th>What it is</th><th>When it is the right choice</th></tr></thead>
        <tbody>
          <tr><td><strong>0</strong></td><td><a href="../legion/">The Legion</a></td><td>Parallel multi-source work needing context isolation. Registered first, before everything else.</td></tr>
          <tr><td><strong>1</strong></td><td>Python skill</td><td>Logic we own, pure Python, low latency required.</td></tr>
          <tr><td><strong>2</strong></td><td>MCP server</td><td>A third-party integration that already has a Model Context Protocol server.</td></tr>
          <tr><td><strong>3</strong></td><td>OSS adapter</td><td>A whole open-source application wrapped over HTTP or a subprocess.</td></tr>
        </tbody>
      </table>
    </div>

    <div class="grid g2" style="margin-top:2.4rem">
      <article class="holo pad-lg">
        <h3 class="card-title">Research &amp; retrieval</h3>
        <p class="card-body">Multiple independent search surfaces, page fetching, academic paper search, market
          data, and a full deep-research engine wrapped as an adapter. All annotated read-only, which is what
          allows genuine parallel execution rather than serialised tool calls.</p>
        <ul class="chips"><li>tavily</li><li>exa</li><li>brave</li><li>fetch</li><li>arxiv</li><li>alpha vantage</li><li>deep_research</li></ul>
      </article>
      <article class="holo pad-lg" style="--a: var(--centurion)">
        <h3 class="card-title">OSINT &amp; security</h3>
        <p class="card-body">A twelve-tool intelligence surface plus CVE intelligence and a wrapped security
          analysis engine — reputation, malware corpora, breach data, exposure search and chain tracing.</p>
        <ul class="chips"><li>ip_geolocate</li><li>ip_reputation</li><li>urlhaus</li><li>threatfox</li><li>malwarebazaar</li><li>pwned_password</li><li>darkweb_search</li><li>otx</li><li>shodan</li><li>email_discovery</li><li>crypto_trace</li><li>intelx</li></ul>
      </article>
      <article class="holo pad-lg" style="--a: var(--atomix)">
        <h3 class="card-title">Creation &amp; execution</h3>
        <p class="card-body">Branded A4 documents in three formats with Turkish-ready fonts, file authoring
          delivered as download cards, and a real shell in an isolated Linux container with a persistent
          workspace.</p>
        <ul class="chips"><li>generate_document</li><li>save_file</li><li>deliver_file</li><li>run_command</li><li>text_to_speech</li><li>speech_to_text</li></ul>
      </article>
      <article class="holo pad-lg" style="--a: var(--nightcrawler)">
        <h3 class="card-title">Productivity &amp; network</h3>
        <p class="card-body">Notes, mail and calendar over self-refreshing OAuth, source control, plus the
          inter-agent surface: direct dispatch, background dispatch with tickets, the shared agent transcript,
          and the protocol switch.</p>
        <ul class="chips"><li>notion</li><li>gmail</li><li>calendar</li><li>github</li><li>dispatch_agent</li><li>dispatch_status</li><li>read_agent_channel</li><li>house_party</li></ul>
      </article>
    </div>

    <div class="prose" style="margin-top:clamp(2.6rem,5vw,4rem)">
      <h3>Why a huge toolset costs almost nothing</h3>
      <p>Carrying every tool definition in every request would be the obvious way to build this, and it would
        be ruinous. Three mechanisms stop that:</p>
      <ul>
        <li><strong>Progressive disclosure.</strong> Only always-on servers occupy the prompt prefix. Everything
          else is mounted at runtime when a tool is actually needed.</li>
        <li><strong>Long-lived prompt caching.</strong> The static prefix is written once and read for an hour,
          so the fixed cost is paid once rather than per turn.</li>
        <li><strong>Documentation on demand.</strong> Full skill guides are loaded only when the model asks for
          them, instead of shipping every manual in every request.</li>
      </ul>
      <p>A single budget-mode switch clamps the whole thing down: it stands the Legion off and injects a
        concise-output directive.</p>

      <h3>The rule that makes tools usable at all</h3>
      <p>Every tool description must be at least three or four sentences, and must state four things: what the
        tool does, when to use it, when <em>not</em> to, and what it returns. This is enforced when the skill is
        written, not at runtime. A one-line description makes a good tool unusable, because selection accuracy
        is a function of description quality more than anything else.</p>
      <p>Adding a capability means dropping a file and registering it at startup. The orchestrator never
        changes — that is the whole point of the registry.</p>
    </div>

    <div class="actions">
      <a class="btn btn-primary" href="../../igor/">Inside Igor</a>
      <a class="btn" href="{repo}/blob/main/docs/REFERENCE.md">The full catalog</a>
    </div>
  </div>
</section>
""".replace("{repo}", REPO)


def system_pages() -> list[Page]:
    return [
        _sys("legion", accent="nightcrawler",
             title="The Legion — Sub-Agent Workers in Speda Mark VI",
             meta="The Legion is the sub-agent worker system of Speda Mark VI: scout, researcher, analyst, judge and general, run on the parent model's own cheap tier.",
             keywords="The Legion, AI sub-agents, parallel research agents, multi-agent fan-out, sub-agent worker system",
             eyebrow="System 01 — The Legion",
             h1="The Legion",
             crumb="The Legion",
             lede="Disposable, anonymous, memoryless workers deployed for a single task and discarded. They are "
                  "not the roster — they are the grunts, and the system is built so they stay that way.",
             doctrine="Each worker is fully isolated: it sees nothing of the conversation, so the prompt must be "
                      "self-contained, and its result returns only to the agent that deployed it.",
             cite="app/legion/roster.py",
             body=LEGION_BODY, priority="0.85"),

        _sys("memory", accent="orion",
             title="Memory & Recall — How Speda Mark VI Remembers",
             meta="Speda Mark VI's memory is a closed set of eight Markdown files, one question each, enforced by a custodian agent, plus episodic recaps and semantic recall.",
             keywords="AI assistant memory architecture, persistent AI memory, agent memory hygiene, episodic recall, semantic conversation recall",
             eyebrow="System 02 — Memory",
             h1="Memory that<br>does not rot",
             crumb="Memory & recall",
             lede="Eight files. One question each. One agent whose entire job is keeping them true — and one "
                  "precedence rule that settles every conflict between them.",
             doctrine="current.md outranks every other file for the present tense. A state that ends is not "
                      "deleted — it is demoted, with its dates intact.",
             cite="docs/MEMORY_ARCHITECTURE.md — the file law",
             body=MEMORY_BODY, priority="0.85"),

        _sys("automations", accent="sentinel",
             title="Proactive Automations — Speda Mark VI Watchers",
             meta="How Speda Mark VI watches the world without burning turns: deterministic zero-token probes gate every trigger, and an automated turn is a real chat turn.",
             keywords="proactive AI assistant, AI watchers, n8n AI automation, scheduled AI agent, cheap probe pattern",
             eyebrow="System 03 — Automations",
             h1="A poll must<br>not cost a turn",
             crumb="Automations",
             lede="Watchers on pages, feeds, markets, mail and schedules that reach the owner unprompted — "
                  "designed around the one number that decides whether proactivity is affordable at all.",
             doctrine="If answering needs judgement, it belongs in the turn, not the probe.",
             cite="CLAUDE.md — cheap probes",
             body=AUTOMATIONS_BODY, priority="0.85"),

        _sys("news", accent="nightcrawler",
             title="The News Desk — Two-Tier Intelligence in Speda Mark VI",
             meta="The Speda Mark VI News Desk pairs an always-on zero-cost RSS layer with deduplication and keyword alerts against a quota-budgeted analyst tier.",
             keywords="AI news briefing, RSS monitoring AI, automated news digest, breaking news alerts AI agent",
             eyebrow="System 04 — The News Desk",
             h1="The News Desk",
             crumb="The News Desk",
             lede="A professional two-tier news operation built into the assistant, so that knowing what "
                  "happened costs nothing and understanding it costs only what it must.",
             doctrine="It reads full articles for free wherever it can, and spends the paid tier only when the "
                      "free one cannot answer.",
             cite="app/news/ — the collection pipeline",
             body=NEWS_BODY),

        _sys("forge", accent="optimus",
             title="The Forge — The External Execution Engine of Speda Mark VI",
             meta="The Forge is the standalone execution engine behind Optimus in Speda Mark VI: a privileged shell in an isolated cell, connected back as a WebSocket peer.",
             keywords="agentic coding engine, AI code execution sandbox, external agent peer, The Forge Speda",
             eyebrow="System 05 — The Forge",
             h1="The Forge",
             crumb="The Forge",
             lede="The one part of Mark VI that is not Mark VI: a separately deployed execution framework that "
                  "connects back as a peer and gives an agent real hands on real machines.",
             doctrine="When the peer is offline the in-process profile answers instead. There is never a hard "
                      "dependency — which is the only reason the split is safe.",
             cite="app/core/external_proxy.py",
             body=FORGE_BODY),

        _sys("maps", accent="speda",
             title="Maps & Navigation — Inline Routing in Speda Mark VI",
             meta="Speda Mark VI renders traffic-aware routes inside the conversation, defaulting the origin to your live position — never writing it into stored history.",
             keywords="AI assistant maps, traffic aware routing AI, inline map rendering, location aware AI assistant",
             eyebrow="System 06 — Cartography",
             h1="Maps, inside<br>the conversation",
             crumb="Maps & navigation",
             lede="Ask for directions and get a live map rather than a wall of coordinates — with the origin "
                  "already known, and without accumulating a record of where you have been.",
             doctrine="Location is stamped onto the live turn only. It is never written into stored history.",
             cite="app/skills/navigation.py",
             body=MAPS_BODY),

        _sys("house-party", accent="warroom",
             title="House Party Protocol — All-Hands Mode in Speda Mark VI",
             meta="House Party Protocol runs the entire Speda Mark VI roster in parallel at full model grade with boundaries relaxed — passphrase-gated, and deliberately expensive.",
             keywords="House Party Protocol, multi-agent broadcast, all-hands AI mode, War Room AI dashboard",
             eyebrow="System 07 — House Party Protocol",
             h1="All hands",
             crumb="House Party Protocol",
             lede="For genuinely high-stakes moments the orchestrator becomes mission commander and summons the "
                  "entire roster at once. It is heavy on purpose, and it is locked behind a passphrase for the "
                  "same reason.",
             doctrine="Broadcast is refused outside the protocol. Normal dispatch stays strictly direct, "
                      "one-to-one — there is no ambient fan-out.",
             cite="app/core/dispatch.py",
             body=HOUSE_BODY),

        _sys("capabilities", accent="centurion",
             title="The Capability Arsenal of Speda Mark VI",
             meta="The four capability tiers of Speda Mark VI, presented to the model as one array: Python skills, MCP servers, wrapped open-source engines and the Legion.",
             keywords="AI agent tools, MCP servers, model context protocol integration, AI capability registry, agent toolset",
             eyebrow="System 08 — The arsenal",
             h1="Four tiers,<br>one array",
             crumb="The arsenal",
             lede="Skills we wrote, third-party servers, whole open-source applications and the worker corps — "
                  "unified behind one registry, and loaded only when they are actually needed.",
             doctrine="Adding a capability means dropping a file and registering it at startup. The "
                      "orchestrator never changes.",
             cite="app/core/registry.py",
             body=CAP_BODY),
    ]


def systems_hub() -> Page:
    cards = [
        ("legion", "nightcrawler", "01", "The Legion", "Sub-agent workers",
         "Scout, researcher, analyst, judge and general — isolated, memoryless, and resolved onto the cheap tier of whatever provider the parent turn already uses."),
        ("memory", "orion", "02", "Memory &amp; recall", "Eight files, one custodian",
         "A closed taxonomy with one question per file, a precedence rule that settles conflicts, and three tiers of recall above it."),
        ("automations", "sentinel", "03", "Proactive automations", "Probes and triggers",
         "Deterministic zero-token probes gate every agentic turn, and an automated turn is a real, readable, attachable chat turn."),
        ("news", "nightcrawler", "04", "The News Desk", "Two-tier intelligence",
         "An always-on free RSS layer with cross-outlet dedup and keyword alerts, plus a metered analyst tier for corroboration and timelines."),
        ("forge", "optimus", "05", "The Forge", "External execution",
         "A standalone engine connected as a WebSocket peer, giving an agent a privileged shell in its own isolated cell — with graceful fallback."),
        ("maps", "speda", "06", "Maps &amp; navigation", "Cartography inline",
         "Traffic-aware routing rendered in the conversation, origin defaulted to your live position, never written to stored history."),
        ("house-party", "warroom", "07", "House Party Protocol", "All hands",
         "The whole roster in parallel at full model grade with boundaries relaxed, behind a passphrase and a War Room takeover."),
        ("capabilities", "centurion", "08", "The arsenal", "Four tiers, one array",
         "Skills, MCP servers and wrapped open-source engines, lazily mounted and prompt-cached so breadth is nearly free."),
    ]
    grid = "\n".join(
        f"""      <a class="holo pad agent-card rise" style="--a: var(--{accent})" href="{slug}/">
        <span class="mark">{num}</span>
        <span class="name">{name}</span>
        <span class="domain">{dom}</span>
        <p class="card-body">{blurb}</p>
      </a>"""
        for slug, accent, num, name, dom, blurb in cards
    )

    body = f"""
<section class="hero" style="min-height:auto;padding:6rem 0 2rem">
  <div class="wrap">
    {TOP_CRUMB}
    <p class="eyebrow rise">The systems</p>
    <h1 class="rise">Eight subsystems,<br>one contract</h1>
    <p class="lede rise">None of these is a wrapper around a single API call. Each solves a problem that only
      appears once an assistant is expected to run continuously, remember a person, and spend real money doing
      it — and each is built to the same written architectural rules.</p>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="grid g3">
{grid}
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="section-head">
      <p class="eyebrow">The spine</p>
      <h2>Rules every subsystem obeys</h2>
      <p class="lede">These are enforced at review time rather than aspirational, and they are what keeps a
        system this wide from becoming a pile of special cases.</p>
    </div>
    <div class="grid g3">
      <article class="holo pad"><h3 class="card-title">Nothing lives in routers</h3><p class="card-body">A router builds context, hands off to the engine and streams. No business logic, no prompt construction, no tool registration.</p></article>
      <article class="holo pad"><h3 class="card-title">One owner of the system prompt</h3><p class="card-body">Prompt assembly exists in exactly one place. Never in a router, never in a service, never inline.</p></article>
      <article class="holo pad"><h3 class="card-title">One source of request state</h3><p class="card-body">A single context object carries user, session, database, model, timezone and a request UUID. No module-level globals.</p></article>
      <article class="holo pad"><h3 class="card-title">Every stop reason handled</h3><p class="card-body">The loop handles completion, tool use, truncation and server-tool pauses explicitly, and never breaks after the first tool call.</p></article>
      <article class="holo pad"><h3 class="card-title">A hard safety guard</h3><p class="card-body">Past thirty tool iterations the loop emits an error and terminates gracefully. A guard against runaway loops, not a feature limit.</p></article>
      <article class="holo pad"><h3 class="card-title">Zero identity in core</h3><p class="card-body">Names, personalities, prompt templates and model IDs live only in profile files. The engine is untouched by identity.</p></article>
      <article class="holo pad"><h3 class="card-title">No internal scheduler</h3><p class="card-body">Scheduling is external, permanently. The backend never grows a cron.</p></article>
      <article class="holo pad"><h3 class="card-title">Everything authenticates</h3><p class="card-body">Middleware validates the API key in constant time before any router runs; automated endpoints validate a second secret on top.</p></article>
      <article class="holo pad"><h3 class="card-title">Every request is traceable</h3><p class="card-body">A UUID propagates through every log line, tool call record, stream event and background task, so a multi-worker execution can be followed end to end.</p></article>
    </div>
    <div class="actions">
      <a class="btn btn-primary" href="{REPO}/blob/main/CLAUDE.md">The architectural contract</a>
      <a class="btn" href="../igor/">Inside Igor</a>
    </div>
  </div>
</section>
"""
    return Page(
        slug="systems",
        title="The Systems of Speda Mark VI — How It Actually Works",
        description=(
            "The eight subsystems of Speda Mark VI: the Legion, memory, proactive automations, the News Desk, "
            "the Forge, maps, House Party Protocol and the capability arsenal."
        ),
        keywords=(
            "Speda Mark VI architecture, AI assistant subsystems, multi-agent system design, agentic "
            "architecture, AI assistant internals"
        ),
        body=body,
        nav="systems",
        crumbs=[("systems", "Systems")],
        priority="0.9",
        jsonld=[{
            "@type": "ItemList",
            "name": "The subsystems of Speda Mark VI",
            "numberOfItems": 8,
            "itemListElement": [
                {"@type": "ListItem", "position": i, "name": name.replace("&amp;", "&"),
                 "url": url(f"systems/{slug}")}
                for i, (slug, _a, _n, name, _d, _b) in enumerate(cards, start=1)
            ],
        }],
    )


# ═══ CLIENTS ═════════════════════════════════════════════════════════════════

HB_BODY = """
<section>
  <div class="wrap">
    <figure style="margin-top:0">
      <img src="https://github.com/user-attachments/assets/3691eed2-f8a2-4922-b188-2fe3c24b3402"
           width="1920" height="1014"
           alt="The Heartbreaker desktop client for Speda Mark VI showing the fluid-glass conversation view">
      <figcaption>The conversation deck, themed to the active agent</figcaption>
    </figure>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="section-head">
      <p class="eyebrow">The surface</p>
      <h2>Every panel is wired to live state</h2>
      <p class="lede">Nothing on the deck is decorative. The client holds <strong>zero business logic</strong> —
        it renders the network; <a href="../igor/">Igor</a> runs it.</p>
    </div>
    <div class="grid g3">
      <article class="holo pad"><h3 class="card-title">Chat deck</h3><p class="card-body">Streaming answers with tool-call disclosure, and rich blocks for code, charts, calendars, maps and generated files.</p></article>
      <article class="holo pad"><h3 class="card-title">Welcome</h3><p class="card-body">Clock, agent identity, and a memory-aware one-liner in that agent's voice — generated from real state, then cached so it appears instantly.</p></article>
      <article class="holo pad"><h3 class="card-title">Roster switch</h3><p class="card-body">A cinematic agent picker across the whole roster. The interface recolours to the selected agent and shows its current model.</p></article>
      <article class="holo pad"><h3 class="card-title">Systems Board</h3><p class="card-body">Live telemetry: the model-routing matrix, active toolset shards, token budget, round-trip trace, memory data banks and engine-link status.</p></article>
      <article class="holo pad"><h3 class="card-title">Comms tray</h3><p class="card-body">The inter-agent group channel — every dispatch and its reply, with live working state on background jobs.</p></article>
      <article class="holo pad"><h3 class="card-title">War Room</h3><p class="card-body">The House Party takeover: activation, the staged roster strip, and per-agent model control while the protocol is engaged.</p></article>
    </div>
    <figure>
      <img src="https://github.com/user-attachments/assets/fbfbfdd7-dba2-4af5-8f0a-db49e80751d1"
           width="1920" height="994" loading="lazy"
           alt="The Heartbreaker Systems Board showing the model-routing matrix, toolset shards, token budget and memory data banks">
      <figcaption>The Systems Board — routing, toolsets, budget and memory, all live</figcaption>
    </figure>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="split">
      <div class="prose" style="max-width:none">
        <h3>The design contract</h3>
        <p>The look is codified so it stays coherent rather than drifting per component. A single glass recipe
          provides frosted volumetric depth, soft inner light and an agent-tinted rim; panels are built from it
          and glass is never reinvented locally.</p>
        <p>What it refuses is equally specified. <strong>Background grids, corner brackets, ruler ticks and
          scanlines are banned</strong> — they read as toy sci-fi rather than instrument. If a mockup contains
          them, they are dropped. That single prohibition is most of the distance between a command deck and a
          themed chat window.</p>
        <p>This page obeys the same contract.</p>
      </div>
      <div class="prose" style="max-width:none">
        <h3>One codebase, any brand</h3>
        <p>The build flag is the trick: it sets both the visual identity — name, model number, colour — and
          which backend agent the app addresses. The same build therefore ships as any single agent, each a
          fully branded standalone application pointed at its own endpoint.</p>
        <p>The sub-768px layout doubles as the specification for the Android client, so
          <a href="../speda-go/">Speda GO</a> and Heartbreaker are one design rather than two. A fixture test
          suite verifies the two theme engines against each other, agent by agent.</p>
      </div>
    </div>

    <div class="prose" style="margin-top:clamp(2.4rem,5vw,3.6rem)">
      <h3>Survivability is honoured client-side</h3>
      <p>Because turns are detached server-side, the client is written to match: it re-attaches to a live run
        when you enter a session, and cancels through the backend rather than by dropping the socket. Reload
        mid-answer and the answer is still arriving.</p>
    </div>

    <pre><code><span class="c"># with the backend already up</span>
npm install
npm run heartbreaker:dev        <span class="c"># Electron app</span>
npm run heartbreaker:web:dev    <span class="c"># browser-only</span></code></pre>

    <div class="actions">
      <a class="btn btn-primary" href="{repo}/blob/main/packages/heartbreaker/HEARTBREAKER.md">HEARTBREAKER.md</a>
      <a class="btn" href="../speda-go/">The Android client</a>
    </div>
  </div>
</section>
""".replace("{repo}", REPO)


GO_BODY = """
<section>
  <div class="wrap">
    <div class="split">
      <div class="prose" style="max-width:none">
        <h3>Native, and held to a written parity contract</h3>
        <p>Speda GO is Kotlin and Jetpack Compose rather than a wrapped web view, which means the Stark glass,
          the palette morph on agent switch, the ambient background and the House Party colour parade are all
          re-implemented natively. The theme engine is <strong>verified against the desktop one by fixture
          tests, agent by agent</strong> — parity is a test suite, not an intention.</p>
        <p>It carries the full chat core rather than a reduced companion subset: streaming with the live tool
          feed and typewriter rendering, re-attach so a turn that started while the screen was off is still
          running when you come back, the markdown prose renderer with inline chart, calendar and map blocks,
          attachments and image upload, download cards filing everything the backend generates, an offline
          transcript cache and session list, the agent switcher, a configuration tab, and rename or delete on
          any chat.</p>
        <p>AMOLED black, fullscreen, built for one thumb. The API key is held in the platform keystore rather
          than in plain preferences.</p>

        <h3>On the name</h3>
        <p>The mobile client is <strong>Speda GO</strong> — never "Heartbreaker mobile" or "Heartbreaker
          Droid". <a href="../heartbreaker/">Heartbreaker</a> is the desktop client only. The Kotlin package
          identifier deliberately still reads as the old name: renaming it would orphan every installed app's
          keystore data, so it stays exactly where it is.</p>
      </div>
      <figure class="portrait" style="margin-top:0">
        <img src="https://github.com/user-attachments/assets/704053d8-74b8-4a44-8d75-138c55dde3f9"
             width="738" height="1600"
             alt="Speda GO running on Android showing the AMOLED-black fluid-glass conversation view">
        <figcaption>One thumb, full deck</figcaption>
      </figure>
    </div>

    <div class="prose" style="margin-top:clamp(2.6rem,5vw,4rem)">
      <h3>Where the phone wins</h3>
      <p>Location is the case the desktop cannot match. Because the assistant knows which surface it is being
        addressed from and — with permission — where you are, <em>"how do I get home from here?"</em> resolves
        without a follow-up question, and answers with a live traffic-aware map inside the conversation.
        <a href="../systems/maps/">How the maps work →</a></p>

      <h3>Building it</h3>
      <p>The module opens straight in Android Studio from the monorepo. A push to the main branch produces a
        signed release through the mirror repository, which is a CI mirror of the package rather than a
        separate project — only its README is allowed to diverge.</p>
    </div>

    <div class="actions">
      <a class="btn btn-primary" href="https://github.com/spedatox/speda-go">Speda GO on GitHub</a>
      <a class="btn" href="{repo}/blob/main/docs/ANDROID_PORT_PLAN.md">The parity contract</a>
    </div>
  </div>
</section>
""".replace("{repo}", REPO)


IGOR_BODY = """
<section>
  <div class="wrap">
    <div class="section-head">
      <p class="eyebrow">The loop</p>
      <h2>What actually happens on a turn</h2>
    </div>
    <div class="prose">
      <p>A router builds a context object and hands off. Every chat turn then runs as a <strong>detached
        task</strong> decoupled from the HTTP request: it streams events, persists itself, and survives the
        client disconnecting. That is why closing the window mid-answer loses nothing, and why a running
        briefing can be tailed or cancelled from anywhere.</p>
      <p>The loop handles every stop reason explicitly. Completion returns. Tool use executes — in parallel
        where the tools are annotated read-only — appends results and <em>continues</em>. Truncation retries
        with a higher budget. A server-tool pause resumes the conversation. It never breaks after the first
        tool call, and it runs until the model is genuinely finished, under a hard thirty-iteration guard that
        exists to contain runaway loops rather than to cap capability.</p>
      <p>The system prompt is assembled in exactly one place, from ordered prompt sections plus a skills
        manifest read from disk at request time. Identity never enters the engine.</p>
    </div>

    <div class="stats rise" style="margin-top:2.4rem">
      <div class="stat"><span class="n">1</span><span class="l">orchestrator</span></div>
      <div class="stat"><span class="n">4</span><span class="l">stop reasons handled</span></div>
      <div class="stat"><span class="n">30</span><span class="l">iteration safety guard</span></div>
      <div class="stat"><span class="n">7</span><span class="l">providers, one client</span></div>
      <div class="stat"><span class="n">3</span><span class="l">transport channels</span></div>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="split">
      <div class="prose" style="max-width:none">
        <h3>Provider-agnostic by construction</h3>
        <p>Every model call goes through one client. References are <code>provider:model</code> strings, a bare
          name routing to the primary. Internally everything speaks a single content-block format and
          translation happens <em>only</em> at the wire boundary — which is what makes switching providers a
          configuration change rather than a rewrite.</p>
        <p>A fallback chain retries the next provider on failure, and the model picker only offers providers
          with configured credentials. The owner's per-agent pin outranks every default, for every trigger
          source — app, scheduler or inter-agent.</p>
        <p>One rule is stated as a prohibition, because violating it is subtle: <strong>never cross providers on
          the engine's own initiative.</strong> Cheap background tiers derive from the model the turn is
          actually running on, so no code path can quietly pull work back onto one vendor.</p>
      </div>
      <div class="prose" style="max-width:none">
        <h3>Three channels, not one</h3>
        <ul>
          <li><strong>HTTP + streaming</strong> — the user-facing chat surface, one endpoint per agent.</li>
          <li><strong>WebSocket chat</strong> — the low-latency bidirectional path.</li>
          <li><strong>Peer sockets</strong> — reserved exclusively for external execution engines. In-process
            agents are profiles, not sockets, and never use this path.</li>
        </ul>
        <p>Conflating those three is the most common way to misread the architecture, so the distinction is
          written down rather than implied.</p>

        <h3>Sessions are scoped by agent</h3>
        <p>Every session belongs to a pair of owner and agent, which is why one agent's history never appears
          in another's list, and why the same field that selects a profile also scopes automations and recall.</p>
      </div>
    </div>

    <div class="prose" style="margin-top:clamp(2.6rem,5vw,4rem)">
      <h3>Observability is not optional</h3>
      <p>Every request is issued a UUID at context construction, and it propagates through every log line, tool
        call record, stream event and background task. Logs are structured JSON. It is the only practical way to
        trace a single request through a multi-tool, multi-worker execution — and with the Legion running
        several isolated workers in parallel, that is the normal case rather than the exception.</p>

      <h3>Security posture</h3>
      <ul>
        <li>Middleware validates the API key in <strong>constant time</strong> on every request, before any
          router logic runs.</li>
        <li>Automated endpoints validate a second shared secret on top of that.</li>
        <li>Interactive API documentation is disabled outside debug.</li>
        <li>Browser automation runs only inside container isolation on an internal network.</li>
        <li>Generated files are temporary by design and cleaned on a schedule.</li>
      </ul>
    </div>

    <div class="actions">
      <a class="btn btn-primary" href="{repo}/blob/main/CLAUDE.md">The architectural contract</a>
      <a class="btn" href="{repo}/blob/main/docs/REFERENCE.md">Technical reference</a>
      <a class="btn" href="../systems/">The subsystems</a>
    </div>
  </div>
</section>
""".replace("{repo}", REPO)


def client_pages() -> list[Page]:
    return [
        Page(
            slug="heartbreaker",
            title="Heartbreaker — The Speda Mark VI Desktop Command Deck",
            description=(
                "Heartbreaker is the Speda Mark VI desktop client: a fluid-glass holographic command deck on "
                "Electron and React, with a live Systems Board and a comms tray."
            ),
            keywords=(
                "Heartbreaker, Speda Mark VI desktop client, Electron AI assistant UI, holographic HUD "
                "interface, fluid glass UI, AI command deck"
            ),
            body=shell(
                crumbs_html=TOP_CRUMB,
                eyebrow="The desktop client",
                h1="Heartbreaker",
                lede="The primary face of Speda Mark VI, and the thing that makes it feel unlike other "
                     "assistants: a Stark-tech fluid-glass command deck rather than a message list.",
                doctrine="Heartbreaker renders the network. Igor runs it. The client holds zero business logic.",
                cite="packages/heartbreaker/HEARTBREAKER.md",
                body=HB_BODY,
            ),
            nav="heartbreaker",
            crumbs=[("heartbreaker", "Heartbreaker")],
            priority="0.85",
            jsonld=[{
                "@type": "SoftwareApplication",
                "name": "Heartbreaker",
                "alternateName": "Speda Mark VI desktop client",
                "applicationCategory": "BusinessApplication",
                "operatingSystem": "Windows, macOS, Linux",
                "url": url("heartbreaker"),
                "description": "The desktop client for Speda Mark VI — a fluid-glass holographic command deck built with Electron and React, with an agent switcher, a live Systems Board and an inter-agent comms tray.",
                "author": {"@id": f"{BASE}/#author"},
                "isPartOf": {"@type": "SoftwareApplication", "name": "Speda Mark VI", "url": url("")},
            }],
        ),
        Page(
            slug="speda-go",
            title="Speda GO — Native Android Client for Speda Mark VI",
            description=(
                "Speda GO is the native Kotlin and Jetpack Compose Android client for Speda Mark VI — full "
                "chat core, inline maps and charts, and an offline transcript cache."
            ),
            keywords=(
                "Speda GO, Speda Mark VI Android, Kotlin AI assistant app, Jetpack Compose AI client, native "
                "Android AI assistant"
            ),
            body=shell(
                crumbs_html=TOP_CRUMB,
                eyebrow="The Android client",
                h1="Speda GO",
                lede="The command deck in your pocket — native Kotlin and Jetpack Compose, carrying the whole "
                     "chat core rather than a companion subset.",
                doctrine="Parity with the desktop client is a fixture test suite, agent by agent — not an "
                         "intention.",
                cite="packages/speda-go",
                body=GO_BODY,
            ),
            nav="speda-go",
            accent="atomix",
            crumbs=[("speda-go", "Speda GO")],
            priority="0.85",
            jsonld=[{
                "@type": "MobileApplication",
                "name": "Speda GO",
                "alternateName": "Speda Mark VI for Android",
                "applicationCategory": "BusinessApplication",
                "operatingSystem": "Android",
                "url": url("speda-go"),
                "downloadUrl": "https://github.com/spedatox/speda-go",
                "description": "The native Android client for Speda Mark VI, written in Kotlin with Jetpack Compose. Carries the full chat core including streaming, the tool feed, inline map, chart and calendar rendering, attachments, an offline transcript cache and the agent switcher.",
                "author": {"@id": f"{BASE}/#author"},
                "isPartOf": {"@type": "SoftwareApplication", "name": "Speda Mark VI", "url": url("")},
            }],
        ),
        Page(
            slug="igor",
            title="Igor — The Speda Mark VI Agentic Backend (FastAPI)",
            description=(
                "Igor is the FastAPI agentic core of Speda Mark VI: one orchestrator owning the loop and the "
                "system prompt, detached turns, and provider-agnostic model routing."
            ),
            keywords=(
                "Igor backend, Speda Mark VI architecture, FastAPI agentic loop, multi-agent orchestrator, "
                "capability registry, provider-agnostic LLM routing, detached turns"
            ),
            body=shell(
                crumbs_html=TOP_CRUMB,
                eyebrow="The backend",
                h1="Igor",
                lede="The agentic core — built to a written contract rather than assembled by accretion. One "
                     "orchestrator owns the loop and the system prompt, one registry owns every capability, and "
                     "one client owns every model call.",
                doctrine="Heartbreaker is the face. Igor is the brain and hands.",
                cite="packages/igor/IGOR.md",
                body=IGOR_BODY,
            ),
            nav="igor",
            accent="optimus",
            crumbs=[("igor", "Igor")],
            priority="0.85",
            jsonld=[{
                "@type": "TechArticle",
                "headline": "Igor — the agentic backend of Speda Mark VI",
                "description": "How the Speda Mark VI backend is built: a single orchestrator owning the system prompt and agentic loop, detached turns, a four-tier capability registry, in-process agent profiles and provider-agnostic model routing.",
                "url": url("igor"),
                "author": {"@id": f"{BASE}/#author"},
                "about": {"@type": "SoftwareApplication", "name": "Speda Mark VI", "url": url("")},
                "proficiencyLevel": "Expert",
            }],
        ),
    ]
