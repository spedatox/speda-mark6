<div align="center">


# S.P.E.D.A. Mark VI

### Specialized Personal Executive Digital Assistant Mark VI

**A private, proactive, multi-agent executive assistant** — a suite of eight
domain specialists that watch the world for you, act while you sleep, remember
who you are, and answer through a holographic command deck straight out of a
Stark lab.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![Electron](https://img.shields.io/badge/Heartbreaker-Electron%20%2B%20React-47848F?logo=electron&logoColor=white)
![Agents](https://img.shields.io/badge/agents-8-2eb6ac)
![Providers](https://img.shields.io/badge/LLM%20providers-6-8a7fd6)

*Not a chatbot. Not multi-tenant SaaS. One owner, one key, one assistant that is actually **yours**.*

</div>

---

## What makes it different

Most "AI assistants" wake up, answer a question, and forget you exist. SPEDA is
built on the opposite premise — a **single owner** it knows deeply, a **standing
staff** of specialists, and **senses of its own** that fire without being asked.

- 🧠 **It knows you.** Persistent, structured memory of who you are, what you're
  working on, and how you like things — carried across every conversation.
- 📡 **It acts on its own.** Watchers monitor pages, feeds, markets and schedules
  and ping your Telegram the moment something matters — no prompt required.
- 👥 **It's a team, not a bot.** Eight agents with distinct domains, voices and
  colours, dispatching work to each other behind the scenes.
- 🎛️ **It looks incredible.** *Heartbreaker*, the desktop client, is a
  fluid-glass holographic HUD — a genuine command deck, not a chat window.
- 🔒 **It's private.** Self-hosted, single-key, no accounts, no tenancy. Your
  data stays on your server.

---

## Meet the roster

Eight agents share one brain, one memory of you, and one event loop — but each
carries its own identity, model policy, toolset and signature colour. Address
any of them directly; they quietly hand work to each other when a task belongs
to someone else's domain.

| Agent | Mark | Domain | What they do |
|---|---|---|---|
| 🟦 **SPEDA** | Mark VI | Chief of Staff | Your main assistant — plans, routes, runs automations, commands the others. |
| 🟨 **Sentinel** | Mark II | Finance & Budget | Markets, portfolios, spending, cost discipline. |
| 🟪 **NightCrawler** | Mark III | OSINT & Surveillance | Lawful web recon, threat intel, the **News Desk**, watchers. |
| ⬜ **Ultron** | Mark III | Academy & Work | Research synthesis, literature, deep analytical work. |
| 🟥 **Centurion** | Mark I | Cyber Security | Vulnerability & CVE analysis, scanning, threat intelligence. |
| 🟩 **Atomix** | Mark I | Health & Wellness | Personal health, fitness, nutrition, recovery. |
| 🟩 **Optimus** | Mark II | Systems, Code & Infra | Real coding on real machines via **The Forge**. |
| 🟪 **Orion** | Mark I | Maintenance & Memory | The system's own custodian — keeps SPEDA's memory and house in order. |

> **Rebrandable to the core.** Identity lives entirely in prompt + profile files;
> the engine holds zero identity strings. The same codebase ships as any agent —
> a fully branded standalone app — by flipping one build flag.

---

## The features

### 🎛️ Heartbreaker — a command deck, not a chat box

The desktop client is a Stark-tech, holographic *fluid-glass* interface — the
product's primary face, and unlike anything else in the assistant space.

- **Cinematic agent switcher** — hit `Alt+A` and select your agent like Tony
  Stark picking an armour: the room glows in the agent's colour, dual HUD rings
  spin up around the chosen one, a live spec panel reads out its designation.
- **Systems Board** — a live telemetry deck: model-routing matrix, active
  toolset shards, token budget, response-time trace, your memory data-banks, and
  the Forge-link status — all real data, nothing decorative.
- **Comms tray** — watch your agents talk to each other in real time, with live
  "working…" timers on background jobs.
- **JARVIS welcome** — every home screen opens with a contextual, memory-aware
  one-liner in the current agent's voice ("Friday's workout complete, ready for
  weekend recharge") — generated fresh, cached so it's instant.
- **Per-session pulse** — a running job keeps streaming even if you switch away;
  a glowing jewel marks any conversation that's still cooking.

### 🛡️ The Legion — disposable workers on any model

When a job genuinely needs deep research — six searches across six subtopics —
an agent deploys **The Legion**: anonymous, single-purpose worker agents
(*scout* pre-filters sources, *researchers* deep-dive one subtopic each in
parallel, an *analyst* synthesises, a *judge* verifies the draft). Workers are
provider-agnostic: they run on the cheap tier of **whatever model you're
chatting on** — Claude, GPT, GLM, Gemini, even local Ollama — never locked to
one vendor. Fire-and-forget background workers return a ticket and land their
findings in the comms tray. Legionnaires have no identity and no memory; they
are not the Superior Six — they're the grunts.

### ⚒️ The Forge — Optimus codes on real machines

Optimus isn't a chatbot pretending to write code. When **The Forge** is online,
Optimus runs on a standalone execution engine with privileged shell and code
execution, isolated in its own sandboxed *Cell*, understanding your codebase
through a graph index. It reads, writes, runs, tests and iterates — full agentic
coding — then reports back. The header shows a live **FORGE LINK** jewel and a
workspace picker; when the Forge is offline, Optimus gracefully falls back to
its in-process self. No hard dependency, ever.

### 📡 Proactive automations — it works while you're away

n8n is SPEDA's nervous system. Just *tell* it what to watch:

> *"Track this page for a month and tell me when the results are up."*

SPEDA composes the watcher itself, arms it, and — when it fires — writes you a
proper message and delivers it to **Telegram**, unprompted. Watch web pages for
changes or keywords, follow RSS feeds, schedule morning briefings, or open
inbound webhooks. Time-boxed watchers self-expire. Manage them in chat or in the
Settings panel. Every agent even has its **own Telegram bot**, so a Sentinel
alert speaks in Sentinel's voice.

### 📰 The News Desk — a two-tier intelligence operation

A professional news desk built into the assistant. **Tier 1** is an always-on,
zero-cost RSS watcher over Turkish + international outlets — deduplicated
headlines, breaking-news keyword alerts ("flag anything about OSTİM"), and a
daily briefing. **Tier 2** is the analyst layer (NewsData.io) for corroboration,
story timelines and historical search — quota-budgeted so it's never wasteful.
It reads full articles for free when it can, and only spends the paid tier when
the free one can't answer.

### 🧠 Memory that actually lasts

SPEDA keeps a structured, Markdown-based memory of you — who you are, what's
current, a behavioural dossier, your projects, a rolling log — readable and
editable by the agents themselves and refreshed by background tasks after every
exchange. **Orion** is the dedicated custodian that keeps it clean. The result:
an assistant that remembers your last conversation, your ongoing work, and your
preferences without you repeating yourself.

### ♾️ Nothing gets lost — survivable turns & background work

Turns run **detached from your connection**. Reload the app, switch agents, close
the window mid-answer — the work keeps running server-side, saves itself, and
re-attaches live when you come back. Hand a long job to another agent in the
**background** and keep chatting while it works; check on it any time. A running
answer is never a hostage to your network.

### 🚨 House Party Protocol — all hands on deck

For genuinely high-stakes moments, SPEDA becomes mission commander and summons
the **entire roster in parallel** at full model grade with domain boundaries
relaxed. It's deliberately heavy and passphrase-gated: a secure authorization
window (masked passphrase field, validated server-side) unlocks it, and the
whole client transforms into a **War Room** dashboard until you stand down.

### 🧩 One assistant, many brains

Every model call flows through one router that speaks fluent **Anthropic,
OpenAI, Gemini, z.ai (GLM), DeepSeek, NVIDIA NIM and local Ollama**. Pick a model
per agent, set a fallback chain, or run entirely local. Switching providers
changes nothing about how the system behaves — the whole engine speaks one
internal format and normalizes the rest at the edge.

### 🛠️ A deep capability arsenal

Under the hood, a unified four-tier tool system the agents draw on seamlessly:

- **Research & web** — Tavily, Exa, Brave, Fetch, arXiv, and a full GPT-Researcher
  deep-research engine.
- **OSINT & security** — IP geolocation & reputation, URLhaus / ThreatFox /
  MalwareBazaar, HaveIBeenPwned, Shodan, OTX, dark-web search, CVE intelligence,
  blockchain tracing, breach discovery, and automated scanning.
- **Productivity** — Notion, Gmail & Google Calendar (self-refreshing OAuth),
  GitHub.
- **Creation** — branded PDF / DOCX / PPTX generation (Turkish-ready fonts),
  code & file authoring, an isolated Linux **sandbox** for running real commands.
- **Voice** — Whisper speech-to-text and Kokoro text-to-speech.
- **Recall** — literal history search and semantic vector recall over your past
  conversations.

Only what's needed sits in context; the rest loads on demand, with prompt
caching so the cost of a huge toolset rounds to nothing. A one-switch **budget
mode** clamps everything down when you want it lean.

---

## See it running

```bash
# 1. Configure
cp .env.example packages/api/.env      # set ANTHROPIC_API_KEY + SPEDA_API_KEY

# 2. Igor, the backend (SQLite by default — no services needed)
cd packages/api && uv sync && uv run uvicorn app.main:app --port 8000 --reload

# 3. The command deck (repo root, new terminal)
npm install && npm run heartbreaker:dev
```

On Windows, **`speda.ps1`** boots the whole system — backend, sandbox, the Forge
link, and the app — with a single command.

**Full stack** (`docker compose up -d`) brings up Postgres, the app, the sandbox,
and n8n together. **`./deploy.sh`** is the one-shot production runbook (server,
domain + TLS, memory import, app packaging). Ship a branded desktop installer for
any agent with **`build-app.ps1 -Agent <name> -ApiBase <url> -ApiKey <key>`**.

---

## Under the hood

**Igor** is the backend — a FastAPI agentic core with a strict architectural
spine: routers hold zero business logic, one orchestrator owns the system prompt
and the agentic loop, everything is provider-agnostic behind one LLM client, and
n8n is the only scheduler. Heartbreaker is the face; Igor is the brain and
hands. The full contract is codified in **[`CLAUDE.md`](CLAUDE.md)**.

**Deeper docs** live in [`docs/`](docs/):

| Doc | What |
|---|---|
| [REFERENCE.md](docs/REFERENCE.md) | Full capability catalog, HTTP API, and configuration reference |
| [SETUP.md](docs/SETUP.md) · [DEPLOY.md](DEPLOY.md) | Install & production runbook |
| [MEMORY_ARCHITECTURE.md](docs/MEMORY_ARCHITECTURE.md) | How memory works |
| [TELEGRAM_ARCHITECTURE.md](docs/TELEGRAM_ARCHITECTURE.md) | The bot fleet |
| [FORGE_INTEGRATION_PLAN.md](docs/FORGE_INTEGRATION_PLAN.md) · [NEWS_BRIEFING_PLAN.md](docs/NEWS_BRIEFING_PLAN.md) · [BACKGROUND_OPS_PLAN.md](docs/BACKGROUND_OPS_PLAN.md) | Design notes for the newest systems |

**Monorepo:** `packages/api` (**Igor** — the backend) · `packages/heartbreaker` (the app) ·
`packages/desktop` (neutral fork base) · `packages/sandbox` (the isolated
computer). The Forge is a separate deployment that connects back as a peer.

---

<div align="center">

**S.P.E.D.A.** — Specialized Personal Executive Digital Assistant · Mark VI

Built by **Ahmet Erol Bayrak** ([@spedatox](https://github.com/spedatox))
· Private project — not licensed for redistribution.

</div>
