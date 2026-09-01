# Working agreement for this repo

This file is rules for how to work here, not architecture documentation. For that, read [README.md](README.md) for the system overview, and the relevant package doc before touching that package's code:

| Package | Doc |
|---|---|
| `packages/igor` | [IGOR.md](packages/igor/IGOR.md) |
| `packages/heartbreaker` | [HEARTBREAKER.md](packages/heartbreaker/HEARTBREAKER.md) |
| `packages/striker` | [STRIKER.md](packages/striker/STRIKER.md) |
| `packages/speda-go` | [README.md](packages/speda-go/README.md) |
| `packages/browser` | [README.md](packages/browser/README.md) |

If any of those docs disagrees with the code, the code is right and the doc is wrong — fix the doc as part of whatever change you're making, don't leave it for later.

---

## Standing rules

**Cross-client parity.** Any change made to Heartbreaker — a UI change, a behavior change, a new feature — gets applied to Striker and Speda GO in the same piece of work, not filed as a follow-up. Exception: things Striker deliberately excludes (no roster, no switcher, no House Party) and things that are desktop-only by nature don't need a forced mobile equivalent.

**No hardcoded values.** Maximum configurability is the goal. If a value could plausibly need to change without a code change — a threshold, a limit, a model id, a timeout, a feature flag — it's a setting, not a literal. And it's not done halfway: wire it into the backend config schema *and* into the settings surface of whichever client(s) it's relevant to. A setting that only exists in `config.py` with no UI path to it isn't finished.

**Fix what you see.** If you notice an error while working — even one unrelated to the task — fix it, after the assigned task is done. Don't let it derail the task in progress, and don't leave it noticed-but-untouched either.

**Ship it.** Commit and push completed work without asking for confirmation first — that includes incidental fixes from the rule above. This does not extend to force-push or rewriting history; those still get flagged before they happen, every time.

**No co-author trailer.** Never add `Co-Authored-By: Claude ...` to a commit in this repo. This is his authorship record. Write the subject line and a prose body explaining the reasoning, and stop there.

---

## Licensing

AGPL-3.0-or-later. Every source file carries an SPDX header (`SPDX-FileCopyrightText` / `SPDX-License-Identifier`) — new files need one too.
