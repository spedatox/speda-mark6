# Lifeboat Protocol — emergency disk reclamation

**Owner agent:** Orion (via `system_ops`) · **Trigger:** n8n disk watchdog · **Script:** [`/opt/speda/lifeboat.sh`](../lifeboat.sh)

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

## Trigger

Per the architecture, **the backend runs no internal scheduler** — n8n owns all
timing (CLAUDE.md). A watchdog workflow on n8n's clock:

1. Every ~15 min, check host disk (Orion `system_ops exec: df -B1 --output=pcent /`,
   or a thin host probe).
2. If used % ≥ **85**, `POST /trigger/orion` with
   `{"type":"watchdog","event":"disk_pressure","output_mode":"push"}`.
3. Orion runs `bash /opt/speda/lifeboat.sh --activate` over `system_ops` and
   reports what it reclaimed as a push notification.

There is **no auto-jettison without pressure** — the script is inert below the
threshold, and `--assess` (the default) changes nothing.

---

## The tiers

The script escalates only as far as it must, re-checking free space after each
tier and standing down the moment free space clears **`TARGET_FREE_GB` (default
30 GB)**.

### Tier 1 — bail water (safe, autonomous, reversible)
Zero service impact. Orion runs this unattended.
- `docker builder prune -af` — build cache (the single biggest win)
- `docker container prune -f` — stopped Cells (throwaway by design)
- `docker image prune -f` — dangling layers
- `journalctl --vacuum-size=100M`
- delete `/tmp/speda_outputs` older than 24 h
- delete Forge Cell workspaces older than 7 days

Running service containers, tagged images, and recent logs are **never** touched.

### Tier 2 — jettison the arsenal (the deadweight)
Only if Tier 1 left < `TARGET_FREE_GB` free. Reclaims ~25 GB:
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
own — Orion pages the owner with a `du -xh / | sort -rh | head` breakdown so a
human decides. Auto-deleting past this point risks owner data.

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

```bash
bash /opt/speda/lifeboat.sh --assess          # report + verdict, changes nothing (default)
bash /opt/speda/lifeboat.sh --activate         # Tier 1, then Tier 2 only if still low
bash /opt/speda/lifeboat.sh --force-jettison   # skip straight to Tier 2 (manual)
bash /opt/speda/lifeboat.sh --restore          # rebuild the arsenal (disk must be healthy)
```

Thresholds via env: `LIFEBOAT_ACTIVATE_PCT` (85), `LIFEBOAT_TARGET_FREE_GB` (30),
`LIFEBOAT_WATCH_FS` (`/`).

## Safety

- Every `system_ops` call is audited to `/memories/.audit/ops.md` with a request id.
- The `system_ops` deny-list already refuses catastrophic commands (`rm -rf /`,
  `mkfs`, `dd if=`, `reboot`, …); nothing in Lifeboat trips it.
- Idempotent throughout: re-running after a partial pass is safe; an
  already-jettisoned Centurion is detected and skipped.
