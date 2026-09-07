#!/usr/bin/env bash
# Replay this repo's new commits onto a checkout of speda-mark6-core, stripping
# every path Core does not own, and push the result to a machine-owned branch.
#
# Run from inside the Core checkout. Called by .github/workflows/sync-to-core.yml,
# and directly by hand for a dry run:
#
#   cd /path/to/speda-mark6-core
#   git remote add full /path/to/speda-mark6 && git fetch full main
#   DRY_RUN=1 FULL_REPO=spedatox/speda-mark6 bash .../replay.sh
#
# DRY_RUN=1 does everything except push, and leaves the branch behind to inspect.
#
# Inputs (environment):
#   SINCE_INPUT   optional; replay from this SHA instead of Core's recorded state
#   FULL_REPO     owner/name of the full build, for the Carried-From trailer
#   SYNC_BRANCH   branch to push (default sync/from-full-build)
#   DRY_RUN       set to 1 to skip the push
#
# Outputs are appended to $GITHUB_OUTPUT when it is set.
set -euo pipefail

STATE_FILE=".github/sync-state"
EXCLUDE_FILE=".github/sync-exclude"
SYNC_BRANCH="${SYNC_BRANCH:-sync/from-full-build}"
FULL_REPO="${FULL_REPO:-spedatox/speda-mark6}"

emit() { [ -n "${GITHUB_OUTPUT:-}" ] && printf '%s\n' "$1" >> "$GITHUB_OUTPUT" || true; }

git config user.name  "speda-sync"
git config user.email "sync@users.noreply.github.com"

# ── Where to start ───────────────────────────────────────────────────────────
# The manual input wins; otherwise the SHA Core recorded on its own main;
# otherwise the fork point, which carries everything since the split.
if [ -n "${SINCE_INPUT:-}" ]; then
  SINCE="$SINCE_INPUT"
elif [ -f "$STATE_FILE" ]; then
  SINCE="$(grep -v '^[[:space:]]*#' "$STATE_FILE" | tr -d '[:space:]' | head -1)"
else
  SINCE=""
fi
if [ -z "${SINCE:-}" ]; then
  SINCE="$(git merge-base HEAD full/main)"
  echo "No recorded state — starting from the merge base ${SINCE}."
fi
if ! git cat-file -e "${SINCE}^{commit}" 2>/dev/null; then
  echo "ERROR: recorded sync state '${SINCE}' is not a commit this clone can see." >&2
  exit 1
fi

COMMITS="$(git log --reverse --format=%H "${SINCE}..full/main" || true)"
if [ -z "$COMMITS" ]; then
  echo "Core is already level with the full build at ${SINCE}."
  emit "carried=0"; emit "skipped=0"; emit "blocked="; emit "should_pr=false"
  emit "since=${SINCE}"; emit "last_ok=${SINCE}"
  exit 0
fi

# The branch is machine-owned: rebuilt from Core's main on every run so that an
# unmerged carry and the commits that follow it always arrive as one coherent
# series. Nothing hand-written may live here — see the PR body.
git switch -C "$SYNC_BRANCH" >/dev/null 2>&1

# ── The strip ────────────────────────────────────────────────────────────────
# Snapshot the list ONCE, before any pick runs. It is read out of the worktree,
# and the worktree is exactly what a cherry-pick mutates — reading it inside the
# loop means a pick that touches .github/ can change the rules mid-replay, and a
# replay onto a commit that predates the file silently strips nothing at all.
EXCLUDE_LIST="$(mktemp)"
if [ -f "$EXCLUDE_FILE" ]; then
  sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e 's/^[[:space:]]*//' "$EXCLUDE_FILE"     | grep -v '^$' > "$EXCLUDE_LIST"
fi
if [ ! -s "$EXCLUDE_LIST" ]; then
  echo "ERROR: ${EXCLUDE_FILE} is missing or empty in this checkout." >&2
  echo "Refusing to replay: without it, every path the fork deleted comes back." >&2
  exit 1
fi
echo "Strip list: $(wc -l < "$EXCLUDE_LIST") path(s)."

# Undo whatever the pick did to an excluded path — do NOT simply delete it.
#
# This distinction is the whole correctness of the strip. Core HAS a README.md,
# a CLAUDE.md, an IGOR.md, docs/ and logos/; they are excluded because the full
# build's versions describe a roster and must never overwrite Core's. Deleting
# them "because they are excluded" wipes Core's own copies with every carry —
# which is exactly what the first version of this function did.
#
# So: restore the path to what Core's HEAD has. If HEAD has no such path, the
# pick invented it (a Heartbreaker file, say) and it is dropped outright.
#
# Run after each pick, so a commit touching only Core-less paths collapses to
# nothing and is skipped rather than carried as an empty commit.
strip_excluded() {
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    if git cat-file -e "HEAD:${path}" 2>/dev/null ||        [ -n "$(git ls-tree -r --name-only HEAD -- "$path" 2>/dev/null)" ]; then
      git rm -rq --cached --ignore-unmatch -- "$path" 2>/dev/null || true
      rm -rf -- "$path" 2>/dev/null || true
      git checkout -q HEAD -- "$path" 2>/dev/null || true
    else
      git rm -rq --cached --ignore-unmatch -- "$path" 2>/dev/null || true
      rm -rf -- "$path" 2>/dev/null || true
    fi
  done < "$EXCLUDE_LIST"
}

CARRIED=0
SKIPPED=0
LAST_OK="$SINCE"
BLOCKED=""
BLOCKED_SUBJECT=""

for sha in $COMMITS; do
  SUBJECT="$(git log -1 --format=%s "$sha")"

  # -n stages without committing, so the strip runs before anything is written.
  # A conflict is NOT resolved here: deciding what a shared-code change means in
  # a one-agent build is the whole reason this ends in a PR.
  git cherry-pick -n "$sha" >/dev/null 2>&1 || true
  strip_excluded

  if git diff --name-only --diff-filter=U | grep -q .; then
    BLOCKED="$sha"
    BLOCKED_SUBJECT="$SUBJECT"
    git cherry-pick --abort >/dev/null 2>&1 || true
    git reset -q --hard
    git clean -qfd
    echo "Conflict on ${sha} (${SUBJECT}) — stopping here."
    break
  fi

  # Clear the sequencer state WITHOUT touching the tree. `git reset --merge`
  # would throw the pick away; --quit leaves the staged result alone.
  git cherry-pick --quit >/dev/null 2>&1 || true
  git add -A

  if git diff --cached --quiet; then
    # Everything this commit touched is excluded — nothing to carry.
    echo "Skipped ${sha} (${SUBJECT}) — excluded paths only."
    SKIPPED=$((SKIPPED + 1))
    LAST_OK="$sha"
    continue
  fi

  # The original message, plus a line saying where it came from. That trailer is
  # the only reliable way to answer "is this already in Core?" later, since the
  # replayed commit necessarily has a different SHA.
  {
    git log -1 --format=%B "$sha"
    printf '\nCarried-From: %s@%s\n' "$FULL_REPO" "$sha"
  } | git commit -q -F -
  echo "Carried ${sha} (${SUBJECT})."
  CARRIED=$((CARRIED + 1))
  LAST_OK="$sha"
done

# ── Record how far we got ────────────────────────────────────────────────────
# In the same series, so merging the PR advances Core's state in one act and the
# next run resumes from exactly here.
cat > "$STATE_FILE" <<EOF
# Last commit of ${FULL_REPO} that has been carried into this repo.
#
# Written by the sync-to-core workflow in the full build. Everything after this
# SHA on the full build's main is what the next sync PR will contain, so moving
# it by hand is how you tell the sync to skip a commit (or to re-carry one).
${LAST_OK}
EOF
git add "$STATE_FILE"
git diff --cached --quiet || git commit -q -m "chore(sync): record carry point ${LAST_OK}"

emit "carried=${CARRIED}"
emit "skipped=${SKIPPED}"
emit "blocked=${BLOCKED}"
emit "blocked_subject=${BLOCKED_SUBJECT}"
emit "since=${SINCE}"
emit "last_ok=${LAST_OK}"

if [ "$CARRIED" -eq 0 ] && [ -z "$BLOCKED" ]; then
  echo "Nothing to carry: ${SKIPPED} commit(s) touched only excluded paths."
  emit "should_pr=false"
  exit 0
fi
emit "should_pr=true"

if [ "${DRY_RUN:-}" = "1" ]; then
  echo "DRY_RUN — not pushing. Branch ${SYNC_BRANCH} is left in place."
  exit 0
fi

# --force, not --force-with-lease: the branch is rebuilt from Core's main every
# run by design, so the remote tip is always expected to be stale. The lease adds
# nothing here except a failure mode when the remote-tracking ref is behind.
git push --force origin "$SYNC_BRANCH"
