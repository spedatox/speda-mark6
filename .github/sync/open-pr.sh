#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 SPEDA
# SPDX-License-Identifier: AGPL-3.0-or-later
# Reads a replay report and PR metadata; never runs carried backend code.
set -euo pipefail
: "${CORE_REPO:?}"
: "${REPORT_FILE:?}"
SYNC_BRANCH="${SYNC_BRANCH:-sync/from-full-build}"
FULL_REPO="$(jq -er '.full_repo' "$REPORT_FILE")"
SINCE="$(jq -er '.since' "$REPORT_FILE")"
LAST_OK="$(jq -er '.last_ok' "$REPORT_FILE")"
CARRIED="$(jq -er '.carried | numbers' "$REPORT_FILE")"
SKIPPED="$(jq -er '.skipped | numbers' "$REPORT_FILE")"
BLOCKED="$(jq -r '.blocked // ""' "$REPORT_FILE")"
BLOCKED_SUBJECT="$(jq -r '.blocked_subject // ""' "$REPORT_FILE")"
BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT
{
  printf 'Carries %s commit(s) from [%s](https://github.com/%s).\n\n' "$CARRIED" "$FULL_REPO" "$FULL_REPO"
  printf 'Range: `%s..%s`. Skipped: %s excluded or already-present commit(s).\n\n' "$SINCE" "$LAST_OK" "$SKIPPED"
  printf 'Review shared-code changes for assumptions about agents, workers or peers before merging into the single-agent Core build.\n\n'
  printf 'This machine-owned branch is rebuilt from Core main on each run. Put fixes on another branch or on main. The sync cursor advances on main only when this PR is merged; closing this PR does not advance it.\n'
  if [ -n "$BLOCKED" ]; then
    printf '\n### Blocked carry\n\n'
    printf '[`%s`](https://github.com/%s/commit/%s) did not apply: %s\n\n' "$BLOCKED" "$FULL_REPO" "$BLOCKED" "$BLOCKED_SUBJECT"
    printf 'The cursor remains before this commit; all later changes are pending. Adapt this commit for Core or explicitly review and skip it on main, then run sync again.\n'
  fi
} > "$BODY"
TITLE="sync: carry ${CARRIED} commit(s) from the full build"
if [ -n "$BLOCKED" ]; then TITLE="${TITLE} (blocked carry)"; fi
# Do not swallow authentication/API errors and then attempt a duplicate PR.
EXISTING="$(gh pr list --repo "$CORE_REPO" --head "$SYNC_BRANCH" --base main \
  --state open --json number,isDraft --jq '.[0] // {}')"
NUMBER="$(printf '%s' "$EXISTING" | jq -r '.number // empty')"
IS_DRAFT="$(printf '%s' "$EXISTING" | jq -r '.isDraft // false')"
if [ -n "$NUMBER" ]; then
  gh pr edit "$NUMBER" --repo "$CORE_REPO" --title "$TITLE" --body-file "$BODY"
  if [ -n "$BLOCKED" ] && [ "$IS_DRAFT" != "true" ]; then
    gh pr ready "$NUMBER" --repo "$CORE_REPO" --undo
  elif [ -z "$BLOCKED" ] && [ "$IS_DRAFT" = "true" ]; then
    gh pr ready "$NUMBER" --repo "$CORE_REPO"
  fi
  URL="https://github.com/$CORE_REPO/pull/$NUMBER"
else
  OPTIONS=()
  if [ -n "$BLOCKED" ]; then OPTIONS+=(--draft); fi
  URL="$(gh pr create --repo "$CORE_REPO" --base main --head "$SYNC_BRANCH" \
    --title "$TITLE" --body-file "$BODY" "${OPTIONS[@]}")"
fi
printf '%s\n' "$URL"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  printf '[Carry PR](%s) — carried %s, skipped %s.\n' "$URL" "$CARRIED" "$SKIPPED" >> "$GITHUB_STEP_SUMMARY"
fi
if [ -n "$BLOCKED" ]; then
  echo "::error::Carry stopped at $BLOCKED; later commits remain pending."; exit 1
fi
