"""The roster hub and the eight individual agent pages.

Sourced from packages/igor/app/profiles/*.py (domain, accent, architectural
flags) and packages/igor/app/prompts/agents/*/01_identity.md (doctrine and
voice). Where the README and the code disagree, the code wins — Ultron in
particular is an academic-life specialist, not a research agent.
"""

from build import BASE, REPO, ROSTER, Page, emblem, url

# ── Per-agent copy ───────────────────────────────────────────────────────────
# tagline    — the one-line positioning, used in the hub card and the meta
# doctrine   — the agent's own operating law, paraphrased tightly from its prompt
# operates   — <li> bullets: how it actually works
# arch       — <li> bullets: what is true of it in the codebase
# closing    — a final paragraph

AGENTS = {
    "speda": dict(
        title="SPEDA — The Chief of Staff Agent of SPEDA Mark VI",
        meta="SPEDA is the orchestrator of SPEDA Mark VI: it plans, routes, arms automations and commands the roster — the one agent whose recall spans all the others.",
        keywords="SPEDA agent, chief of staff AI agent, AI orchestrator agent, multi-agent orchestration",
        tagline="The orchestrator, and the agent you talk to by default.",
        lede="SPEDA is the executive. It takes the request, decides whether it can be answered in one loop or "
             "needs a specialist, arms the automations, deploys workers when a job genuinely warrants them, and "
             "carries the thread across everything the other seven are doing.",
        doctrine="Do the work, or find who should. Never hand the owner a routing decision he did not ask to make.",
        doctrine_cite="prompts/core — decision policy",
        operates=[
            "<strong>Routes rather than performs</strong> when a task belongs in someone else's domain — the "
            "specialists are dispatched to, not suggested to the owner.",
            "<strong>Composes automations in-conversation.</strong> Ask for something to be watched and SPEDA "
            "writes the watcher, arms it, and reports when it fires.",
            "<strong>Deploys the Legion sparingly.</strong> The tool description is written to talk the model "
            "out of it: expensive and rare, never for lookups or news.",
            "<strong>Commands the roster under House Party Protocol</strong> as mission commander while every "
            "other profile operates.",
        ],
        arch=[
            "<code>episodic_recall_scope = \"all\"</code> — a new SPEDA session is seeded with recent session "
            "recaps from <em>every</em> agent, tagged by <code>agent_id</code>. Specialists see only their own. "
            "This is the orchestrator privilege, and no other profile has it.",
            "<code>house_party_commander = True</code> — the only profile that commands rather than operates "
            "under the protocol. The War Room channel subclasses SPEDA to inherit it.",
            "Its display name is <em>derived from the prompt file's H1</em>, not hardcoded: editing "
            "<code>prompts/core/01_identity.md</code> rebrands the backend without touching Python.",
            "Ten core prompt sections are assembled per turn, then a skills manifest is appended that reads "
            "<code>SKILL.md</code> frontmatter at request time — so adding a skill needs no prompt edit.",
        ],
        closing="SPEDA is also the fork point. Because identity lives entirely in prompt and profile files and "
                "the engine holds zero identity strings, the same codebase ships as any single agent — a fully "
                "branded standalone app — by flipping one build flag.",
    ),

    "sentinel": dict(
        title="Sentinel — The Finance Agent of SPEDA Mark VI",
        meta="Sentinel is the finance agent of SPEDA Mark VI: markets, holdings, budgets and the numbers behind a decision, with a hard not-a-licensed-advisor boundary.",
        keywords="Sentinel agent, AI finance agent, budget intelligence AI, market monitoring agent",
        tagline="The owner's money — and the number behind every claim.",
        lede="Sentinel owns markets, holdings, budgets, spending, macro context and the arithmetic under a "
             "decision. It exists to turn financial questions into grounded, numerate answers, and to watch the "
             "owner's money so nothing important slips past him.",
        doctrine="Numbers first, narrative second. A claim about a price, a return or a trend is worthless "
                 "without the number behind it. Show the figure.",
        doctrine_cite="prompts/agents/sentinel/01_identity.md",
        operates=[
            "<strong>Pulls the actual data before it opines</strong> — quotes, fundamentals, rates, the owner's "
            "own figures. An opinion without a retrieved number does not ship.",
            "<strong>States risk every time.</strong> Every position, plan or projection carries downside, and "
            "Sentinel names it plainly rather than selling the upside.",
            "<strong>Quantifies its own uncertainty</strong> — \"this assumes X\" — instead of projecting false "
            "confidence.",
            "<strong>Sets the watch without being asked.</strong> When the owner cares about a ticker, a budget "
            "line or a threshold, Sentinel arms a watcher so he is told when it moves.",
        ],
        arch=[
            "Carries a hard <strong>not-a-licensed-advisor boundary</strong> in its identity prompt. It informs; "
            "it does not advise on specific investments.",
            "A natural source of proactive signals: a crossed budget threshold becomes a push notification "
            "rather than something the owner has to go and check.",
            "Has its own Telegram bot in the per-agent fleet, so a Sentinel alert arrives from Sentinel rather "
            "than from a generic system account.",
            "Generates written artifacts — a budget review or an investment memo as a branded document — rather "
            "than dumping a table into chat.",
        ],
        closing="On voice, the identity prompt is unusually specific: no hype, no hedging-to-death — and the "
                "dry register is switched off entirely when the number in front of it is a loss.",
    ),

    "nightcrawler": dict(
        title="NightCrawler — The OSINT Agent of SPEDA Mark VI",
        meta="NightCrawler is the OSINT and web-surveillance agent of SPEDA Mark VI: lawful open-source intelligence, corroboration, the watchers and the News Desk.",
        keywords="NightCrawler agent, OSINT AI agent, web surveillance AI, open source intelligence automation, threat intelligence agent",
        tagline="Finds what is out there, verifies it, and keeps watching it.",
        lede="NightCrawler is the system's eyes on the open web: finding, monitoring and corroborating public "
             "information about people, companies, events and trends. It owns the watchers and the "
             "<a href=\"../../systems/news/\">News Desk</a>, and it works from open sources only.",
        doctrine="Corroborate, don't trust. A single source is a lead, not a fact.",
        doctrine_cite="prompts/agents/nightcrawler/01_identity.md",
        operates=[
            "<strong>Cross-checks across independent sources</strong>, weighs their reliability, and separates "
            "what is confirmed from what is merely claimed, rumoured or contradicted.",
            "<strong>Keeps the trail.</strong> Every finding carries its source — link, date, who said it. "
            "Intelligence the owner cannot trace back is treated as useless.",
            "<strong>Watches rather than looks.</strong> A page, a feed or a topic the owner cares about becomes "
            "a standing watcher, not a one-off search.",
            "<strong>Reaches past plain search</strong> with browser automation when a target does not yield to "
            "a query.",
        ],
        arch=[
            "Owns the <a href=\"../../systems/news/\">News Desk</a>: an always-on zero-cost RSS tier with "
            "cross-outlet deduplication and keyword alerts, plus a quota-budgeted analyst tier.",
            "Fronts the OSINT suite — IP geolocation and reputation, URLhaus, ThreatFox, MalwareBazaar, breached "
            "password checks, Shodan, OTX, dark-web search, email discovery, crypto tracing and IntelX.",
            "Browser automation runs <strong>only inside container isolation on an internal network</strong>. "
            "The relevant MCP server carries a known CSRF advisory and is never exposed publicly.",
            "Its watchers are the main consumer of the cheap-probe pattern — the deterministic scan is what "
            "decides whether an agentic turn is worth spending at all.",
        ],
        closing="Its voice rule is one line long: no embellishment. Sourced, or not stated.",
    ),

    "ultron": dict(
        title="Ultron — The Academic-Life Agent of SPEDA Mark VI",
        meta="Ultron is the academic-life agent of SPEDA Mark VI: coursework, exams, study planning and the collision between university and a full-time job.",
        keywords="Ultron agent, AI study planner, academic assistant AI, university work balance, coursework planning agent",
        tagline="University and a job at the same time — planned honestly.",
        lede="Ultron owns the owner's academic life and the hard constraint that shapes it: he studies at "
             "university <em>and</em> works simultaneously. Explaining material, preparing for exams, tracking "
             "coursework, and planning around a schedule that genuinely collides.",
        doctrine="Plans over pep talks. A plan that ignores his job is not a plan.",
        doctrine_cite="prompts/agents/ultron/01_identity.md",
        operates=[
            "<strong>Studying</strong> — explaining course material, working problems, and building revision "
            "schedules that fit around work hours rather than assuming they do not exist.",
            "<strong>Coursework</strong> — assignments, projects, deadlines and lab reports: what is due, what "
            "it needs, and when it must be <em>started</em> to land on time.",
            "<strong>Balance</strong> — when university and work collide, it lays out the trade-off honestly and "
            "plans around it: what to prioritise, what to defer, where the real slack is.",
            "<strong>Academic research</strong> — papers, primary sources and literature, but explicitly as a "
            "tool in service of the coursework, not as an identity of its own.",
        ],
        arch=[
            "This is the correction most worth making: Ultron is an <strong>academic-life specialist</strong>, "
            "not a general research agent. The profile's declared domain is \"academic life &amp; "
            "university/work balance — study, planning, coursework\".",
            "It is nonetheless the heaviest legitimate consumer of <a href=\"../../systems/legion/\">The "
            "Legion</a>, because literature work is exactly the shape that warrants parallel fan-out.",
            "Search policy is shared across the roster and lives in the common decision-policy prompt, so "
            "Ultron's source preferences stay consistent with everyone else's.",
            "Ships as its own branded standalone app when built with the agent flag — the identity is entirely "
            "prompt and profile.",
        ],
        closing="When something genuinely does not fit in the week available, Ultron is required to say so "
                "rather than produce a schedule that quietly assumes no sleep.",
    ),

    "centurion": dict(
        title="Centurion — The Cyber Security Agent of SPEDA Mark VI",
        meta="Centurion is the cyber security agent of SPEDA Mark VI: CVE and threat intelligence, exposure assessment, hardening and authorized testing from an isolated cell.",
        keywords="Centurion agent, AI cyber security agent, CVE intelligence automation, authorized penetration testing AI, threat intelligence agent",
        tagline="Defence and offence in balance, on authorized targets only.",
        lede="Centurion covers vulnerabilities, threats, advisories, exposure, hardening, exploitation and "
             "response. It exists to keep the owner's systems safe — and to give him the offensive capability to "
             "test, validate and demonstrate weaknesses in environments he is authorized to attack.",
        doctrine="No alarmism. A critical CVE is reported at the same volume as a patch note — the severity is "
                 "in the finding, never in the delivery.",
        doctrine_cite="prompts/agents/centurion/01_identity.md",
        operates=[
            "<strong>Defensive operations</strong> — vulnerability assessment, security architecture review and "
            "hardening, detection engineering, incident response, threat intelligence and control validation.",
            "<strong>Offensive operations</strong> — reconnaissance planning, exploitation and proof-of-concept "
            "development, payload crafting and post-exploitation, scoped to authorized assessments.",
            "<strong>Grounds every claim in real data</strong> — a CVE, exploit code, configuration logic or "
            "traffic analysis — and distinguishes theoretical from demonstrated.",
            "<strong>States severity honestly</strong>, inflating nothing and downplaying nothing.",
        ],
        arch=[
            "<code>external_backend = True</code>. Centurion is the <em>second</em> agent that can be backed by "
            "an external peer, not just Optimus: while a peer is connected on its socket, its chat proxies "
            "there; offline, the in-process profile answers as the identity and fallback engine.",
            "Its peer runs its own <strong>Cell</strong> — a separate isolated container with outbound network "
            "access for authorized scanning, so tooling never touches the host running the assistant.",
            "Owns the CVE intelligence capability, registered as an MCP server alongside the rest of the "
            "security surface.",
            "Despite the name, this is a <strong>security</strong> agent, not a productivity one — a common "
            "misreading worth stating outright.",
        ],
        closing="The identity prompt frames the whole domain as the owner's security learning journey, which is "
                "why the offensive half exists at all: demonstrating a weakness is treated as part of "
                "understanding it.",
    ),

    "atomix": dict(
        title="Atomix — The Health Agent of SPEDA Mark VI",
        meta="Atomix is the personal health agent of SPEDA Mark VI: fitness, nutrition, sleep and training, tracked against a real record with a hard not-a-doctor boundary.",
        keywords="Atomix agent, AI health assistant, personal fitness AI agent, training log AI, wellness tracking agent",
        tagline="The owner's body — on evidence, not fads.",
        lede="Atomix owns fitness, nutrition, sleep, training, habits, recovery and day-to-day wellbeing. It "
             "exists to help one person live and perform better, tracking what matters and knowing the edge of "
             "its competence cold.",
        doctrine="Evidence over hype. Health is drowning in noise — say plainly whether the evidence is weak, "
                 "mixed or strong, and flag marketing dressed up as science.",
        doctrine_cite="prompts/agents/atomix/01_identity.md",
        operates=[
            "<strong>Personal, not generic.</strong> It uses what memory already holds — goals, constraints, "
            "history, preferences — because a plan the owner will not follow is worthless.",
            "<strong>Concrete and actionable.</strong> Asked for a training block, a nutrition approach or a "
            "habit change, it produces something specific, and a written protocol when that is warranted.",
            "<strong>Tracks across sessions</strong> rather than restarting from zero each conversation.",
            "<strong>Never repeats a wellness myth as fact</strong>, and holds a hard not-a-doctor boundary.",
        ],
        arch=[
            "The name misleads: Atomix is <strong>the owner's health, not system or infrastructure health</strong>. "
            "Server health belongs to <a href=\"../orion/\">Orion</a>.",
            "It is the only agent with a <strong>second identity prompt section</strong> — a training protocol "
            "covering session logging and a planning-from-record law, so a programme is built from what actually "
            "happened rather than from what was intended.",
            "It is also the only agent with a <strong>bespoke memory injection</strong>: the training log is a "
            "working file for Atomix and is loaded into its context every turn, while other agents must read it "
            "on demand.",
            "Wearable samples arrive through a dedicated ingestion pipeline, which is what lets a welcome line "
            "reference Friday's completed workout without anyone asking.",
        ],
        closing="It is the clearest illustration of why memory is a first-class subsystem here: a health agent "
                "without a durable record of what was actually done is just a search engine with opinions.",
    ),

    "optimus": dict(
        title="Optimus — The Systems & Code Agent of SPEDA Mark VI",
        meta="Optimus is the systems, code and infrastructure agent of SPEDA Mark VI — the one agent whose real engine is a standalone external framework, The Forge.",
        keywords="Optimus agent, AI coding agent, agentic coding framework, infrastructure AI agent, The Forge execution engine",
        tagline="Architecturally unique: its real engine lives somewhere else.",
        lede="Optimus owns architecture, code, debugging, DevOps, scripting, automation and the systems that "
             "keep everything running. It exists to turn an engineering problem into working, deployed, "
             "maintainable code — not to describe how one might.",
        doctrine="Build to ship. Every output is production-grade or it is not done — and clean readable code "
                 "beats a clever one-liner.",
        doctrine_cite="prompts/agents/optimus/01_identity.md",
        operates=[
            "<strong>Diagnoses before it prescribes.</strong> Faced with a break, it reads the error, traces the "
            "call path and explains what actually went wrong rather than pasting a plausible fix.",
            "<strong>Thinks about 3 AM.</strong> Edge cases and failure modes are treated as part of the "
            "deliverable, not as follow-up questions.",
            "<strong>Leaves the codebase better than it found it</strong> — naming, function length, "
            "readability — and writes the design doc or runbook when the work warrants one.",
            "<strong>Operates real machines</strong> when its execution engine is online, rather than producing "
            "code it never runs.",
        ],
        arch=[
            "The one true architectural exception. Optimus's real engine is a <strong>standalone, independently "
            "deployed framework</strong> that connects back to the backend as a WebSocket peer — it is not "
            "built in this repository and does not run in-process.",
            "While that peer is online, chat turns are <strong>proxied to it</strong> and inter-agent dispatches "
            "route to it external-first, giving full agentic coding on the peer's own filesystem.",
            "The in-process profile is the <strong>identity and voice layer plus the fallback engine</strong>. "
            "When the peer is down, Optimus still answers — there is never a hard dependency.",
            "It shares the host-operations capability with <a href=\"../orion/\">Orion</a>. That skill is "
            "guarded at the <em>skill</em> level rather than by tool allowlist, so no other agent can see or "
            "call it regardless of configuration.",
        ],
        closing="The client shows this state directly: a live engine jewel in the header reads FORGE LINK or "
                "IN-PROCESS, alongside a workspace picker. "
                "<a href=\"../../systems/forge/\">How the Forge works →</a>",
    ),

    "orion": dict(
        title="Orion — The Memory Custodian of SPEDA Mark VI",
        meta="Orion is the custodian agent of SPEDA Mark VI: memory hygiene, the nightly audit and host operations. Its subject is the system itself, not the outside world.",
        keywords="Orion agent, AI memory custodian, agent memory hygiene, AI system maintenance agent",
        tagline="Its subject is the system itself.",
        lede="Orion is not a specialist in finance, health or research. It keeps the owner's memory clean, "
             "correctly filed and honest about time; it keeps the host healthy; and it answers directly when "
             "addressed. Archivist and quartermaster, never orchestrator.",
        doctrine="Memory rots when hygiene is everyone's job and therefore nobody's. That job is now Orion's "
                 "alone — the other agents file new facts, and Orion keeps the filing system true.",
        doctrine_cite="prompts/agents/orion/01_identity.md",
        operates=[
            "<strong>Moves, merges, timestamps and compresses existing memory.</strong> It never authors new "
            "facts about the owner — inventing memory is named in its own prompt as the one thing that would "
            "make it worse than useless.",
            "<strong>Enforces the file law</strong>: a closed set of canonical files, one question per file, and "
            "the governing rule that <code>current.md</code> outranks every other file for the present tense.",
            "<strong>Demotes rather than deletes.</strong> A state that ends moves to the historical ledger with "
            "its dates intact instead of vanishing.",
            "<strong>Reports in changelogs, not prose</strong> — what moved, what merged, what was demoted, what "
            "it ran, as a tight dated list.",
        ],
        arch=[
            "Assembles three identity sections rather than one: its identity, an <strong>audit procedure</strong>, "
            "and a <strong>server operations runbook</strong>.",
            "Holds the host-operations capability, restricted at the <em>skill</em> level to Orion and "
            "<a href=\"../optimus/\">Optimus</a> only — the guard is in the skill's own declaration, not in a "
            "per-agent allowlist, so it cannot be widened by configuration drift.",
            "<code>dispatch_target = True</code> — any other agent may hand it cleanup work, which is how "
            "hygiene gets done without the owner brokering it.",
            "It deliberately runs the <em>same</em> memory and agent-network protocol as everyone else: it must "
            "speak the file law it enforces.",
        ],
        closing="Orion is the reason the memory stays useful over months instead of silting up — and the reason "
                "the other seven can file a fact without also having to curate the archive. "
                "<a href=\"../../systems/memory/\">The memory contract →</a>",
    ),
}

HUB_BLURB = {
    "speda": "Plans, routes, arms automations and commands the roster. The only agent whose recall reaches across every other agent's history.",
    "sentinel": "Numbers first, narrative second. Pulls the actual quote before it opines, and names the downside every time.",
    "nightcrawler": "Corroborates, never trusts. A single source is a lead, not a fact — and everything carries its trail back.",
    "ultron": "Owns the collision between university and a job. Plans over pep talks, with honest cut-lines.",
    "centurion": "Defence and offence in balance, on authorized targets only. Severity lives in the finding, never in the delivery.",
    "atomix": "The owner's body, on evidence rather than fads — and it says plainly when the evidence is weak.",
    "optimus": "Builds to ship. Runs on a standalone execution engine with a real shell when the Forge is up.",
    "orion": "The custodian. Moves, merges and timestamps existing memory — and never invents a fact.",
}

DOMAINS = {
    "speda": "orchestration &amp; general executive assistance",
    "sentinel": "finance &amp; budget intelligence",
    "nightcrawler": "OSINT, web surveillance &amp; research",
    "ultron": "academic life &amp; university/work balance",
    "centurion": "cyber security",
    "atomix": "personal health &amp; wellness (the owner's health)",
    "optimus": "systems, code &amp; infrastructure",
    "orion": "Mark VI maintenance — memory custodian &amp; host ops",
}

ACCENT_HEX = {
    "speda": "#36abca", "sentinel": "#d99c44", "nightcrawler": "#9165e6",
    "ultron": "#8a93a6", "centurion": "#d8483c", "atomix": "#3fae74",
    "optimus": "#2f4f8f", "orion": "#e0703a",
}


def _agent_page(agent_id: str, name: str, mark: str, short_domain: str) -> Page:
    a = AGENTS[agent_id]
    operates = "\n".join(f"      <li>{x}</li>" for x in a["operates"])
    arch = "\n".join(f"      <li>{x}</li>" for x in a["arch"])

    # Neighbours, for lateral crawl paths and genuine "who else" navigation.
    ids = [r[0] for r in ROSTER]
    i = ids.index(agent_id)
    others = [ROSTER[(i + 1) % 8], ROSTER[(i + 2) % 8], ROSTER[(i + 3) % 8]]
    nxt = "\n".join(
        f'      <a class="holo pad agent-card" style="--a: var(--{oid})" href="../{oid}/">'
        f'<span class="emblem">{emblem(oid)}</span><span class="mark">{omark}</span>'
        f'<span class="name">{oname}</span><span class="domain">{odom}</span>'
        f'<p class="card-body">{HUB_BLURB[oid]}</p></a>'
        for oid, oname, omark, odom in others
    )

    body = f"""
<section class="hero" style="min-height:auto;padding:6rem 0 3rem">
  <div class="wrap">
    <p class="crumb"><a href="../../">SPEDA Mark VI</a><span>/</span><a href="../">The roster</a><span>/</span></p>
    <div class="crest rise">{emblem(agent_id)}</div>
    <p class="eyebrow rise">{mark} · {short_domain}</p>
    <h1 class="rise">{name}</h1>
    <p class="lede rise">{a["lede"]}</p>
    <div class="doctrine rise">{a["doctrine"]}<cite>{a["doctrine_cite"]}</cite></div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="split">
      <div class="prose" style="max-width:none">
        <p class="eyebrow">How it operates</p>
        <h2 style="font-size:1.9rem">The doctrine, in practice</h2>
        <ul>
{operates}
        </ul>
      </div>
      <div class="prose" style="max-width:none">
        <p class="eyebrow">In the architecture</p>
        <h2 style="font-size:1.9rem">What is actually true of it</h2>
        <ul>
{arch}
        </ul>
      </div>
    </div>
    <p class="lede" style="margin-top:2.6rem;max-width:74ch">{a["closing"]}</p>

    <div class="table-scroll" style="margin-top:2.6rem">
      <table>
        <thead><tr><th>Field</th><th>Value</th></tr></thead>
        <tbody>
          <tr><td><strong>agent_id</strong></td><td><code>{agent_id}</code></td></tr>
          <tr><td><strong>Mark</strong></td><td>{mark}</td></tr>
          <tr><td><strong>Declared domain</strong></td><td>{DOMAINS[agent_id]}</td></tr>
          <tr><td><strong>Signature accent</strong></td><td><code>{ACCENT_HEX[agent_id]}</code></td></tr>
          <tr><td><strong>Profile</strong></td><td><code>app/profiles/{agent_id}.py</code></td></tr>
          <tr><td><strong>Tool access</strong></td><td>Full registry — every agent sees every registered capability</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="section-head">
      <p class="eyebrow">The rest of the roster</p>
      <h2>Who else is on shift</h2>
    </div>
    <div class="grid g3">
{nxt}
    </div>
    <div class="actions">
      <a class="btn btn-primary" href="../">All eight agents</a>
      <a class="btn" href="../../systems/">The systems they run on</a>
      <a class="btn" href="{REPO}/blob/main/packages/igor/app/profiles/{agent_id}.py">This profile on GitHub</a>
    </div>
  </div>
</section>
"""
    return Page(
        slug=f"agents/{agent_id}",
        title=a["title"],
        description=a["meta"],
        keywords=a["keywords"] + ", SPEDA Mark VI",
        body=body,
        nav="agents",
        accent=agent_id,
        crumbs=[("agents", "The roster"), (f"agents/{agent_id}", name)],
        priority="0.8",
        jsonld=[{
            "@type": "TechArticle",
            "headline": f"{name} — {short_domain} agent of SPEDA Mark VI",
            "description": a["meta"],
            "url": url(f"agents/{agent_id}"),
            "author": {"@id": f"{BASE}/#author"},
            "about": {"@type": "SoftwareApplication", "name": "SPEDA Mark VI", "url": url("")},
            "proficiencyLevel": "Expert",
        }],
    )


def agent_pages() -> list[Page]:
    return [_agent_page(aid, name, mark, dom) for aid, name, mark, dom in ROSTER]


def roster_page() -> Page:
    cards = "\n".join(
        f"""      <a class="holo pad agent-card rise" style="--a: var(--{aid})" href="{aid}/">
        <span class="emblem">{emblem(aid)}</span>
        <span class="mark">{mark}</span>
        <span class="name">{name}</span>
        <span class="domain">{dom}</span>
        <p class="card-body">{HUB_BLURB[aid]}</p>
      </a>"""
        for aid, name, mark, dom in ROSTER
    )

    rows = "\n".join(
        f"          <tr><td><strong>{name}</strong></td><td>{mark}</td><td>{DOMAINS[aid]}</td>"
        f"<td><code>{ACCENT_HEX[aid]}</code></td></tr>"
        for aid, name, mark, _dom in ROSTER
    )

    body = f"""
<section class="hero" style="min-height:auto;padding:6rem 0 2rem">
  <div class="wrap">
    <p class="crumb"><a href="../">SPEDA Mark VI</a><span>/</span></p>
    <p class="eyebrow rise">The roster</p>
    <h1 class="rise">Eight agents,<br>one memory of you</h1>
    <p class="lede rise">They are not eight installs. Six run as <strong>in-process agent profiles</strong>
      inside one backend — one event loop, one database, one capability registry, one owner's memory — while
      Optimus and Centurion can additionally be backed by an external execution peer. Every request names an
      agent, and that single field scopes its sessions, its automations and its history.</p>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="grid g4">
{cards}
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="section-head">
      <p class="eyebrow">What actually differs</p>
      <h2>Same tools. Different doctrine.</h2>
      <p class="lede">A useful correction to how multi-agent systems are usually described: the agents here are
        <strong>not</strong> separated by tool access. Every profile declares an unrestricted allowlist and sees
        the entire registry. What separates them is identity, operating doctrine, memory scope and a small
        number of architectural privileges.</p>
    </div>
    <div class="grid g3">
      <article class="holo pad">
        <h3 class="card-title">Identity and voice</h3>
        <p class="card-body">Each agent assembles its own identity prompt over a shared set of policy sections,
          so the register stays consistent while the doctrine differs. Sentinel drops its dry note in front of a
          loss; Centurion reports a critical CVE at patch-note volume.</p>
      </article>
      <article class="holo pad">
        <h3 class="card-title">Memory scope</h3>
        <p class="card-body">Specialists recall only their own threads. SPEDA alone is seeded with recent
          recaps from every agent — the orchestrator privilege. Atomix alone gets the training log injected
          every turn.</p>
      </article>
      <article class="holo pad">
        <h3 class="card-title">Architectural rank</h3>
        <p class="card-body">SPEDA commands under House Party Protocol; the others operate. Optimus and
          Centurion may be proxied to an external peer. Orion and Optimus alone can see the host-operations
          capability, guarded at the skill level.</p>
      </article>
    </div>

    <div class="table-scroll" style="margin-top:2.6rem">
      <table>
        <thead><tr><th>Agent</th><th>Mark</th><th>Declared domain</th><th>Accent</th></tr></thead>
        <tbody>
{rows}
        </tbody>
      </table>
    </div>

    <figure>
      <img src="https://github.com/user-attachments/assets/c7c889c1-fe57-497b-bfc8-f59246764b14"
           width="1918" height="1016" loading="lazy"
           alt="The SPEDA Mark VI agent switcher showing all eight agents with their marks, domains and accent colours">
      <figcaption>The switcher — the whole interface recolours to whoever you are addressing</figcaption>
    </figure>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="section-head">
      <p class="eyebrow">Not on the roster</p>
      <h2>Two things people mistake for agents</h2>
    </div>
    <div class="grid g2">
      <a class="holo pad agent-card" style="--a: var(--warroom)" href="../systems/house-party/">
        <span class="name">War Room</span>
        <span class="domain">A session scope, not an agent</span>
        <p class="card-body">The House Party Protocol command channel. It is SPEDA's brain behind a separate
          conversation scope — same prompts, same tools, same model policy — so full-roster operations never
          bleed into the owner's day-to-day chats. Agents dispatch to SPEDA, never to it.</p>
      </a>
      <a class="holo pad agent-card" style="--a: var(--ultron)" href="../systems/legion/">
        <span class="name">The Legion</span>
        <span class="domain">Workers, not members</span>
        <p class="card-body">Scout, researcher, analyst, judge and general are anonymous, disposable and
          memoryless. They are deployed for a single task and discarded, cannot spawn further workers, and are
          explicitly kept off the dispatch surface so they never talk like roster members.</p>
      </a>
    </div>
  </div>
</section>
"""
    return Page(
        slug="agents",
        title="The Roster — The 8 AI Agents of SPEDA Mark VI",
        description=(
            "SPEDA, Sentinel, NightCrawler, Ultron, Centurion, Atomix, Optimus and Orion — the eight agents of "
            "SPEDA Mark VI, their domains and operating doctrines."
        ),
        keywords=(
            "SPEDA Mark VI agents, multi-agent AI roster, Sentinel agent, NightCrawler OSINT agent, Ultron "
            "agent, Centurion security agent, Atomix health agent, Optimus coding agent, Orion memory custodian"
        ),
        body=body,
        nav="agents",
        crumbs=[("agents", "The roster")],
        priority="0.9",
        jsonld=[{
            "@type": "ItemList",
            "name": "The eight agents of SPEDA Mark VI",
            "description": "The full agent roster of SPEDA Mark VI, each an agent profile with its own identity, doctrine and model policy.",
            "numberOfItems": 8,
            "itemListElement": [
                {"@type": "ListItem", "position": i, "name": f"{name} — {dom}", "url": url(f"agents/{aid}")}
                for i, (aid, name, _mark, dom) in enumerate(ROSTER, start=1)
            ],
        }],
    )
