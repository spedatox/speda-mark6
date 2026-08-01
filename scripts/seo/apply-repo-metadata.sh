#!/usr/bin/env bash
#
# Set the GitHub repository metadata that search engines actually read.
#
# WHY THIS MATTERS MORE THAN THE README
# -------------------------------------
# Google renders a GitHub repo page as:
#
#     <title>GitHub - spedatox/speda-mark6: {description}</title>
#     <meta name="og:description" content="{description}">
#
# The README is body copy. The *description* is the title tag and the snippet —
# the two strongest on-page signals there are. speda-mark6 currently has an
# empty description, so Google has nothing to show and nothing to match. Topics
# feed GitHub's own search and the page's keyword surface; the homepage field
# publishes the one link that points crawlers at the Pages site.
#
# USAGE
# -----
#   export GITHUB_TOKEN=ghp_xxx      # a PAT with the `repo` scope
#   ./scripts/seo/apply-repo-metadata.sh              # dry run — prints, writes nothing
#   ./scripts/seo/apply-repo-metadata.sh --apply      # writes speda-mark6
#   ./scripts/seo/apply-repo-metadata.sh --apply --siblings
#
# --siblings also points the predecessor and component repos at Mark VI. Those
# repos are the closest thing this project has to an inbound link network: they
# already exist, they already carry the brand token, and each one that links
# forward tells Google the "SPEDA" entity resolves to Mark VI. Descriptions are
# metadata only — no README, code or history is touched, and every write is
# reversible from the repo's own settings page.

set -euo pipefail

OWNER="spedatox"
REPO="speda-mark6"
SITE="https://spedatox.github.io/speda-mark6/"

DESCRIPTION="SPEDA Mark VI — a private, self-hosted multi-agent AI assistant. Eight specialist agents, persistent memory, proactive watchers, and a holographic desktop + Android command deck."

# Max 20 topics; lowercase, digits and hyphens only. Ordered most-distinctive
# first: the brand tokens are the ones with no competition to beat.
TOPICS='["speda","speda-mark-vi","ai-assistant","multi-agent","multi-agent-systems","personal-assistant","self-hosted","proactive-ai","agentic-ai","ai-agents","llm","anthropic","claude","fastapi","electron","kotlin","jetpack-compose","mcp","telegram-bot","python"]'

APPLY=0
SIBLINGS=0
for arg in "$@"; do
  case "$arg" in
    --apply)    APPLY=1 ;;
    --siblings) SIBLINGS=1 ;;
    -h|--help)  sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "GITHUB_TOKEN is not set. Create a PAT with the 'repo' scope at" >&2
  echo "https://github.com/settings/tokens and export it before running." >&2
  exit 1
fi

api() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-sS -X "$method"
    -H "Authorization: Bearer ${GITHUB_TOKEN}"
    -H "Accept: application/vnd.github+json"
    -H "X-GitHub-Api-Version: 2022-11-28"
    "https://api.github.com${path}")
  [[ -n "$body" ]] && args+=(-d "$body")
  curl "${args[@]}"
}

# Minimal JSON string escaper — descriptions contain em dashes and plus signs
# but no quotes or backslashes; this keeps the payloads honest anyway.
json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

set_repo() {
  local repo="$1" desc="$2" home="$3" topics="${4:-}"

  if [[ $APPLY -eq 0 ]]; then
    echo "  [dry run] ${OWNER}/${repo}"
    echo "            description: ${desc}"
    echo "            homepage:    ${home}"
    [[ -n "$topics" ]] && echo "            topics:      ${topics}"
    return
  fi

  echo "  → ${OWNER}/${repo}"
  api PATCH "/repos/${OWNER}/${repo}" \
    "{\"description\":\"$(json_escape "$desc")\",\"homepage\":\"${home}\"}" \
    | grep -q '"full_name"' && echo "    description + homepage set" \
    || { echo "    FAILED — check the token scope and that the repo exists" >&2; return 1; }

  if [[ -n "$topics" ]]; then
    api PUT "/repos/${OWNER}/${repo}/topics" "{\"names\":${topics}}" >/dev/null \
      && echo "    topics set"
  fi
}

echo
echo "═══ SPEDA Mark VI — repository metadata ═══"
[[ $APPLY -eq 0 ]] && echo "DRY RUN. Nothing is written. Re-run with --apply to commit these."
echo

echo "Primary repository:"
set_repo "$REPO" "$DESCRIPTION" "$SITE" "$TOPICS"

if [[ $SIBLINGS -eq 1 ]]; then
  echo
  echo "Sibling repositories (each one becomes an inbound link to Mark VI):"

  # Component repos — current, actively part of the system.
  set_repo "speda-go" \
    "SPEDA GO — the native Kotlin + Jetpack Compose Android client for SPEDA Mark VI." \
    "$SITE" \
    '["speda","speda-mark-vi","speda-go","android","kotlin","jetpack-compose","ai-assistant","multi-agent","self-hosted"]'

  set_repo "forge-mark1" \
    "The Forge — the standalone execution engine behind Optimus, the systems and code agent of SPEDA Mark VI." \
    "$SITE" \
    '["speda","speda-mark-vi","optimus","agentic-ai","code-execution","sandbox","python"]'

  # Predecessor generations — the lineage that establishes the entity.
  for n in 1 2 3 4 5; do
    case $n in
      1) roman="I" ;; 2) roman="II" ;; 3) roman="III" ;; 4) roman="IV" ;; 5) roman="V" ;;
    esac
    set_repo "speda-mark${n}" \
      "S.P.E.D.A. Mark ${roman} — an earlier generation of SPEDA, the Specialized Personal Executive Digital Assistant. Superseded by SPEDA Mark VI." \
      "https://github.com/spedatox/speda-mark6" \
      "[\"speda\",\"speda-mark-${roman,,}\",\"ai-assistant\",\"personal-assistant\",\"archive\"]"
  done
fi

echo
if [[ $APPLY -eq 0 ]]; then
  echo "Nothing was written. Re-run with --apply (and optionally --siblings)."
else
  echo "Done. Verify at https://github.com/${OWNER}/${REPO}"
  echo "Then request indexing for ${SITE} in Google Search Console."
fi
echo
