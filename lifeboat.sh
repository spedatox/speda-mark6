#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# LIFEBOAT PROTOCOL — emergency disk reclamation for the Mark VI host.
#
# When the Contabo box starts running out of disk, Orion activates this. It bails
# the cheap water first (throwaway Docker junk), and only throws cargo overboard
# — the comprehensive Kali arsenal baked into Centurion's Cell image — if that is
# not enough. Centurion keeps working after a jettison: it falls back to the base
# kali-rolling image and re-installs tools per job (slower, but alive), exactly as
# it did before the bake. Rebuild the arsenal with `--restore` once disk is healthy.
#
# Orion runs this over system_ops on the host:
#     bash /opt/speda/lifeboat.sh --assess     # report only, changes nothing (default)
#     bash /opt/speda/lifeboat.sh --bail        # Tier 1 ONLY — never escalates
#     bash /opt/speda/lifeboat.sh --activate    # bail water, then jettison if needed
#     bash /opt/speda/lifeboat.sh --restore     # rebuild the arsenal (disk must be healthy)
#
# --bail exists because the protocol is owner-led: Tier 1 deletes only what was
# already garbage and is safe to authorize on its own, while Tier 2 costs a
# 45-minute rebuild and is the owner's decision. --activate makes that decision
# for them, so the agent path (app/skills/lifeboat.py) uses --bail and asks.
#
# Thresholds (env-overridable):
#     LIFEBOAT_ACTIVATE_PCT  used%% at/above which activation is warranted   (default 85)
#     LIFEBOAT_TARGET_FREE_GB free GB to stop reclaiming once reached         (default 30)
#     LIFEBOAT_WATCH_FS      filesystem to watch                              (default /)
#
# Every action is idempotent and prints what it reclaimed. Nothing here is in the
# system_ops deny-list, so Orion can run it; the Tier-2 forge/systemd steps sit at
# the edge of the restricted key — if they are refused, the script says so loudly
# instead of half-finishing.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

WATCH_FS="${LIFEBOAT_WATCH_FS:-/}"
ACTIVATE_PCT="${LIFEBOAT_ACTIVATE_PCT:-85}"
TARGET_FREE_GB="${LIFEBOAT_TARGET_FREE_GB:-30}"

FORGE_DIR="${FORGE_DIR:-/opt/forge-mk1}"
CENTURION_PROFILE="$FORGE_DIR/forge/agents/centurion/profile.toml"
DOCKERFILE="$FORGE_DIR/deploy/cell-centurion.Dockerfile"
ARSENAL_IMAGE="forge-cell-centurion:latest"
FALLBACK_IMAGE="kalilinux/kali-rolling"
PEER_UNIT="forge@centurion.service"
JETTISON_FLAG="/opt/speda/.lifeboat-jettisoned"

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
  # Build cache is the single biggest, cheapest win (the Centurion bake alone left
  # tens of GB). builder prune only removes cache NOT in use by a running build.
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

# ── TIER 2 — jettison the arsenal: reclaim the ~25GB Kali image. ────────────────
# Centurion degrades to the base image (re-installs tools per job) but stays alive.
tier2_jettison() {
  head "TIER 2 — jettison the Kali arsenal (~25 GB)"
  if [[ ! -f "$CENTURION_PROFILE" ]]; then
    log "profile not found at $CENTURION_PROFILE — cannot repoint; skipping jettison."
    return 1
  fi
  if ! grep -q "$ARSENAL_IMAGE" "$CENTURION_PROFILE"; then
    log "Centurion already off the arsenal image — nothing to jettison."
    return 0
  fi

  log "→ repoint Centurion profile: $ARSENAL_IMAGE → $FALLBACK_IMAGE"
  if sed -i "s|^image\\s*=.*|image         = \"$FALLBACK_IMAGE\"|" "$CENTURION_PROFILE"; then
    log "   profile repointed"
  else
    log "   could not edit profile (permission?) — ABORTING jettison to avoid a broken state."
    return 1
  fi

  log "→ restart $PEER_UNIT so the peer picks up the fallback image"
  if systemctl restart "$PEER_UNIT" 2>/dev/null; then
    log "   peer restarted"
  else
    log "   WARNING: could not restart $PEER_UNIT (restricted key?). Do it manually:"
    log "     systemctl restart $PEER_UNIT"
  fi

  # Remove any stopped cell still pinning the image, then drop the image itself.
  reclaim_step "remove arsenal image" bash -c "docker container prune -f; docker rmi $ARSENAL_IMAGE"

  # Breadcrumb so --restore (and the owner) know the arsenal owes a rebuild.
  printf 'jettisoned %s by lifeboat\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$JETTISON_FLAG" 2>/dev/null || true
  log "jettison complete — Centurion is on $FALLBACK_IMAGE (per-job installs). Rebuild with --restore when healthy."
}

# ── RECOVERY — rebuild the arsenal once the storm has passed. ────────────────────
restore_arsenal() {
  report
  if ! safe_now; then
    head "REFUSED"
    log "Only $(gb "$(avail_bytes)") GB free (< ${TARGET_FREE_GB}GB target). Rebuilding the"
    log "arsenal needs headroom — clear disk first, then re-run --restore."
    exit 1
  fi
  head "RESTORE — rebuild $ARSENAL_IMAGE"
  if [[ ! -f "$DOCKERFILE" ]]; then
    log "Dockerfile missing at $DOCKERFILE — pull the forge-mk1 repo first."
    exit 1
  fi
  log "→ docker build (this is the long comprehensive bake)…"
  if ( cd "$FORGE_DIR" && docker build -f "$DOCKERFILE" -t "$ARSENAL_IMAGE" deploy/ ); then
    log "   built $ARSENAL_IMAGE"
  else
    log "   build FAILED — leaving Centurion on the fallback image."
    exit 1
  fi
  log "→ repoint Centurion profile back to the arsenal"
  sed -i "s|^image\\s*=.*|image         = \"$ARSENAL_IMAGE\"|" "$CENTURION_PROFILE" \
    && log "   profile repointed" || log "   could not edit profile — do it by hand."
  systemctl restart "$PEER_UNIT" 2>/dev/null \
    && log "   peer restarted" || log "   restart $PEER_UNIT by hand."
  rm -f "$JETTISON_FLAG"
  head "RESTORED"; report
}

# ── Driver ───────────────────────────────────────────────────────────────────
MODE="${1:---assess}"
case "$MODE" in
  --assess)
    report
    head "VERDICT"
    if (( $(used_pct) >= ACTIVATE_PCT )); then
      log "used $(used_pct)% >= ${ACTIVATE_PCT}% — activation WARRANTED. Run: bash $0 --activate"
    else
      log "used $(used_pct)% < ${ACTIVATE_PCT}% — healthy. No action."
    fi
    ;;
  --bail)
    # Tier 1 and nothing else. Deliberately does NOT escalate: the caller wanted
    # the safe reclamation, and silently throwing the arsenal overboard because
    # it was not enough is exactly the decision they did not delegate.
    report
    tier1_bail
    head "FINAL STATE"; report
    if safe_now; then
      log "Healthy again — $(gb "$(avail_bytes)") GB free. Arsenal untouched."
    else
      log "Still only $(gb "$(avail_bytes)") GB free (< ${TARGET_FREE_GB}GB)."
      log "Tier 1 was not enough. Tier 2 (jettison the arsenal) needs a decision:"
      log "  bash $0 --force-jettison"
    fi
    ;;
  --activate)
    report
    tier1_bail
    if safe_now; then
      head "STAND DOWN"; log "Tier 1 recovered the box — $(gb "$(avail_bytes)") GB free. Arsenal untouched."
    else
      log "Tier 1 left only $(gb "$(avail_bytes)") GB free (< ${TARGET_FREE_GB}GB) — escalating."
      tier2_jettison
    fi
    head "FINAL STATE"; report
    ;;
  --force-jettison)
    report; tier2_jettison; head "FINAL STATE"; report
    ;;
  --restore)
    restore_arsenal
    ;;
  *)
    echo "usage: $0 [--assess|--bail|--activate|--force-jettison|--restore]" >&2
    exit 2
    ;;
esac
