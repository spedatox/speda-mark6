# Automation templates

Import one, edit **one node**, activate. Fork it when you want another variant.

Every template here is built on the same rule, which is the only thing you need
to understand before changing one:

> **A poll must not cost a turn.** Checking whether something happened is a
> plain HTTP call and free. Only when the answer is *yes* does an agent wake up
> and spend a model call.

That split is visible in every workflow as one node called **Gate**. Everything
upstream of the Gate is free; everything downstream costs money. A Gate that
returns no items stops the branch — which is what happens on the overwhelming
majority of runs.

---

## The templates

| File | Watches for | Costs a turn when |
|---|---|---|
| `mail_watch.json` | Mail from sender domains you list | mail actually arrives |
| `web_publish_watch.json` | New lines appearing on pages you list | something is published |
| `service_health_check.json` | Services going down / coming back | a service changes state |
| `persistent_reminders.json` | *(nothing — it asks)* | never; asking and answering are free |
| `daily_briefings.json` | *(nothing — it schedules)* | **every firing, by design** |
| `ultron_wear_attendance.json` | A lecture ending unanswered | never; it pushes to the watch directly |

`daily_briefings.json` is the deliberate exception: you are asking an agent to
go and do work, so of course it costs a turn. Keep watchers out of it. Anything
shaped like *"check whether X happened"* belongs in one of the watch templates,
where checking is free.

---

## How to use one

1. **Import** — n8n ▸ Workflows ▸ Import from File.
2. **Edit the one config node.** Each workflow has exactly one, and its name
   tells you (`Mail list`, `Watch list`, `Reminder list`, `Briefing list`,
   `Service list`). Everything is a plain JS array of objects at the top of the
   node; the comment block above it documents every field.
3. **Activate.** All of them are safe to activate with an empty list — they do
   nothing until you add an entry.

Nothing else in the workflow needs touching. Adding a tenth watched page or a
fifth reminder is one more `{ … }` block; every node downstream already runs
once per entry.

## How to fork one

Duplicate the workflow (⋯ ▸ Duplicate), rename it, and edit the list. Use this
when you want:

- **a different agent's voice** — most templates take a per-entry `agent`, but
  reminders are per-workflow (one `AGENT` constant), so a second agent's
  reminders means a second workflow;
- **a different cadence** — e.g. one page checked every 5 minutes while the
  rest are hourly;
- **separation** — keeping work automations away from personal ones so you can
  deactivate a whole group at once.

Forked workflows share nothing but their code. Their memory (`staticData`) is
per-workflow, and Igor's state is keyed by the ids you write in the list, so
**reusing a `watch_id` or reminder `id` across two workflows makes them fight
over the same state.** Keep ids unique.

---

## Things that will bite you

**Ids are permanent.** `watch_id`, reminder `id`, briefing `id` — renaming one
starts a fresh entity. For a watch that means one silent re-baseline; for a
reminder it means the history under the old name is orphaned.

**Times are `Europe/Istanbul`.** Every template pins its own timezone in
workflow settings, so it stays right regardless of the container's clock. If
you fork one, do not remove that setting. (The backend agrees:
`app/core/clock.py` is the single source of truth there.)

**The ack always comes last.** In the watch templates the notify node runs
*before* the mark-seen / ack node, and the notify node deliberately has no
`neverError`. If notifying fails, the branch dies and the item stays unhandled,
so the next poll retries it. Reordering these silently converts "you get told
twice occasionally" into "you never hear about it". Don't.

**Health alerts are edge-triggered.** Down, and later recovered — one message
each, not one per poll. The flags live in `staticData`, which resets if you
re-import the workflow, so the first tick after a re-import re-announces
anything currently broken.

**`$env` needs the secrets.** All of these authenticate with
`{{ $env.SPEDA_API_KEY }}` and `{{ $env.N8N_SECRET }}`, which reach the n8n
container from `docker-compose.yml` (sourced from `packages/igor/.env` by
`deploy.sh`). n8n 2.x also blocks `$env` in expressions unless
`N8N_BLOCK_ENV_ACCESS_IN_NODE=false`, which compose sets. If a node fails with
*"access to env vars denied"*, that is the flag.

---

## The endpoints behind them

| Probe (free) | Ack / commit | Notes |
|---|---|---|
| `POST /mail/watch/scan` | `POST /mail/watch/seen` | Gmail via the owner's existing Google connection |
| `POST /web/watch/scan` | `POST /web/watch/ack` | also `GET /web/watch`, `DELETE /web/watch/{id}` to inspect and reset |
| `POST /reminders/tick` | *(button tap or the `reminders` tool)* | also `GET /reminders/open`, `GET /reminders/history` |
| `POST /academic/ask-pending` | *(the watch answers)* | Ultron Wear |

All require `X-API-Key` **and** `X-N8N-Secret`. None of them run a model.
