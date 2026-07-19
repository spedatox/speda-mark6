# Igor — the SPEDA Mark VI backend

**Igor is the backend core of SPEDA Mark VI.** One FastAPI process holding the
event loop, the database, the owner's shared memory, the orchestrator, and every
capability tier — the brain and hands of the whole system. Heartbreaker
(`packages/heartbreaker`) is the face; Igor does the work.

> Machine identifiers keep their historical names (`pyproject` name
> `speda-mark-vi`, docker-compose service `app`) — "Igor" is the component's
> name everywhere humans and agents refer to it.

## What lives in Igor

| Organ | Where | What |
|---|---|---|
| The orchestrator | `app/core/orchestrator.py` | Owns the system prompt and the agentic loop — the only place either exists |
| The roster | `app/profiles/` | SPEDA + the Superior Six as in-process `AgentProfile`s; all identity lives here, zero in core |
| **The Legion** | `app/legion/` | Disposable worker agents (wire name `Task`): scout / researcher / analyst / judge / general, provider-agnostic model resolution, background tickets |
| Capabilities | `app/core/registry.py` | Four tiers behind one interface: the Legion (0), Python skills (1), MCP servers (2), OSS adapters (3) |
| Memory | `app/skills/memory.py`, `app/services/memory.py` | The `/memories` file law, episodic session recaps, semantic recall |
| Sessions & turns | `app/core/session_manager.py`, `app/core/turn_runner.py` | Per-agent sessions, detached turns with replay/reattach |
| Transport | `app/routers/chat.py`, `app/websocket/` | HTTP+SSE chat, Flutter WS, and the Optimus peer socket |
| Automations | n8n (external) → `POST /trigger/{agent_id}` | Igor never schedules anything internally |

## Running Igor

```bash
cd packages/igor && uv sync && uv run uvicorn app.main:app --port 8000 --reload
```

Or `speda.ps1` at the repo root to boot the whole system. Local test runs:
set `TELEGRAM_MODE=off` and a scratch `DATABASE_URL` so a dev instance never
fights the production Telegram pollers or writes to the live database.

The full architectural contract — build order, the twelve non-negotiable rules,
the Legion policy (D-SA1–5) — is codified in [`CLAUDE.md`](../../CLAUDE.md).
