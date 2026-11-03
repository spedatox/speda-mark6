# Local testing — 29 October 2026

Open **http://127.0.0.1:5273/**. The backend runs at
**http://127.0.0.1:5384/** and its managed launcher supports the Configuration
panel's restart button. Double-click `Start Local Testing.cmd` in `renovated`
to start missing services and open the web client again. Both services bind
only to this computer. This is the Heartbreaker web client; desktop packaging
and mobile testing are separate.

The workspace root also has **Start SPEDA Test.cmd**, which calls the same
launcher. Double-click either file from Explorer. The backend and frontend run
in the background, and the browser opens after they are ready. Clicking again
reuses running services. If startup fails, the window stays open with the error
and log location.

Private configuration, logs, generated files and the test database live under
`C:/Users/ahmet/.codex/local-testing/renovated-mark-vi/`. The launcher reads
`local.env` there without displaying credentials. It does not change the normal
`~/.speda` settings, the original reference checkouts, or production.

## Models and spending

The local OpenAI credential was verified against the provider's model catalog.
GPT-6 Luna is the initial model, with low reasoning. GPT-6 Sol is also priced
for optional selection. Other model catalog entries are visible but may lack
credentials; missing spending rates no longer block their use.
All personas inherit the local default. A previously saved browser model choice
still takes precedence; choose GPT-6 Luna in the composer if necessary.

At the owner's request on **3 November 2026**, the local global and coordination
policies have `enforce_limits: false`. The artificial $5 global allowance,
room spending ceilings, daily allowances and missing-price admission blocks no
longer prevent chat. Provider billing still applies. Call records, concurrency,
per-run execution bounds and explicit pause controls remain active. Configuration
→ advanced runtime policies exposes this persistent option; relaunching preserves
it. Iacon maintenance retains its separate $1 daily allowance. No recurring job
or live automation is enabled.

The declared per-token bounds are deliberately conservative: 1 microdollar for
Luna, 20 for Sol, applied to input and output, with a 16,384-token per-image
allowance. See [the provider pricing reference](https://developers.openai.com/api/docs/pricing).
These rates are local reservation estimates, not quoted actual usage charges.
Unpriced calls have no monetary reservation recorded; zero reservation does not
mean a free call or zero vendor charges.

## Memory

The test database was freshly imported from the retained **10 October 2026**
offline snapshot: 424 documents, 11,397 owner messages and 2,443 observation
claims. Iacon imported 424 documents, seven entities and 21 states. The source
snapshot and prior research databases remain intact. The import report is
`memory-import.json` in the private data directory.

This is historical memory: changes since 10 October are absent. A separate
conversation import now also restores **1,858 sessions and 24,604 messages**
to the client history, preserving agent identities, titles, timestamps, content
blocks and inline images. It also retains 6,144 historical tool-call records
and archives 111 session-linked agent messages as historical evidence. Original
IDs are recorded in durable mappings rather than reused over local records.
The existing setup chat remains intact. Use **Load older conversations** at the
bottom of Speda's history to reach sessions beyond the first 500.

`scripts/import_chat_snapshot.py` performs this additive import with a mandatory
new backup and report path. Exact snapshot repeats are idempotent; a different
snapshot requires overlap reconciliation. `chat-import-20261029.json` records
this run, and `testing-before-chat-import-20261029.db` is the retained rollback
copy. Nothing is re-executed or re-extracted. External files referenced by old
messages are not bundled in a database snapshot. New local chats and memory
changes stay in the test database; this remains short of a full application-data
restore.

## Suggested first checks

1. Chat normally, then reload and reopen the conversation to check persistence.
2. Ask about a specific fact present before 10 October and inspect the answer.
3. Open Memory to inspect records; try a small correction in this test copy.
4. Summon a specialist from the conversation menu and check attribution.
5. Try Canvas, an attachment, and a generated document as separate checks.
6. Inspect Configuration, change a harmless setting and use Restart backend.

The minimal live chat check has already returned a real GPT-6 Luna response.
Frontend type checking and 43 client tests passed during preparation. Local
health/readiness, authenticated access and browser CORS were checked. A local
browser connection issue was fixed by allowing explicit IPv4 loopback in the
page's existing localhost connection policy.

## Features that need more setup

External MCP/OSS startup integrations, mail, Telegram and n8n delivery are off.
Docker project execution and publication are off. Hisar/library sidecars and
continuous speech are not activated by this setup. House Party requires its
owner-configured passphrase. These controls remain available for later focused
testing; their presence does not mean their external dependencies are running.

Logs are `backend.log` and `frontend.log` in the private data directory. To check
status without launching another process, run the renovation Python environment
with `scripts/local_testing.py status` from this checkout. The start command
reuses the running backend only if its authentication matches this test instance.
Neither start nor status deletes or resets test data.
