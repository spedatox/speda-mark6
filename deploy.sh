#!/usr/bin/env bash
#
# SPEDA Mark VI — one-shot server deploy.
#
#   ./deploy.sh                      # build + start the whole stack
#   ./deploy.sh --migrate speda.db   # ...and import your existing SQLite memory
#
# Reads packages/igor/.env. If DOMAIN is set there, it also starts Caddy with
# automatic HTTPS for that domain. Run this on the server (Ubuntu + Docker).
#
set -euo pipefail
cd "$(dirname "$0")"

MIGRATE_DB=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --migrate) MIGRATE_DB="${2:-}"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

say() { printf "\n\033[36m▸ %s\033[0m\n" "$*"; }

# ── One-time rename migration (packages/api → packages/igor) ─────────────────
# The gitignored .env can't ride across the folder rename via git, so move it the
# first time this runs after the rename. Idempotent — a no-op once migrated.
if [[ -f packages/api/.env && ! -f packages/igor/.env ]]; then
  printf "\n\033[36m▸ Migrating packages/api/.env → packages/igor/.env (folder renamed)\033[0m\n"
  mv packages/api/.env packages/igor/.env
  rmdir packages/api 2>/dev/null || true
fi

# ── Preflight ────────────────────────────────────────────────────────────────
command -v docker >/dev/null || { echo "Docker is not installed."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 required."; exit 1; }
[[ -f packages/igor/.env ]] || {
  echo "Missing packages/igor/.env — run: cp packages/igor/.env.example packages/igor/.env  (then fill it in)"
  exit 1
}

# ── Domain? ──────────────────────────────────────────────────────────────────
DOMAIN="$(grep -E '^DOMAIN=' packages/igor/.env 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)"
PROFILE=()
if [[ -n "${DOMAIN}" ]]; then
  say "Domain: ${DOMAIN} — Caddy will provision HTTPS (DNS must point here, ports 80/443 open)"
  export DOMAIN
  PROFILE=(--profile domain)
else
  say "No DOMAIN set — API will be on http://<server-ip>:8000 (no TLS)"
fi

# ── Postgres credentials ───────────────────────────────────────────────────--
# Exported (from the one secret file) so compose interpolates them into the
# postgres service AND the app's DATABASE_URL — they stay in sync, and nothing
# sensitive lives in the repo. Note: POSTGRES_PASSWORD only takes effect on a
# FRESH database volume; to rotate it on an existing volume, change it in
# postgres directly (ALTER ROLE) or recreate the volume.
for var in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB; do
  val="$(grep -E "^${var}=" packages/igor/.env 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)"
  [[ -n "${val}" ]] && export "${var}=${val}"
done

# ── Build + start ────────────────────────────────────────────────────────────
say "Building and starting the stack (postgres + sandbox + api${DOMAIN:+ + caddy})…"
docker compose "${PROFILE[@]}" up -d --build

# ── Wait for API health ──────────────────────────────────────────────────────
say "Waiting for the API to come up…"
ok=false
for _ in $(seq 1 60); do
  if docker compose exec -T app python -c \
     "import urllib.request,sys; urllib.request.urlopen('http://localhost:8000/health'); " 2>/dev/null; then
    ok=true; break
  fi
  sleep 2
done
$ok && echo "  API is healthy." || echo "  ⚠ API did not report healthy in 120s — check: docker compose logs app"

# ── Import memory (optional, one time) ───────────────────────────────────────
if [[ -n "${MIGRATE_DB}" ]]; then
  [[ -f "${MIGRATE_DB}" ]] || { echo "Migration source not found: ${MIGRATE_DB}"; exit 1; }
  say "Importing memory from ${MIGRATE_DB} (sessions, messages, memory files)…"
  docker compose cp "${MIGRATE_DB}" app:/tmp/import.db
  # --dest is intentionally omitted: the script falls back to the container's
  # DATABASE_URL, which compose builds from POSTGRES_PASSWORD. Hardcoding a URL
  # here would use the wrong password on any server with a real (non-default) DB
  # password and fail with InvalidPasswordError.
  docker compose exec -T app python scripts/migrate_sqlite_to_postgres.py \
    --source /tmp/import.db
fi

# ── Done ─────────────────────────────────────────────────────────────────────
say "Deployed."
if [[ -n "${DOMAIN}" ]]; then
  echo "  Live at: https://${DOMAIN}"
  echo "  Build the desktop app pointed here:"
  echo "    powershell -File build-app.ps1 -ApiBase https://${DOMAIN} -ApiKey <SPEDA_API_KEY>"
else
  echo "  Live at: http://<server-ip>:8000"
fi
echo "  Logs: docker compose logs -f app"
