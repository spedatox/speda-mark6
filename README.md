# S.P.E.D.A. Mark VI

**S**pecialized · **P**ersonal · **E**xecutive · **D**igital · **A**ssistant

A self-hosted, single-owner AI assistant platform. One backend process serves eight specialized agents over a shared memory store, tool registry, and event loop, exposed through a desktop app, an Android app, and Telegram.

It keeps persistent memory across sessions, runs scheduled and event-driven work without anyone present, and acts directly on the owner's own accounts and devices. Anything with a real-world consequence — a payment, a message, a deployment, a destructive command — goes through an explicit approval or safety gate, covered below.

---

## Contents

- [Architecture](#architecture)
- [Agent roster](#agent-roster)
- [Capabilities](#capabilities)
- [Multi-agent coordination](#multi-agent-coordination)
- [Integrations](#integrations)
- [Ecosystem](#ecosystem)
- [Safety and operations protocols](#safety-and-operations-protocols)
- [Clients](#clients)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Deployment](#deployment)
- [License](#license)

---

## Architecture

Everything runs through one FastAPI process (`packages/igor`). Every agent persona, the memory system, the tool registry, and the background job queue share that process, one database, and one event loop — there is no per-agent server or per-agent database.

A single API key authenticates every request. Two channels reach the backend: an HTTP/SSE endpoint for interactive chat, and a WebSocket channel reserved for the one agent (Optimus) that can hand work off to an external peer machine. A third-party workflow engine (n8n) owns all scheduling — the backend itself never runs a clock; it exposes trigger and probe endpoints that n8n calls on a timer.

Tool capabilities are organized in tiers, all exposed to the model through one registry:

| Tier | What it is |
|---|---|
| Legion | Parallel multi-agent research workers, deployed for anything needing independent, isolated sub-tasks |
| Skills | In-process Python capabilities — the bulk of what's listed below |
| MCP servers | Third-party integrations reached over the Model Context Protocol |
| Adapters & sidecars | Full external applications wrapped over HTTP, running in their own containers |

---

## Agent roster

Eight agents, each owning a distinct domain, all built on the same underlying contract and sharing the same memory and tools.

| Agent | Domain |
|---|---|
| **Speda** | Orchestrator and primary point of contact. Plans, delegates across the roster, and holds cross-agent context specialists don't have. |
| **Sentinel** | Finance and budgeting — markets, holdings, and spending turned into numbers behind a decision. Not licensed financial advice. |
| **Orion** | The system's own custodian — memory hygiene, nightly audits, and host maintenance. |
| **Optimus** | Systems, code, and infrastructure. Hands off agentic coding work to a dedicated coding-engine peer when one is connected, falling back to its own engine otherwise. |
| **Centurion** | Security for the owner's own assets — threat intelligence, exposure assessment, hardening. Supports an optional dedicated scanning peer for authorized network work. |
| **Atomix** | Health and wellness coaching — fitness, nutrition, sleep, habits. Not a medical provider. |
| **Ultron** | Academic and work-life planning — coursework, exams, study schedules. |
| **NightCrawler** | OSINT and web research from open sources only. |

Model allocation follows one policy across the roster: the owner's manual per-agent pin always wins; failing that, a deployment default; interactive turns run on a frontier model, automated background turns drop to a cheaper tier from the same provider. Tool access is unrestricted by default — the boundary sits on individual high-consequence skills (restricted to specific agents), not on the agent as a whole.

---

## Capabilities

### Communication & messaging

- Push messages, files, and generated documents directly to the owner's Telegram from any agent's own bot.
- Device push notifications, including to a connected smartwatch.
- Persistent, self-repeating reminders that keep re-asking until answered, with a history of what was taken or missed.

### Calendar, scheduling & automation

- Full control over recurring briefings, reminders, and proactive watchers — page-change hooks, keyword hooks, inbound-mail hooks, RSS watches, webhooks — backed by an n8n workflow engine.
- On-demand loading of additional integrations and skills, so the active prompt only carries what a given task needs.

### Memory, recall & knowledge management

- A durable, schema-enforced memory store: topic-organized documents (who the owner is, ongoing projects, people, running history) that agents read and rewrite over time, not a chat log.
- Shape-aware writers that append to the correct ledger period, create or update a person's or project's own file, or revise one chapter of a biography — without corrupting neighboring entries.
- A sourced, addressable layer of individual facts beneath the memory documents, each traceable to its evidence, ranked by relevance and reinforcement, and softly demoted when it turns out wrong.
- Exact keyword and date search across the full raw conversation history.
- Meaning-based (hybrid vector + keyword) recall across every past conversation, for "did we discuss this" queries that can't be phrased as an exact search term.
- Pattern detection: a fact or behavior observed enough times graduates into a recorded pattern that agents check *before* planning or promising anything, rather than noting it only after the fact.

### Files, storage & documents

- Generate downloadable PPTX, DOCX, and PDF files from Markdown, auto-branded per agent.
- Render a locked, branded training-program PDF from a planned workout.
- Write arbitrary generated text or code to a real downloadable file.
- Browse and file documents in the owner's own cloud vault — read anywhere, write only to designated folders, no delete or rename.
- Pull files produced inside the code sandbox out to the user as a download.

### Code execution & web automation

- Run shell commands in an isolated Linux sandbox with a persistent workspace — Python, pip, git, and data tooling available for real computation, not just text generation.
- Render JavaScript-heavy or blocked pages, drive multi-step browser interactions (click, fill, upload, download, tabs, dialogs), and sign into the owner's own saved web portals without the password ever passing through the model.

### Research, news & OSINT

- Two-tier news system: a free, always-on RSS store with keyword watchlists that flag and push breaking terms, a quota-budgeted deep-analyst tier for cross-outlet corroboration, and full-text article extraction.
- A dozen read-only OSINT and threat-intelligence lookups for authorized security research: IP geolocation and reputation, malicious-URL and malware-IOC feeds, breached-password checking (k-anonymity, password never transmitted), dark-web and leak-metadata search, internet-wide device exposure, domain-based email discovery, and cryptocurrency wallet tracing.

### Health & wellness

- Query synced biometrics — steps, sleep stages, heart rate, exercise sessions, body composition — with trend comparisons and a freshness gate that refuses to answer present-tense questions with stale data.

### Academic

- Read the owner's class attendance ledger and report remaining absence budget per course against the official attendance rule.
- Push a one-tap "did you attend?" prompt to the owner's watch right after class, to keep the ledger accurate without manual entry.

### Navigation, transit & weather

- Live-traffic-aware turn-by-turn directions across drive, walk, transit, and bike modes, with congestion breakdown and an interactive map card.
- Nearby place search, rankable by distance or rating, rendered as tappable map markers.
- Live flight tracking by tail number, flight number, or callsign.
- Live city-bus arrival board for a given stop.
- Current conditions and multi-day forecast for any named place or the owner's home location.

### Voice

- Transcribe uploaded audio to text.
- Convert text to a downloadable spoken response in each agent's own neural voice.

---

## Multi-agent coordination

- **Dispatch** — any agent can hand a task to another specialist, synchronously or as a trackable background job, and read the shared inter-agent conversation log.
- **Legion** — a parallel research swarm, deployed for anything needing three or more independent sources. Each worker is a fully isolated agent loop with no memory of the parent conversation:
  - **Scout** — fast, cheap pre-filter that returns a ranked shortlist of leads before deeper digging.
  - **Researcher** — deep-dives one assigned subtopic across many searches and returns cited findings.
  - **Analyst** — synthesizes gathered findings into the finished briefing or report.
  - **Judge** — audits a drafted report by checking each claim against its source, never writes one.
  - **Archivist** — runs multi-hop searches across the owner's own conversation history.
  - **General** — a catch-all worker with the deploying agent's full toolset.
- **House Party** — an all-hands mode that mobilizes the entire roster in parallel at full model grade. Owner-only, desktop-only, and gated behind a passphrase entered directly by the owner in a window the model never sees. Runs in War Room, a dedicated command channel kept separate from day-to-day agent chats.

---

## Integrations

Reached over the Model Context Protocol, each optional and skipped at startup if unconfigured:

- **Notion** — read, search, create, and edit pages and databases.
- **Google Workspace** — Gmail, Calendar, Tasks, Drive, and Contacts.
- **Microsoft Graph** — Outlook mail.
- **Brave Search, Tavily, Exa** — general and research-grade web search.
- **Fetch** — turns a URL into readable Markdown.
- **GitHub** — repository, issue, and code operations.
- **Alpha Vantage** — financial market data.
- **arXiv** — academic paper search.
- **CVE intelligence** — security vulnerability lookups.
- **Filesystem** — read/write scoped to the assistant's own output directory.
- **Playwright MCP** — full browser automation for the open public web, in its own container, never used for the owner's saved logins.
- Owners can register their own custom MCP servers by command or URL.

**Adapters & sidecars** — full external applications, each isolated in its own container:

- **Deep research** — a wrapped open-source research engine for comprehensive, multi-source reports too broad for individual tool calls.
- **Security analysis** — a wrapped security toolkit for vulnerability scans, CVE lookups, and network reconnaissance against a named target.
- **Sandbox** — a no-secrets container that executes arbitrary shell commands with a hard timeout, giving the assistant a real, stateful command line without touching the API container or its credentials.
- **Browser** — the assistant's eyes on the web: renders pages, returns readable text and an accessibility snapshot of what's clickable, and holds the owner's portal sessions in a persisted per-profile cookie jar so a password never becomes text the model produces.

---

## Ecosystem

Two companion products extend the platform beyond this repository. Neither ships as a package here — each is a separate, owner-run service that the backend connects to as a client, sitting in its own repository.

**Hisar** — the owner's own self-hosted cloud filesystem and web desktop: a real vault of folders (Documents, Media, Projects, Desktop) that agents work inside as guests, not as the backend's storage layer. Any agent with the Hisar skill enabled can read anywhere in the vault; writes are confined to a dedicated folder, never overwrite an existing file, and deleting or renaming isn't reachable from an agent at all — those stay owner-only, inside Hisar itself. It runs as an optional companion service alongside the backend.

**Forge** — a standalone execution engine that runs coding-agent work on a dedicated machine, on behalf of Optimus and Centurion. It holds its own model credentials and makes its own inference calls — the backend hands it jobs, never proxies inference through it. While a Forge peer is connected, that agent's turns route to it; the moment it disconnects, the corresponding agent falls back to its own in-process profile, with one function owning that decision so a turn never lands on the wrong machine. Once a night, the memory custodian pushes a fresh summary of the owner's memory to any connected peer, keeping Forge's picture of the owner no more than a day stale.

---

## Safety and operations protocols

Explicit, high-consequence capabilities, most restricted to a small set of trusted agents and gated to refuse unless triggered directly by the owner in conversation:

- **Skyfall** — a countdown-gated remote trigger. The tool can only arm the countdown; only the owner's own client can fire it, and letting the clock run out or aborting are the only two outcomes. Nothing fires that the owner didn't personally watch happen.
- **Octavius** — automated off-site backups of the entire system (chat history, memory, background jobs) to the owner's own Drive, taken as a consistent snapshot, integrity-checked, and confirmed against what was actually stored before old copies are pruned. Credentials are never included in a backup.
- **Doormat** — a staged, reversible way to move the deployment to a new domain: the old address stays live until the new one is proven, and retirement is a deliberate final step, not an automatic one.
- **Lockdown** — an emergency network containment switch that seals inbound SSH and the raw app port from the outside world in one action, while keeping the web app and the owner's own remote-control channel alive.
- **Lifeboat** — tiered disk-space monitoring and cleanup: reversible cache cleanup can run autonomously when disk is critically full; anything destructive requires explicit owner approval.
- **System operations** — direct host-machine access for the ops-custodian agents: shell execution behind a hard deny-list, file access jailed to an ops root, and safe container restarts.
- **Budget mode** — a cost-saving toggle that shortens answers, limits search, and disables expensive sub-agent workers.

---

## Clients

| Client | Platform | Description |
|---|---|---|
| **Heartbreaker** | Electron desktop (React, TypeScript) | Full-roster command deck — switch between every agent persona, see the whole system. |
| **Striker** | Electron desktop (React, TypeScript) | Single-agent public build, forked from Heartbreaker. No roster, no switcher, Speda only. |
| **Speda GO** | Android (Kotlin, Jetpack Compose) | Native mobile client with Health Connect sync for Atomix and push-driven interactions. |
| **Telegram** | Any device | Each agent runs its own bot; a running response can be steered mid-generation instead of starting a competing reply. |

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, uvicorn, SQLAlchemy (async), Alembic |
| Database | SQLite (production), Postgres-compatible |
| LLM / AI | Anthropic SDK, OpenAI SDK, Model Context Protocol |
| Embeddings & search | OpenAI embeddings, brute-force vector search + full-text keyword search, fused |
| Background work | Database-backed job queue with durable drain; n8n for all scheduling |
| Desktop | Electron, React, TypeScript, electron-vite |
| Mobile | Kotlin, Jetpack Compose, Health Connect, WorkManager, Firebase Cloud Messaging |
| Browser automation | Playwright (owner-login sidecar) and the official Playwright MCP server (open web), each containerized separately |
| Code execution | Isolated Python/shell sandbox container |
| Infrastructure | Docker Compose, Caddy (reverse proxy, automatic HTTPS), n8n, GitHub Actions |

---

## Repository layout

| Package | Description |
|---|---|
| `packages/igor` | The backend — every agent, the memory system, the tool registry, the orchestrator |
| `packages/heartbreaker` | Full-roster desktop client |
| `packages/striker` | Single-agent public desktop client |
| `packages/speda-go` | Android client |
| `packages/browser` | Playwright sidecar for rendering pages and the owner's portal logins |
| `packages/playwright-mcp` | Official Playwright MCP server, open web only |
| `packages/sandbox` | Isolated code-execution sidecar |

---

## Deployment

The stack runs as a set of Docker Compose services on one internal network — the backend, n8n, the sandbox, the browser sidecar, and Playwright MCP — with only the backend and an optional Caddy reverse proxy exposed. A GitHub Actions pipeline deploys the backend over SSH on every push to `main` that touches backend code; the desktop and Android clients build and publish as signed releases on their own triggers.

---

## License

AGPL-3.0. See [LICENSE](LICENSE). Running a modified version of this software as a network service requires making that modified source available to its users under the same license.
