# A local Forge peer

Optimus runs on the server. When the owner asks it to build something "here",
"here" is `/opt/forge-mk1` — not their PC. On 2026-08-04 that produced a
complete, working multipage site inside a directory named
`C:\Users\AREL TARIM\Downloads\Yeni klasör`, because `\` and `:` are legal
filename characters on Linux and nothing failed. The site was fine. It was on
the wrong machine, and Optimus reported success.

This describes running Forge on the owner's PC as well, so Optimus can work in
a folder or repo on that machine.

## One Optimus, two places it can run

This does not create a second Optimus. There is one agent: one profile
(`app/profiles/optimus.py`), one set of sessions, one memory, one entry in the
roster. What gets added is a second place its tools can execute.

That works because the peer holds no state. `ExternalAgentProxy` ships the full
conversation history over the socket on every turn and the backend DB stays the
source of truth — truncate, regenerate and edit all mutate it server-side, so a
stateful peer would drift. The peer is a pair of hands, not a brain. Two pairs
of hands attached to the same brain is still one agent, which is why "same
memory on both machines" needs no synchronisation: there is only one copy and
it never leaves the server.

`host` is therefore a transport detail and never an identity. It does not
appear in `agent_id`, in the roster, or in any prompt.

## What already works

The peer dials **out** to Igor — `wss://<host>/agents/ws/{agent_id}` with an
`X-API-Key` header. A PC behind NAT needs no port forward, no static IP and no
inbound firewall rule. Forge is already a client of Igor rather than the
reverse, so running it on Windows is a packaging problem, not a protocol one.

Forge also already supports a non-Docker cell: `FORGE_CELL_BACKEND=subprocess`
(`forge/cell/subprocess_cell.py`). The PC does not need Docker Desktop.

## Host-aware routing — landed

`WebSocketManager._connections` used to map `agent_id → WebSocket`, one slot per
agent:

```python
self._connections[agent_id] = websocket   # last connect silently wins
```

Both peers register as `optimus`, so the second connection replaced the first
with no error, and a laptop sleeping and waking traded the slot back and forth.

It now maps `agent_id → host → WebSocket`, with the peer's platform and roots
recorded alongside.

### Registration

`AgentRegistration` (`app/schemas/agent.py`) carries three extra fields:

| Field | Example | Purpose |
|---|---|---|
| `host` | `"arel-pc"` | Identifies this peer among peers of the same agent |
| `platform` | `"windows"` | How to validate a path aimed at it |
| `roots` | `["C:\\Users\\AREL TARIM\\repos"]` | Directories this peer will work in |

All three default — `host="default"`, `platform="linux"`, `roots=[]` — so the
peer currently deployed on the server keeps working without being redeployed.

A peer advertising **no roots** accepts any path well-formed for its platform.
That is exactly the server peer's existing behaviour, and preserving it is what
makes this change invisible to the running deployment.

### Choosing a peer

`app/core/peer_routing.py` owns this decision and nothing else makes it.

1. **The task names a directory** → the peer whose `roots` contain it. This is
   the normal case and it needs no decision from the owner: they pick a folder,
   and the folder identifies the machine. There is no "local mode" to remember.
2. **No directory** → the always-on server peer. A PC that is asleep should
   never be the default for work that does not need it.
3. **A directory no connected peer claims** → refuse immediately, naming what
   is connected. Do not fall through to another peer, and do not wait out
   `EXTERNAL_CODING_TIMEOUT_S` (600s) to say so.

Rule 3 is the one that matters. The failure this whole document exists to
prevent is work silently happening in the wrong place, and a fallback is
exactly how that happens again.

That has a sharp edge in `AgentDispatcher._dispatch_body`, which degrades to
the in-process profile when an external dispatch comes back `offline` or
`error`. An unroutable path therefore returns the distinct status **`refused`**,
which is deliberately absent from that list — running it in-process would put
the work on the server after the owner asked for a folder on their PC, which is
the original bug wearing a different hat.

### Where the decision is made

Four call sites choose between a peer and the in-process profile. All of them
go through the same routing:

| Call site | Trigger |
|---|---|
| `routers/chat.py` | the owner's own turn |
| `core/trigger_runner.py` | n8n (cron, watchdogs, briefings) |
| `telegram/gateway.py` | a message to an agent's Telegram bot |
| `core/dispatch.py` | another agent, including House Party |

n8n and House Party work should stay on the server. Both run unattended, and an
all-hands or a 03:00 briefing that half-fails because a laptop is asleep is
worse than one that runs server-side. Neither passes a `cwd`, so rule 2 already
sends them there — but that is the reason rule 2 prefers the always-on peer,
not a coincidence to rely on silently.

## House Party Protocol

Unaffected, and it has to stay that way.

The broadcast roster comes from `AgentDispatcher.known_agents()`, which reads
the **ProfileRegistry** — never the connection table:

```python
return [p.agent_id for p in self._profiles.roster() if p.dispatch_target]
```

So Optimus appears in a fan-out exactly once, whether it is attached from one
machine or three. Each entry then resolves to a single host through the rules
above, so one broadcast produces one task on one machine.

Two things protect that invariant:

- **The roster must never be built from `WebSocketManager`.** Fanning out over
  connections instead of profiles would dispatch to every attached machine and
  return two answers from "one" agent — precisely the second Optimus this
  design refuses. `WebSocketManager.broadcast()`, a dead stub commented "House
  Party Protocol stub", was removed so nothing gets wired to it by mistake.
- **`connected_agents()` is deduplicated by `agent_id`**, for the same reason.

Model policy is untouched: House Party escalates every agent to interactive
grade via `profile.allocate_model("user")`, and where a peer executes has no
bearing on that.

## Allowlisted roots

The peer holds its own list of directories it will work in, in a config file on
the PC. A task aimed anywhere else is refused by the peer, before it runs —
not by the server.

The server cannot widen this. It advertises the roots at registration so Igor
can route by them, but enforcement is local, so a compromised or confused
server cannot talk the peer into `C:\Windows` or a wallet directory.

## Path validation is peer-relative — landed

`ChatRequest.cwd` and `dispatch_agent(working_directory)` used to reject Windows
paths outright. That was right when every peer was Linux and wrong once one is
the owner's PC, where `C:\Users\…` is the correct answer.

Both flat guards are gone. A Pydantic schema has no access to `app.state` and
so cannot know which machines are attached — it now only trims whitespace. The
decision moved to `peer_routing.resolve()`, called with the live peer list on
the two paths that actually reach a peer: `ExternalAgentProxy.run` for chat
turns and `AgentDispatcher._run_external` for dispatched tasks.

| Path | Attached peers | Result |
|---|---|---|
| `/opt/forge-mk1/x` | server (linux) | runs on the server |
| `C:\Users\…\repo` | server only | refused — the 2026-08-04 case |
| `C:\Users\…\repo` | server + PC, path in roots | runs on the PC |
| `C:\Windows\System32` | server + PC | refused — outside the PC's roots |
| unset | any | the always-on peer |

## Failure modes

- **PC asleep.** The common case, not an edge case. A dispatch aimed at a root
  whose peer is not connected fails immediately with which hosts *are*
  connected.
- **Reconnect flapping.** Per-host slots mean a reconnecting laptop can never
  displace the server peer.
- **One machine drops mid-turn.** `ExternalAgentProxy.fail_agent` and
  `AgentRegistry.deregister` both take a host and act only on that one. The
  agent goes offline when its **last** machine leaves, not its first.
- **Two PCs.** Falls out of the design at no extra cost.
- **Server compromise.** This gives the server the ability to drive file
  operations on the owner's machine. The allowlist bounds it; the credential
  rotation below is what keeps the door shut.

## Build order

**0. Rotate the credential first.** Rotating means generating a new random
secret and replacing the old value everywhere it is configured — server env,
both peers, both clients — so that the previous string stops being accepted. It
is not a transformation of the existing key.

`SPEDA_API_KEY` is currently the single shared secret and it has been sitting
in Caddy's request logs in plaintext (`X-Api-Key` is logged in full on every
502). It is about to become the credential that reaches the owner's filesystem,
so it should not be one that has been in a log file. Rotate it, and stop Caddy
logging the header.

Worth doing at the same time: a separate secret for peer connections, so the
app key and the machine-access key are not the same string.

1. ~~Host-aware `WebSocketManager` + the registration fields.~~ **Landed.**
2. ~~Peer selection by root, including the refuse-don't-fall-through rule.~~
   **Landed** (`app/core/peer_routing.py`).
3. ~~Peer-relative path validation replacing the flat Windows rejection.~~
   **Landed.**
4. The Windows peer itself: `FORGE_CELL_BACKEND=subprocess`, the roots config,
   and whatever supervises it (Task Scheduler or a tray app — it needs to
   survive a reboot without the owner starting it by hand). Forge must send
   `host`, `platform` and `roots` in its registration handshake.
5. A directory picker in the desktop client that sends `cwd`, so selecting the
   folder is the whole interaction.

Steps 1–3 were server-side and are done; the deployed server peer needs no
change to keep working, because every new field defaults to what it already
does. Step 4 is the first one that requires anything to be installed on the PC.
