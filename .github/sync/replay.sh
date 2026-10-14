#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 SPEDA
# SPDX-License-Identifier: AGPL-3.0-or-later
# Run in a clean Core checkout on main, with full/main fetched.
# Core owns the exclude list and cursor. DRY_RUN=1 skips pushing.
set -euo pipefail
STATE_FILE=".github/sync-state"
EXCLUDE_FILE=".github/sync-exclude"
REPORT_FILE=".github/sync-report.json"
SYNC_BRANCH="${SYNC_BRANCH:-sync/from-full-build}"
FULL_REPO="${FULL_REPO:-spedatox/speda-mark6}"
emit() { if [ -n "${GITHUB_OUTPUT:-}" ]; then printf '%s\n' "$1" >> "$GITHUB_OUTPUT"; fi; }

if ! git diff --quiet || ! git diff --cached --quiet ||
   [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo "ERROR: replay requires a clean Core checkout." >&2; exit 1
fi
BASE="$(git rev-parse HEAD)"
SINCE="${SINCE_INPUT:-}"
if [ -z "$SINCE" ] && [ -f "$STATE_FILE" ]; then
  SINCE="$(sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' "$STATE_FILE" | head -1 | tr -d '[:space:]')"
fi
if [ -z "$SINCE" ]; then SINCE="$(git merge-base HEAD full/main)"; fi
if ! git cat-file -e "${SINCE}^{commit}" 2>/dev/null ||
   ! git merge-base --is-ancestor "$SINCE" full/main; then
  echo "ERROR: sync state '${SINCE}' is not an ancestor of full/main." >&2; exit 1
fi
SINCE="$(git rev-parse "$SINCE")"

# Filter BEFORE applying. Deleted components and Core's own documents never
# enter the index or conflict resolution. Snapshot Core's rules once.
if [ ! -s "$EXCLUDE_FILE" ]; then
  echo "ERROR: ${EXCLUDE_FILE} is missing or empty; refusing unfiltered replay." >&2; exit 1
fi
PATHS=(.)
COUNT=0
while IFS= read -r path || [ -n "$path" ]; do
  path="$(printf '%s' "$path" | sed 's/#.*//; s/^[[:space:]]*//; s/[[:space:]]*$//')"
  [ -n "$path" ] || continue
  PATHS+=(":(exclude)$path")
  COUNT=$((COUNT + 1))
done < "$EXCLUDE_FILE"
if [ "$COUNT" -eq 0 ]; then echo "ERROR: exclude list has no paths." >&2; exit 1; fi
PATHS+=(":(exclude)$STATE_FILE" ":(exclude)$EXCLUDE_FILE" ":(exclude)$REPORT_FILE")

# Carry merges once as their first-parent delta, including conflict resolutions.
COMMITS="$(git rev-list --first-parent --reverse "${SINCE}..full/main")"
if [ -z "$COMMITS" ]; then
  emit "carried=0"; emit "skipped=0"; emit "blocked="; emit "should_pr=false"
  echo "Core is already level with the full build at ${SINCE}."; exit 0
fi
REMOTE_TIP=""
if [ "${DRY_RUN:-}" != "1" ]; then
  REMOTE_TIP="$(git ls-remote origin "refs/heads/$SYNC_BRANCH" | cut -f1)"
fi
git config user.name "speda-sync"
git config user.email "sync@users.noreply.github.com"
git switch -C "$SYNC_BRANCH" >/dev/null 2>&1
PATCH="$(mktemp)"
APPLY_LOG="$(mktemp)"
trap 'rm -f "$PATCH" "$APPLY_LOG"' EXIT
CARRIED=0
SKIPPED=0
LAST_OK="$SINCE"
BLOCKED=""
BLOCKED_SUBJECT=""
for sha in $COMMITS; do
  SUBJECT="$(git log -1 --format=%s "$sha")"
  git diff --binary "${sha}^1" "$sha" -- "${PATHS[@]}" > "$PATCH"
  if [ ! -s "$PATCH" ]; then
    echo "Skipped ${sha} (${SUBJECT}) — excluded paths only."
    SKIPPED=$((SKIPPED + 1)); LAST_OK="$sha"; continue
  fi
  if ! git apply --3way --index "$PATCH" > "$APPLY_LOG" 2>&1; then
    BLOCKED="$sha"; BLOCKED_SUBJECT="$SUBJECT"
    cat "$APPLY_LOG" >&2
    # Never advance past ANY apply failure, even without unmerged index entries.
    # The reset only undoes this clean, isolated replay's failed application.
    git reset -q --hard HEAD
    git clean -qfd
    echo "Blocked at ${sha} (${SUBJECT}) — stopping here."; break
  fi
  if git diff --cached --quiet; then
    echo "Skipped ${sha} (${SUBJECT}) — changes already present."
    SKIPPED=$((SKIPPED + 1)); LAST_OK="$sha"; continue
  fi
  {
    git log -1 --format=%B "$sha"
    printf '\nCarried-From: %s@%s\n' "$FULL_REPO" "$sha"
  } | git commit -q -F -
  CARRIED=$((CARRIED + 1)); LAST_OK="$sha"
  echo "Carried ${sha} (${SUBJECT})."
done
cat > "$STATE_FILE" <<EOF
# Last commit of ${FULL_REPO} carried into Core (including excluded-only changes).
# Advances on main only when the carry PR is merged. Failed commits are not skipped.
${LAST_OK}
EOF
"${PYTHON_BIN:-python3}" - "$REPORT_FILE" "$FULL_REPO" "$SINCE" "$LAST_OK" \
  "$CARRIED" "$SKIPPED" "$BLOCKED" "$BLOCKED_SUBJECT" <<'PY'
import json
import sys
path, repo, since, last_ok, carried, skipped, blocked, subject = sys.argv[1:]
with open(path, "w", encoding="utf-8", newline="\n") as report:
    json.dump(dict(full_repo=repo, since=since, last_ok=last_ok,
                   carried=int(carried), skipped=int(skipped), blocked=blocked,
                   blocked_subject=subject), report, ensure_ascii=False, indent=2)
    report.write("\n")
PY
git add "$STATE_FILE" "$REPORT_FILE"
git diff --cached --quiet || git commit -q -m "chore(sync): record carry point ${LAST_OK}"
emit "carried=${CARRIED}"; emit "skipped=${SKIPPED}"; emit "blocked=${BLOCKED}"
emit "since=${SINCE}"; emit "last_ok=${LAST_OK}"
if git diff --quiet "$BASE" HEAD; then emit "should_pr=false"; exit 0; fi
# Cursor-only advances also need a PR or excluded changes repeat forever.
emit "should_pr=true"
if [ "${DRY_RUN:-}" = "1" ]; then
  echo "DRY_RUN — branch ${SYNC_BRANCH} is left in place without pushing."
else
  # A concurrent push must fail rather than overwrite a tip we never observed.
  git push --force-with-lease="refs/heads/$SYNC_BRANCH:$REMOTE_TIP" \
    origin "HEAD:refs/heads/$SYNC_BRANCH"
fi
