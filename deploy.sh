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

# ── n8n editor (optional public hostname) ────────────────────────────────────
# n8n's port mapping is localhost-only and stays that way; publishing it means
# giving Caddy a site block so it knows the hostname exists and can obtain a
# certificate for it. Without one, Caddy has no cert for that SNI and aborts the
# handshake — which browsers report as ERR_SSL_PROTOCOL_ERROR.
#
# Like $DOMAIN, the hostname comes from the gitignored .env and the generated
# block lands in gitignored caddy-sites/, so it stays out of this public repo.
# Requires DOMAIN to be set, since that is what starts Caddy at all.
# The editor's only auth boundary is n8n's own owner login — it holds every
# credential you have connected, so keep that password strong.
N8N_DOMAIN="$(grep -E '^N8N_DOMAIN=' packages/igor/.env 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)"
mkdir -p caddy-sites
if [[ -n "${N8N_DOMAIN}" && -n "${DOMAIN}" ]]; then
  say "n8n editor: ${N8N_DOMAIN} — Caddy will provision HTTPS and proxy to n8n:5678"
  # Consumed by docker-compose.yml so n8n generates public URLs, not localhost ones.
  export N8N_DOMAIN
  export N8N_PROTOCOL="https"
  export N8N_PUBLIC_URL="https://${N8N_DOMAIN}/"
  export N8N_PROXY_HOPS=1
  # Unlink first: a file left by a manual root-run deploy is not writable by the
  # CI deploy user, and `cat >` cannot clobber a file it cannot open. Removing it
  # only needs write permission on the directory, which the deploy user has.
  rm -f caddy-sites/n8n.caddy
  cat > caddy-sites/n8n.caddy <<EOF
${N8N_DOMAIN} {
	reverse_proxy n8n:5678
}
EOF
else
  [[ -n "${N8N_DOMAIN}" ]] && say "N8N_DOMAIN set but DOMAIN is not — Caddy is off, so n8n stays tunnel-only"
  rm -f caddy-sites/n8n.caddy
fi

# ── H.İ.S.A.R. (optional peer) ───────────────────────────────────────────────
# Hisar deploys from a sibling clone with its own gitignored .env. If that clone
# is present and configured, mirror it to main and bring it up in the same
# stack; otherwise the profile stays off and nothing about this server changes.
HISAR_DIR="../hisar-mk1"
if [[ -d "${HISAR_DIR}/.git" ]]; then
  say "Updating H.İ.S.A.R. clone (${HISAR_DIR})…"
  git -C "${HISAR_DIR}" fetch --prune origin main
  git -C "${HISAR_DIR}" reset --hard origin/main
fi
HISAR_ON=false
if [[ -f "${HISAR_DIR}/.env" ]]; then
  HISAR_ON=true
  PROFILE+=(--profile hisar)

  # Its public hostname is a sibling of $DOMAIN, not a subdomain of it, so it
  # comes from Hisar's own .env. Caddy imports whatever lands in caddy-sites/;
  # the file is gitignored, keeping the hostname out of this public repo.
  HISAR_DOMAIN="$(grep -E '^HISAR_PUBLIC_DOMAIN=' "${HISAR_DIR}/.env" | cut -d= -f2- | tr -d '[:space:]' || true)"
  mkdir -p caddy-sites
  if [[ -n "${HISAR_DOMAIN}" ]]; then
    say "H.İ.S.A.R. configured — ${HISAR_DOMAIN}"
    # max_size must be >= HISAR_MAX_UPLOAD_BYTES or Caddy truncates large
    # uploads before the API ever sees them.
    rm -f caddy-sites/hisar.caddy
    cat > caddy-sites/hisar.caddy <<EOF
${HISAR_DOMAIN} {
	reverse_proxy hisar:8600

	request_body {
		max_size 2GB
	}
}
EOF
  else
    say "H.İ.S.A.R. configured, but HISAR_PUBLIC_DOMAIN is unset — no TLS site block written"
    rm -f caddy-sites/hisar.caddy
  fi
else
  say "No ${HISAR_DIR}/.env — skipping H.İ.S.A.R. (see hisar-mk1/deploy/README.md)"
fi

# ── n8n's callback secrets + the browser's shared token ────────────────────--
# Exported (from the one secret file) so compose can interpolate them, and
# nothing sensitive lives in the repo.
#
# POSTGRES_USER / _PASSWORD / _DB used to be exported here too. They are not any
# more: the postgres service was removed on 2026-08-22, and exporting credentials
# for a service that no longer exists is how a deploy script starts describing a
# stack nobody is running.
#
# SPEDA_API_KEY / N8N_SECRET are here for the same reason: workflows call back
# into Igor with both headers and read them as {{ $env.* }}. env_file only feeds
# a container — compose interpolation needs them in THIS shell, so without this
# every imported workflow sends empty headers and gets a 401 it cannot explain.
#
# BROWSER_TOKEN is the same story across the other direction: the app reads it
# from its env_file, but the browser sidecar has no env_file of its own (it must
# not see Igor's secrets), so compose has to interpolate it from this shell or
# the two halves disagree and every browser call comes back 401.
# BROWSER_TOKEN is minted here rather than asked for. It is a shared secret
# between two containers and nothing else — no human ever types it, no other
# system needs to know it — so the only thing a manual step could add is the
# chance of it being left empty, which silently means the browser sidecar
# accepts anything on the compose network. Generated once, appended to the one
# secret file, and never regenerated (rotating it mid-run would 401 every
# browser call until the next deploy). To rotate deliberately: delete the line
# and re-run this script.
if [[ -f packages/igor/.env ]] && ! grep -qE '^BROWSER_TOKEN=.+' packages/igor/.env; then
  NEW_TOKEN="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' 
')"
  # Drop an empty BROWSER_TOKEN= line if one is there, then append the real one.
  sed -i '/^BROWSER_TOKEN=$/d' packages/igor/.env
  printf '
# Shared secret between the app and the browser sidecar. Generated by deploy.sh.
BROWSER_TOKEN=%s
' "${NEW_TOKEN}" >> packages/igor/.env
  say "Generated a BROWSER_TOKEN for the browser sidecar (packages/igor/.env)"
fi

for var in SPEDA_API_KEY N8N_SECRET BROWSER_TOKEN; do
  val="$(grep -E "^${var}=" packages/igor/.env 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)"
  [[ -n "${val}" ]] && export "${var}=${val}"
done

# ── Build + start ────────────────────────────────────────────────────────────
say "Building and starting the stack (sandbox + api${DOMAIN:+ + caddy})…"
docker compose "${PROFILE[@]}" up -d --build

# Editing a mounted Caddyfile does not recreate the container, so a config
# change (a new peer site block) needs an explicit reload. Harmless no-op when
# nothing changed; skipped when Caddy isn't running at all.
if [[ -n "${DOMAIN}" ]]; then
  docker compose "${PROFILE[@]}" exec -T caddy \
    caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile 2>/dev/null \
    && echo "  Caddy config reloaded." || true
fi

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

# ── Wait for Hisar health (only when it is part of this deploy) ──────────────
if $HISAR_ON; then
  say "Waiting for H.İ.S.A.R. to come up…"
  hok=false
  for _ in $(seq 1 30); do
    if docker compose "${PROFILE[@]}" exec -T hisar python -c \
       "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8600/health')" 2>/dev/null; then
      hok=true; break
    fi
    sleep 2
  done
  $hok && echo "  H.İ.S.A.R. is healthy." || echo "  ⚠ H.İ.S.A.R. did not report healthy in 60s — check: docker compose logs hisar"
fi

# ── Import memory (optional, one time) ───────────────────────────────────────
if [[ -n "${MIGRATE_DB}" ]]; then
  [[ -f "${MIGRATE_DB}" ]] || { echo "Migration source not found: ${MIGRATE_DB}"; exit 1; }
  say "Importing memory from ${MIGRATE_DB} (sessions, messages, memory files)…"
  docker compose cp "${MIGRATE_DB}" app:/tmp/import.db
  # --dest is intentionally omitted: the script falls back to the container's
  # DATABASE_URL, which compose pins to the server's SQLite file.
  #
  # NOTE (2026-08-22): with postgres gone this path now imports SQLite INTO
  # SQLite, despite the script's name. It is opt-in (`--migrate`) and never runs
  # on a normal deploy, so it is left in place rather than half-removed — but it
  # is a one-time bootstrap that has already happened, and it should go the next
  # time this script is touched.
  docker compose exec -T app python scripts/migrate_sqlite_to_postgres.py \
    --source /tmp/import.db
fi

# ── Done ─────────────────────────────────────────────────────────────────────
say "Deployed."
if [[ -n "${DOMAIN}" ]]; then
  echo "  Live at: https://${DOMAIN}"
  echo "  Build the desktop app pointed here:"
  echo "    powershell -File build-app.ps1 -ApiBase https://${DOMAIN} -ApiKey <SPEDA_API_KEY>"
  if $HISAR_ON && [[ -n "${HISAR_DOMAIN:-}" ]]; then echo "  H.İ.S.A.R.: https://${HISAR_DOMAIN}"; fi
else
  echo "  Live at: http://<server-ip>:8000"
fi
echo "  Logs: docker compose logs -f app"
