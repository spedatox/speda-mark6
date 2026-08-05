# A local Forge peer

Optimus runs on the server. When the owner asks it to build something "here",
"here" is `/opt/forge-mk1` — not their PC. On 2026-08-04 that produced a
complete, working multipage site inside a directory named
`C:\Users\AREL TARIM\Downloads\Yeni klasör`, because `\` and `:` are legal
filename characters on Linux and nothing failed. The site was fine. It was on
the wrong machine, and Optimus reported success.

This describes a second Forge peer that runs on the owner's PC, so Optimus can
work in a folder or repo on that machine.

## What already works

The peer dials **out** to Igor — `wss://<host>/agents/ws/{agent_id}` with an
`X-API-Key` header. A PC behind NAT needs no port forward, no static IP and no
inbound firewall rule. Forge is already a client of Igor rather than the
reverse, so running it on Windows is a packaging problem, not a protocol one.

Forge also already supports a non-Docker cell: `FORGE_CELL_BACKEND=subprocess`
(`forge/cell/subprocess_cell.py`). The PC does not need Docker Desktop.

## The problem to solve first

`WebSocketManager._connections` maps `agent_id → WebSocket`, one slot per
agent:

```python
self._connections[agent_id] = websocket   # last connect silently wins
```

Both peers register as `optimus`. The second connection replaces the first with
no error. A laptop sleeping and waking would trade the slot back and forth, and
each dispatch would land on whichever peer connected most recently.

## Host-aware routing

`optimus` stays one agent. The host it runs on becomes a routing detail, chosen
by where the work lives rather than by the owner remembering a name.

### Registration

`AgentRegistration` (`app/schemas/agent.py`) gains three fields:

| Field | Example | Purpose |
|---|---|---|
| `host` | `"arel-pc"` | Identifies this peer among peers of the same agent |
| `platform` | `"windows"` | How to validate a path aimed at it |
| `roots` | `["C:\\Users\\AREL TARIM\\repos"]` | Directories this peer will work in |

All three default so an existing peer that sends none of them keeps working:
`host="default"`, `platform="linux"`, `roots=[]`.

### The connection map

```python
self._connections: dict[str, dict[str, WebSocket]] = {}   # agent_id → host → ws
```

`connect`/`disconnect` take a host. `send`, `is_connected` and the dispatch
paths take an optional host and fall back to a chosen default when none is
given. `connected_agents()` keeps its current signature so existing callers do
not change.

### Choosing a peer

1. **The task names a directory** → the peer whose `roots` contain it. This is
   the normal case and it needs no decision from the owner: they pick a folder,
   and the folder identifies the machine.
2. **No directory** → the always-on server peer. A PC that is asleep should
   never be the default for work that does not need it.
3. **A directory no connected peer claims** → refuse immediately, naming what
   is connected. Do not fall through to another peer, and do not wait out
   `EXTERNAL_CODING_TIMEOUT_S` (600s) to say so.

Rule 3 is the one that matters. The failure this whole document exists to
prevent is work silently happening in the wrong place, and a fallback is
exactly how that happens again.

## Allowlisted roots

The peer holds its own list of directories it will work in, in a config file on
the PC. A task aimed anywhere else is refused by the peer, before it runs —
not by the server.

The server cannot widen this. It advertises the roots at registration so Igor
can route by them, but enforcement is local, so a compromised or confused
server cannot talk the peer into `C:\Windows` or a wallet directory.

## Path validation becomes peer-relative

`ChatRequest.cwd` and `dispatch_agent(working_directory)` currently reject
Windows paths outright (`app/schemas/chat.py`, `app/skills/dispatch.py`). That
was right when every peer was Linux. With a Windows peer it is wrong: the check
has to ask whether the path suits *the peer it is aimed at*.

| Path | Target peer | Result |
|---|---|---|
| `C:\Users\…\repo` | windows | allowed |
| `C:\Users\…\repo` | linux | refused — today's behaviour |
| `/opt/forge-mk1/x` | linux | allowed |
| `/opt/forge-mk1/x` | windows | refused |

Since routing already picks the peer from the path, most of this resolves
itself: a path matching no peer's roots is refused by rule 3 above. The
platform check catches the rest — a path that is inside an advertised root but
malformed for that OS.

## Failure modes

- **PC asleep.** The common case, not an edge case. A dispatch aimed at a root
  whose peer is not connected fails immediately with which hosts *are*
  connected.
- **Reconnect flapping.** Per-host slots mean a reconnecting laptop can never
  displace the server peer.
- **Two PCs.** Falls out of the design at no extra cost.
- **Server compromise.** This gives the server the ability to drive file
  operations on the owner's machine. The allowlist bounds it; the credential
  rotation below is what keeps the door shut.

## Build order

**0. Rotate the credential first.** `SPEDA_API_KEY` is currently the single
shared secret, and it has been sitting in Caddy's request logs in plaintext
(`X-Api-Key` is logged in full on every 502). It is about to become the
credential that reaches the owner's filesystem, so it should not be one that
has been in a log file. Rotate it, and stop Caddy logging the header.

Worth considering at the same time: a separate secret for peer connections, so
the app key and the machine-access key are not the same string.

1. Host-aware `WebSocketManager` + the registration fields, with the defaults
   that keep the server peer working untouched.
2. Peer selection by root, including the refuse-don't-fall-through rule.
3. Peer-relative path validation replacing the flat Windows rejection.
4. The Windows peer itself: `FORGE_CELL_BACKEND=subprocess`, the roots config,
   and whatever supervises it (Task Scheduler or a tray app — it needs to
   survive a reboot without the owner starting it by hand).
5. A directory picker in the desktop client that sends `cwd`, so selecting the
   folder is the whole interaction.

Steps 1–3 are server-side and independently testable. Nothing about them
requires the Windows peer to exist yet, and they are worth landing first so the
peer has something correct to connect to.
