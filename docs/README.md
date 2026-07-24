# SPEDA Mark VI — Documentation

The complete documentation index for **SPEDA Mark VI** — the private,
proactive, multi-agent executive assistant (backend codename **Igor**, desktop
client **Heartbreaker**, sub-agent workers **The Legion**).

Start with the [project README](../README.md) for the tour, then use this page
as the map to everything else. Docs fall into three kinds:

- 📘 **Reference** — the current, binding description of how the system works.
- 🧩 **Component docs** — one per package, living next to the code they describe.
- 📐 **Design notes** — the plans behind each subsystem. They record *why* and
  *how* a system was built and remain useful as deep dives, but the
  [Technical Reference](REFERENCE.md) and the component docs are authoritative
  where they disagree.

---

## Start here

| Doc | What it covers |
|---|---|
| [../README.md](../README.md) | Product overview — the roster, the features, the tour |
| [../CLAUDE.md](../CLAUDE.md) | **The architectural contract** — the non-negotiable rules the backend is built to |
| [REFERENCE.md](REFERENCE.md) | 📘 Technical reference — capability catalog, HTTP API, LLM routing, configuration |
| [SETUP.md](SETUP.md) | Complete setup guide — local dev through first run |
| [../DEPLOY.md](../DEPLOY.md) | Production runbook (Contabo / Docker / TLS) |

---

## Component docs

One package, one doc. These live beside the code and describe what actually
ships today.

| Component | Doc | Role |
|---|---|---|
| **Igor** (backend) | [../packages/igor/IGOR.md](../packages/igor/IGOR.md) | FastAPI agentic core — orchestrator, registry, skills, memory, automations, news, turns |
| **Heartbreaker** (desktop client) | [../packages/heartbreaker/HEARTBREAKER.md](../packages/heartbreaker/HEARTBREAKER.md) | The primary UI — Stark fluid-glass command deck (Electron + React) |
| **Striker** / *SPEDA Mark VI Core* | [../packages/striker/STRIKER.md](../packages/striker/STRIKER.md) | The single-agent, public "lite" build — same features, calmer theme, SPEDA only |
| **Heartbreaker Core** (Android) | [../packages/heartbreaker-android/README.md](../packages/heartbreaker-android/README.md) · [fonts](../packages/heartbreaker-android/docs/FONTS.md) | Native Kotlin + Compose client — the deck in your pocket |
| **Sandbox** | *(no standalone doc — see [`packages/sandbox/server.py`](../packages/sandbox/server.py) and the `run_command` skill)* | The isolated Linux "capable computer" |

> **Package layout.** `packages/igor` (backend) · `packages/heartbreaker`
> (primary desktop/web UI) · `packages/striker` (single-agent lite build,
> re-themed from the old `packages/desktop` neutral base) ·
> `packages/heartbreaker-android` (native mobile) · `packages/sandbox` (isolated
> computer). The Forge is a separate deployment that connects back as a peer.

---

## Subsystem design notes

Deep dives on how each major system was designed and built.

### Memory & recall
| Doc | What |
|---|---|
| [MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md) | The v2 "Orion Charter" — the `/memories` file law, episodic recaps, semantic recall, and Orion the custodian |
| [MEMORY_REVISION_R1.md](MEMORY_REVISION_R1.md) | Revision R1 — the "semantics correction" refinement pass |

### Proactivity & automation
| Doc | What |
|---|---|
| [TELEGRAM_ARCHITECTURE.md](TELEGRAM_ARCHITECTURE.md) | The per-agent Telegram bot fleet — proactive push delivery |
| [NEWS_BRIEFING_PLAN.md](NEWS_BRIEFING_PLAN.md) | The two-tier News Desk (always-on RSS + NewsData.io analyst layer) |
| [BACKGROUND_OPS_PLAN.md](BACKGROUND_OPS_PLAN.md) | Non-blocking dispatch & survivable, detached turns |

### Optimus & The Forge
| Doc | What |
|---|---|
| [FORGE_INTEGRATION_PLAN.md](FORGE_INTEGRATION_PLAN.md) | Wiring Optimus's Mark II execution engine (The Forge) into Mark VI + Heartbreaker |
| [HISAR_FORGE_PLACEMENT_PLAN.md](HISAR_FORGE_PLACEMENT_PLAN.md) | H.İ.S.A.R. × Forge placement decisions |

### Clients & surfaces
| Doc | What |
|---|---|
| [ANDROID_PORT_PLAN.md](ANDROID_PORT_PLAN.md) | Heartbreaker Core — the parity contract with the desktop client |
| [STARK_MAPS_PLAN.md](STARK_MAPS_PLAN.md) | Inline maps & traffic-aware navigation |

### Health & platform
| Doc | What |
|---|---|
| [ATOMIX_HEALTH_SYNC.md](ATOMIX_HEALTH_SYNC.md) | Samsung Health → Igor → Atomix ingestion pipeline |
| [../MULTI_TENANT_PLAN.md](../MULTI_TENANT_PLAN.md) | The in-process multi-agent (profile) architecture blueprint |

---

## The roster

Eight agents share one backend, one memory, and one event loop; each is an
in-process `AgentProfile` in [`packages/igor/app/profiles/`](../packages/igor/app/profiles/)
except Optimus, which is a standalone deployment connecting back over WebSocket.

| Agent | Domain | Profile |
|---|---|---|
| **SPEDA** | Chief of Staff — plans, routes, commands the roster | `profiles/speda.py` |
| **Sentinel** | Finance & budget | `profiles/sentinel.py` |
| **NightCrawler** | OSINT, surveillance, the News Desk | `profiles/nightcrawler.py` |
| **Ultron** | Academic research & synthesis | `profiles/ultron.py` |
| **Centurion** | Cyber security & CVE intelligence | `profiles/centurion.py` |
| **Atomix** | Personal health & wellness | `profiles/atomix.py` |
| **Optimus** | Systems, code & infra (via The Forge) | `profiles/optimus.py` *(external peer)* |
| **Orion** | Maintenance & memory custodian | `profiles/orion.py` |

The **House Party Protocol** (`profiles/warroom.py`) engages the full roster in
parallel for high-stakes moments. See the [README](../README.md#-house-party-protocol--all-hands-on-deck).

---

## Where to look for…

- **The rules the code must obey** → [CLAUDE.md](../CLAUDE.md)
- **What tools exist / the HTTP API / env vars** → [REFERENCE.md](REFERENCE.md)
- **Getting it running locally** → [SETUP.md](SETUP.md)
- **Shipping it to a server** → [DEPLOY.md](../DEPLOY.md) (and [`deploy.sh`](../deploy.sh))
- **How a specific package works** → the component doc beside it (table above)
- **Why a subsystem is built the way it is** → its design note (tables above)
