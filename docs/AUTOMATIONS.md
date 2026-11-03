# Native automation control

n8n owns schedules. `app.state.automations` owns versioned definitions and durable
remote operations; Coordinator alone admits and executes agent runs. The retained
schedule/template/composer code supplies structured schedules and workflow graphs.
There is no intent-polishing model, hidden retry loop or internal business timer.

An owner request first commits a definition and operation. A create, update,
activation, deactivation or deletion records its dispatch state before network I/O.
Lost acknowledgements stay reconciling. Read-only reconciliation can discover a
create by its reserved native name or confirm an update/publication/deletion;
it cannot send another uncertain mutation. CAS and operation-stage checks fence
stale handlers. Edits preserve node identity and remote static data. Unexpected
remote content is held instead of overwritten. Tool-driven synchronization checks
the originating run before each further dispatch. Full remote effects remain a
live n8n acceptance gate.

`POST /automations`, `PUT /automations/{id}`, toggle, test, runs, delete, reconcile
and sync retain the original settings contracts. New clients/tools should send
`expected_version` and stable create `request_id`. Old clients can omit the edit
version for compatibility; this is weaker than the explicit CAS tool path.
Without n8n configuration definitions remain pending and are never called active.
Workflow snapshots persist placeholders, not callback or provider API keys.

Owner timezone is pinned on each definition and n8n workflow. Once dates must be
explicit and future; expiry has an offset. Unsupported polling intervals are
rejected rather than silently compiling a different frequency. Callback identity
is computed in n8n, rather than guessed by an agent. The stored instruction and
output mode override incoming instruction fields. Completed exact retries retain
their admitted instruction even after a pause or edit. Incoming data has bounded,
redacted previews and remains external evidence.

## Mail watches

`hook_mail` and raw `mail_watch` support `mail_provider: google | microsoft`
(Google is the compatibility default). Connect OAuth and grant the owning agent
mailbox search/read capabilities before saving the definition. Explicit account
login/disconnect changes a persistent identity generation; token refresh does
not. Existing watches require owner review/save after account relinking.

The compiler calls `/mail/watch/scan` or `/outlook/watch/scan`, then the ordinary
native trigger, then the matching `/seen`. Each request carries the native key
and content revision. Scans match exact sender domains or recipient addresses,
bound pages, bytes and elapsed time, and retain immutable observed batches.
The trigger reads that stored batch, not a replacement body sent by a workflow.
Acknowledgement requires a durable admitted run. It records a **local per-watch
receipt**; it does not label, mark read or otherwise change the mailbox. This
deliberately replaces the old remote-label writer. Pending batches survive lost
trigger responses. Per-watch cursors progress through a bounded backlog and
reset when exhausted; provider paging URLs cannot redirect the bearer token to
another host. Attachments are not downloaded and linked URLs are not followed.

## Activation and verification

Configure `N8N_API_URL`, `N8N_API_KEY`, `SPEDA_CALLBACK_URL`, the existing API key
and n8n shared secret. Publish only after checking the actual n8n version and
network topology. Enable n8n SSRF protection and narrowly allow the controlled
backend callback; egress policy must also protect watched URLs and redirects.
No live n8n workflow, mailbox or account was changed by local fixture tests.

Tests cover definition identity, remote uncertainty, static-state preservation,
owner scope, read-only reconciliation, original mail batches and local ack order
on SQLite and disposable PostgreSQL. `test_native_controls_api.py` exercises
the settings and authentication adapters. These are local protocol checks,
not vendor acceptance.

References: [n8n workflow API](https://docs.n8n.io/build/work-with-data/workflow.md),
[n8n expression string hashing](https://docs.n8n.io/build/work-with-data/transform-data/expression-reference/string.md),
[n8n SSRF protection](https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/enable-ssrf-protection.md),
[Gmail messages list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list),
[Graph message listing](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0).
