#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# LIFEBOAT PROTOCOL — emergency disk reclamation for the Mark VI host.
#
# When the Contabo box starts running out of disk, Orion activates this. It
# reclaims throwaway Docker data, old generated outputs, and stale Cell
# workspaces without changing a worker profile or service deployment.
#
# Orion runs this over system_ops on the host:
#     bash /opt/speda/lifeboat.sh --assess     # report only, changes nothing (default)
#     bash /opt/speda/lifeboat.sh --bail        # reclaim disposable data
#
# Thresholds (env-overridable):
#     LIFEBOAT_ACTIVATE_PCT  used%% at/above which activation is warranted   (default 85)
#     LIFEBOAT_TARGET_FREE_GB free GB to stop reclaiming once reached         (default 30)
#     LIFEBOAT_WATCH_FS      filesystem to watch                              (default /)
#
# Every action is idempotent and prints what it reclaimed. Nothing here is in the
# system_ops deny-list, so Orion can run it.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

WATCH_FS="${LIFEBOAT_WATCH_FS:-/}"
ACTIVATE_PCT="${LIFEBOAT_ACTIVATE_PCT:-85}"
TARGET_FREE_GB="${LIFEBOAT_TARGET_FREE_GB:-30}"

log()  { printf '  %s\n' "$*"; }
head() { printf '\n=== %s ===\n' "$*"; }

avail_bytes() { df -B1 --output=avail "$WATCH_FS" 2>/dev/null | tail -1 | tr -dc '0-9'; }
used_pct()    { df --output=pcent "$WATCH_FS" 2>/dev/null | tail -1 | tr -dc '0-9'; }
gb()          { awk -v b="$1" 'BEGIN{printf "%.1f", b/1073741824}'; }

report() {
  head "DISK ($WATCH_FS)"
  df -h "$WATCH_FS" | tail -1
  log "used: $(used_pct)%   free: $(gb "$(avail_bytes)") GB   (activate>=${ACTIVATE_PCT}%, target free ${TARGET_FREE_GB}GB)"
  head "DOCKER FOOTPRINT"
  docker system df 2>/dev/null || log "docker unavailable"
}

# True once free space has climbed back to the target — used to stop early so we
# never throw more overboard than the storm requires.
safe_now() {
  local free_gb; free_gb=$(gb "$(avail_bytes)")
  awk -v f="$free_gb" -v t="$TARGET_FREE_GB" 'BEGIN{exit !(f+0 >= t+0)}'
}

reclaim_step() {  # <label> <command...>
  local label="$1"; shift
  local before after
  before=$(avail_bytes)
  log "→ $label"
  "$@" >/dev/null 2>&1 || log "   (step reported an error; continuing)"
  after=$(avail_bytes)
  local freed=$(( after - before ))
  (( freed > 0 )) && log "   reclaimed $(gb "$freed") GB" || log "   reclaimed 0 GB"
}

# ── TIER 1 — bail water: throwaway Docker junk + logs. Zero service impact. ──────
tier1_bail() {
  head "TIER 1 — bail water (safe, reversible)"
  # Build cache is the single biggest, cheapest win. builder prune only removes
  # cache NOT in use by a running build.
  reclaim_step "docker build cache"    docker builder prune -af
  # Cells are throwaway per job — any stopped one is pure garbage. Running service
  # containers are untouched (prune only removes stopped).
  reclaim_step "stopped containers"    docker container prune -f
  # Dangling (untagged) image layers.
  reclaim_step "dangling images"       docker image prune -f
  # Journald: keep the last 100M, drop the rest.
  reclaim_step "journald vacuum"       journalctl --vacuum-size=100M
  # Generated documents past the 24h contract (n8n also does this; force it now).
  reclaim_step "old /tmp/speda_outputs" find /tmp/speda_outputs -type f -mmin +1440 -delete
  # Finished Forge Cell workspaces older than a week.
  reclaim_step "stale forge workspaces" find /opt/hisar/vault/Forge/workspaces -maxdepth 1 -mindepth 1 -type d -mtime +7 -exec rm -rf {} +
}

# ── Driver ───────────────────────────────────────────────────────────────────
MODE="${1:---assess}"
case "$MODE" in
  --assess)
    report
    head "VERDICT"
    if (( $(used_pct) >= ACTIVATE_PCT )); then
      log "used $(used_pct)% >= ${ACTIVATE_PCT}% — reclamation warranted. Run: bash $0 --bail"
    else
      log "used $(used_pct)% < ${ACTIVATE_PCT}% — healthy. No action."
    fi
    ;;
  --bail)
    # Disposable-data reclamation only. Deliberately does not escalate into
    # deleting active images, volumes, source, or persisted application data.
    report
    tier1_bail
    head "FINAL STATE"; report
    if safe_now; then
      log "Healthy again — $(gb "$(avail_bytes)") GB free."
    else
      log "Still only $(gb "$(avail_bytes)") GB free (< ${TARGET_FREE_GB}GB)."
      log "Disposable-data reclamation was not enough; manual capacity action is required."
    fi
    ;;
  *)
    echo "usage: $0 [--assess|--bail]" >&2
    exit 2
    ;;
esac
