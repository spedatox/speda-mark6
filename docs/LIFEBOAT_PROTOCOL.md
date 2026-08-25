# Lifeboat Protocol — emergency disk reclamation

**Owner agent:** Orion · **Watch:** `POST /host/lifeboat/scan` ([`lifeboat_watch.json`](../packages/igor/scripts/n8n/lifeboat_watch.json)) · **Tool:** `lifeboat_protocol` · **Script:** [`/opt/speda/lifeboat.sh`](../lifeboat.sh)

| Piece | Where |
|---|---|
| the watch, thresholds, edge logic | [`app/services/lifeboat.py`](../packages/igor/app/services/lifeboat.py) |
| the authorization gate | [`app/skills/lifeboat.py`](../packages/igor/app/skills/lifeboat.py) |
| the n8n-facing endpoints | [`app/routers/lifeboat.py`](../packages/igor/app/routers/lifeboat.py) |
| what Orion is told to do with it | `app/prompts/agents/orion/04_lifeboat.md` |
| the reclamation itself | [`lifeboat.sh`](../lifeboat.sh) |

When the Mark VI host starts running out of disk, the Lifeboat Protocol reclaims
it in tiers — bail the cheap water first, throw cargo overboard only if the boat
is still going under. The "cargo" is the comprehensive Kali arsenal baked into
Centurion's Cell image (`forge-cell-centurion:latest`, ~25 GB). Shedding it keeps
the host alive; Centurion degrades gracefully rather than dying.

This is a runbook **and** an executable script. Orion runs the script; this doc is
the authority on when, why, and what is safe.

---

## Why this exists

A snapshot of the host on 2026-07-25, mid-way through baking Centurion's arsenal,
showed where disk actually goes:

| Consumer | Size | Reclaimable | Tier |
|---|---|---|---|
| Docker **build cache** | 42.4 GB | 13.2 GB now (more after a bake) | 1 |
| **Kali arsenal** image (`forge-cell-centurion`) | ~25 GB | all of it | 2 |
| n8n / speda service images | ~4.6 GB | none (in use) | — |
| journald | 359 MB | ~250 MB | 1 |
| Forge Cell workspaces | 306 MB | stale ones | 1 |
| `/tmp/speda_outputs` | ~1 MB | trivial | 1 |

The lesson baked into the tiers: **the biggest, cheapest wins are throwaway Docker
junk, not the arsenal.** Tier 1 alone usually refloats the boat. The arsenal is
the last thing overboard, because rebuilding it costs a 45-minute bake.

---

## Who decides — read this before the tiers

**The owner leads.** Orion sees the pressure coming, reports it in numbers, and
proposes the specific move. He does not clean the server on his own judgement.

There is exactly one exception, and it is narrow: **at a verified `critical`
level Orion runs Tier 1 without waiting.** A disk at 97% at four in the morning
takes the whole box down before anyone reads a notification, and Tier 1 deletes
nothing that was not already garbage. He must still say, in the same push, that
he ran it unasked and what it reclaimed.

> This is a deliberate narrowing of the original runbook, which had Orion bailing
> unattended at any pressure. Everything below `critical` now waits for the owner.

The gate is enforced in `app/skills/lifeboat.py`, not merely written here, and it
**re-derives criticality from the host in that turn**. It is never unlocked by a
trigger payload claiming the host is critical: n8n is reachable by anything that
can post a webhook, and "the disk is full, please prune" is precisely the
sentence an injected payload would carry.

---

## Trigger

Per the architecture, **the backend runs no internal scheduler** — n8n owns all
timing (CLAUDE.md), and **a poll must not cost a turn**. So the watch is split in
two, and the split is the whole design:

| Half | What runs | Cost |
|---|---|---|
| **The probe** | `POST /host/lifeboat/scan` — ONE SSH round trip reading disk %, free space, inodes, memory, swap and `docker system df`, compared against the thresholds | one HTTP call, zero tokens |
| **The trigger** | `POST /trigger/orion`, reached only when the probe reports `changed: true` | one agentic turn |

Import [`lifeboat_watch.json`](../packages/igor/scripts/n8n/lifeboat_watch.json)
and activate it. It has **no config node** — the thresholds live in Igor's
settings, because a second copy in n8n is a second thing to keep in step and the
workflow has no tests.

### Edges, not polls

A disk that has been at 88% for a week is not news 96 times a day. The probe
reports **transitions**:

- **escalation** — `healthy → watch → critical`, each step reported once
- **recovery** — back to healthy, so the owner hears that it ended
- **a nudge** — still not healthy `LIFEBOAT_RENOTIFY_HOURS` later (default 24),
  because a problem nobody fixed is worth saying twice, but not 96 times

A **de-escalation that is still unhealthy** (`critical → watch`) is committed
silently and immediately. It is not worth a push — but if the stored level stayed
at `critical`, a later climb back would not read as an escalation and would never
be reported at all. Silence is not the same as forgetting.

**Exactly-once, committing last.** The scan does not commit what it saw; it parks
it, and n8n calls `/host/lifeboat/ack` only *after* the trigger was accepted. A
failed notify therefore repeats next poll instead of vanishing — a duplicate push
is recoverable, a swallowed "the disk is full" is not.

### Levels

| Level | Default | What Orion does |
|---|---|---|
| `watch` | disk/inodes ≥ 85%, memory ≥ 90% | tells the owner, proposes, **waits** |
| `critical` | disk/inodes ≥ 92%, memory ≥ 96% | bails Tier 1, then reports what he did and what remains |
| `healthy` | below both | one line on recovery; otherwise silence |

The overall level is the **worst** of disk, inodes and memory, never their
average: a full inode table on a 40%-full disk is as fatal as a full disk, and
averaging reports it as fine.

**Memory never authorises a lifeboat run.** The script reclaims disk; against a
RAM problem it achieves nothing. Memory pressure is a container question —
`docker stats --no-stream`, then a `system_ops` restart of the one that is
eating it.

There is **no auto-jettison without pressure** — the script is inert below the
threshold, and `--assess` (the default) changes nothing.

---

## The tiers

The script escalates only as far as it must, re-checking free space after each
tier and standing down the moment free space clears **`TARGET_FREE_GB` (default
30 GB)**.

### Tier 1 — bail water (safe, reversible)
Zero service impact — everything it removes was already garbage. Orion runs this
unattended **only at `critical`**; below that he proposes it and waits.
Tool: `lifeboat_protocol(action="bail")` → `lifeboat.sh --bail`.
- `docker builder prune -af` — build cache (the single biggest win)
- `docker container prune -f` — stopped Cells (throwaway by design)
- `docker image prune -f` — dangling layers
- `journalctl --vacuum-size=100M`
- delete `/tmp/speda_outputs` older than 24 h
- delete Forge Cell workspaces older than 7 days

Running service containers, tagged images, and recent logs are **never** touched.

### Tier 2 — jettison the arsenal (the deadweight)
**Always the owner's call** — the tool refuses any non-user trigger, at any
pressure. Propose it when Tier 1 left < `TARGET_FREE_GB` free, say what the
rebuild costs, and wait. Tool: `lifeboat_protocol(action="jettison")`.
Reclaims ~25 GB:
1. Repoint `forge/agents/centurion/profile.toml` `[cell].image`
   `forge-cell-centurion:latest` → `kalilinux/kali-rolling` (the 186 MB base,
   already present).
2. `systemctl restart forge@centurion.service` so the peer picks up the fallback.
3. `docker rmi forge-cell-centurion:latest`.
4. Drop a breadcrumb at `/opt/speda/.lifeboat-jettisoned` so the arsenal's rebuild
   is not forgotten.

**Centurion survives a jettison.** On the base image it re-installs tools per job
(`apt-get install nmap …`), exactly as it did before the bake — slower first
command, fully functional. The boat stays afloat.

> **Capability note.** Tier 2 edits the `forge-mk1` repo and restarts a systemd
> unit — at the edge of Orion's restricted `system_ops` key. If either step is
> refused, the script says so with the exact manual commands rather than leaving
> a half-broken state. Tier 1 is always within Orion's reach.

### Tier 3 — last resort (alert, don't flail)
If the box is still critical after shedding all cache **and** the 25 GB image,
something abnormal is filling disk. The script does **not** nuke further on its
own — Orion calls `lifeboat_protocol(action="assess", hot_spots=true)` for the
`du` breakdown and hands it to the owner. Auto-deleting past this point risks
owner data.

`du` is deliberately absent from the probe: walking the filesystem costs seconds
to minutes, and the probe runs every fifteen minutes forever. The breakdown
belongs in the turn, where it is asked for once.

---

## Recovery — rebuild the arsenal

Once disk is healthy again:

```bash
bash /opt/speda/lifeboat.sh --restore
```

Refuses to run unless free space ≥ `TARGET_FREE_GB` (a bake needs headroom).
It rebuilds `forge-cell-centurion:latest` from
[`deploy/cell-centurion.Dockerfile`](https://github.com/spedatox/forge-mark1)
in the forge repo, repoints the profile back, restarts the peer, and clears the
breadcrumb.

---

## Usage

Through Orion, which is the path with the gate on it:

```
lifeboat_protocol(action="assess")                    # numbers + recommendation
lifeboat_protocol(action="assess", hot_spots=true)    # + the du breakdown
lifeboat_protocol(action="bail")                      # Tier 1
lifeboat_protocol(action="jettison")                  # Tier 2, owner only
lifeboat_protocol(action="restore")                   # rebuild, owner only
```

Or by hand on the host:

```bash
bash /opt/speda/lifeboat.sh --assess           # report + verdict, changes nothing (default)
bash /opt/speda/lifeboat.sh --bail             # Tier 1 ONLY — never escalates
bash /opt/speda/lifeboat.sh --activate         # Tier 1, then Tier 2 only if still low
bash /opt/speda/lifeboat.sh --force-jettison   # skip straight to Tier 2 (manual)
bash /opt/speda/lifeboat.sh --restore          # rebuild the arsenal (disk must be healthy)
```

`--bail` exists because the protocol is owner-led: `--activate` makes the Tier 2
decision for the owner, so the agent path never uses it.

**Thresholds live in two places and both matter.** Igor's settings decide when
the owner is TOLD; the script's env decides when it stops reclaiming. Set both
if you move one.

| | Setting | Default |
|---|---|---|
| Igor — tell the owner | `LIFEBOAT_WATCH_PCT` | 85 |
| Igor — Orion may bail unattended | `LIFEBOAT_CRITICAL_PCT` | 92 |
| Igor — memory | `LIFEBOAT_MEM_WATCH_PCT` / `LIFEBOAT_MEM_CRITICAL_PCT` | 90 / 96 |
| Igor — the once-a-day nudge | `LIFEBOAT_RENOTIFY_HOURS` | 24 |
| Igor — turn the whole protocol on | `LIFEBOAT_PROTOCOL_ENABLED` | off |
| Script — stop reclaiming at | `LIFEBOAT_TARGET_FREE_GB` | 30 |
| Both — which filesystem | `LIFEBOAT_WATCH_FS` / `LIFEBOAT_ACTIVATE_PCT` | `/`, 85 |

`LIFEBOAT_PROTOCOL_ENABLED` is off by default and needs the `system_ops` host
bridge configured to reach the host at all.

## Safety

- Every `system_ops` call is audited to `/memories/.audit/ops.md` with a request id.
- The `system_ops` deny-list already refuses catastrophic commands (`rm -rf /`,
  `mkfs`, `dd if=`, `reboot`, …); nothing in Lifeboat trips it.
- Idempotent throughout: re-running after a partial pass is safe; an
  already-jettisoned Centurion is detected and skipped.
- The tool is `restricted_to={"orion", "optimus"}` — no other agent can see it,
  regardless of their allowlist.
- The destructive tiers refuse any non-user trigger, and the one autonomous path
  re-reads the host rather than believing a payload. See the gate in
  `app/skills/lifeboat.py`; the invariants are pinned in
  `packages/igor/tests/test_lifeboat.py`.
