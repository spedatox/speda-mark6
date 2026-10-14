# Solus accounting workspace

Implementation: 11 October 2026. Owner scope: personal finance and freelance/small
business bookkeeping. Standalone UI: `https://speda.spedatox.systems/solus`.

## Owner workflow

Set the initial password using the private, expiring setup link supplied at
deployment, or the owner API key in the first-setup form. No default password is
installed. Subsequent access uses the password. Settings changes the password
after verifying the current one and signs out every browser session. Lock
workspace signs out the current browser.

Create a personal or business book, its currency/timezone, and named accounts.
Accounts use asset, liability, equity, income or expense types and one fixed
native currency. Nothing guesses the owner's bank accounts or card dates.

For each credit card, create a liability account and use **Cards & statements**
to enter its closing day and either a due day or days after closing. Short months
clamp to their last day. Closing day is inclusive; later bank bookings enter the
next cycle. Enter an effective closing month for a changed calendar. Individual
issued statements can override actual closing/due dates, balance and minimum
payment. Original schedules survive; reports use the effective dated rules.
Reopen a closed period before changing its statement/calendar.

The journal recognizes a card purchase once, for its full amount. Installments
are obligations; paying the card reduces debt and cash without another expense.
Occurrence and bank booking dates are separate. Default installments conserve
every minor unit; remainder units go to the earliest installments. Obligations
can be partially settled. Reversing a payment releases its allocations without
erasing history.

Invoices and bills recognize a receivable/payable and revenue/expense on issue.
Add a document number, party and first due date; record actual partial payments
or credit notes against obligations. Reports show month-end balances, retained
result and aging. This is accrual bookkeeping; no automatic statutory cash-basis
or tax calculation is implied. Loans, interest, fees, withholding, shared
purchases and owner contributions/draws use their explicit principal, expense,
receivable or equity accounts. Advanced journals support splits and explicit FX
clearing lines, balanced separately in each currency.

Budgets, recurring rules and goals are plans. Forecasts show commitments and
recurring estimates separately and never post a payment automatically. n8n
remains the sole scheduler.

Investment acquisitions retain quantity precision, native-currency cost lots
and the chosen fee treatment. Sales select a specific lot, conserve its cost
across partial disposals and recognize actual gain/loss and fees. Dated supplied
valuations remain observations, never income. Backdating or reversing dependent
disposals requires reversing later disposals first. No quotes, FX rates, FIFO,
tax-lot optimization or jurisdictional filing amounts are invented.

## Imports, reconciliation and migration

Historical migration belongs to the owner. Legacy typed `finance_record` and
prototype storage remain available until explicit retirement. Solus never
includes legacy records in its totals and agents must never dual-write an
occurrence. **Import & history** provides a read-only legacy inventory.

The mapped transaction CSV format is:

```text
date,description,kind,amount,debit_account,credit_account,currency,event_ref
2026-08-10,Example expense,expense,10.00,Shopping,Checking,TRY,bank-row-001
```

Account names/currencies must match accounts already created in the book.
Optional columns: `installments,booked_on,first_due_on,document_number,party`.
Use stable original occurrence references across overlapping exports. Preview
validates the entire batch in an isolated rollback transaction; commit preserves
the original text/hash and posts all rows atomically. Duplicate original files
or occurrence references are rejected. No fuzzy matching or locale guessing.
Maximum: 500 rows and 1 million text characters per import.

Reconciliation uses a separate statement CSV:

```text
date,description,amount,event_ref
2026-08-10,Example expense,-10.00,bank-row-001
```

Positive amounts increase the bank asset or card debt; negative amounts reduce
it. Supply opening/closing balances and statement bounds, then explicitly match
every original row to its posted event. Every ledger movement in that interval
must be covered with the exact amount. A matching balance alone is insufficient.
Card reconciliation uses billed principal and configured statement boundaries.
The immutable result retains the source and basis hash; subsequent journal or
calendar changes show **needs review** rather than silently preserving a stale
reconciled claim.

Posted events are corrected by reversal and replacement. They cannot be deleted
or edited. Period controls close/reopen with the current ledger revision.
Journal JSON export is labelled as the loaded pages; it is not a complete backup.

## Integrity and security

Amounts arrive as exact decimal strings and persist as integer minor units.
Unsupported currencies/precision, ambiguous separators and non-finite values are
rejected. Each event balances debits/credits per native currency. Reports use
Python integer accumulation, preserving exact totals beyond SQLite SUM's range.
Posted journals, schedules, allocations, lots, originals and command receipts
have database immutability guards and book/owner foreign-key boundaries.

`memory_store.run_financial_command` uses a fresh SQLite writer and
`BEGIN IMMEDIATE`. Journal effects, immutable source, deterministic memory view,
metadata/passages/graph/revision and command receipt commit or roll back together.
It never commits a chat session's unrelated dirty rows. Agent assertions require
owner-scoped original evidence and existing admission policy. Owner UI forms
are retained as exact source evidence. Retries must retain command/occurrence
keys; a changed payload with the same key conflicts. Browser storage retains
only an opaque retry key and payload hash after an uncertain network result,
never the financial form or credentials. Receipt lookup resolves uncertain saves.
If a refresh lost the form, **Resolve earlier save** takes the same writer fence:
it returns a committed receipt or records an immutable cancellation that prevents
the old request from posting later. It never removes a committed transaction.
Multi-query browser and agent reports use an explicit read snapshot.

Passwords use salted scrypt (N=131072, r=8, p=1); hashing concurrency is bounded.
Login attempts are durably limited per peer and installation. Session tokens
are random and stored only as hashes, scoped to the Solus API, with HttpOnly,
Secure and SameSite=Strict cookies in HTTPS deployments. Sessions expire after
30 minutes idle or eight hours absolute and bind to the credential edition.
Cookie-authenticated writes require a CSRF token and reject a foreign Origin.
Credential validation errors never echo submitted passwords.

The public shell contains no financial data. All accounting reads/writes require
the existing installation key or scoped browser authentication. Other Igor APIs
retain their installation-key protection. Non-loopback Solus API access requires
HTTPS. Uvicorn trusts the actual Caddy IP only; never set forwarded trust to `*`.
Compiled JS/CSS are served locally under a CSP without inline scripts, CDN
compilation or unsafe-eval. APIs are no-store, framing is denied, and Caddy limits
Solus request bodies to 2 MB. TLS and password hashing protect transport and
credentials; the SQLite file itself is not encrypted by this subsystem.

Protect the server, private backups and owner API key as privileged access.
The complete SQLite backup includes Solus originals, receipts and credentials;
Octavius's existing whole-database snapshot covers them. Do not copy live WAL
files independently. Use SQLite backup, verify integrity, and rehearse additive
schema changes on a fresh snapshot. Roll back code with a retained image; keep
new Solus data intact. Do not restore an old full database after new owner writes
without reconciling those writes.

## Maintainer commands and bounds

Build `packages/solus` with `npm ci && npm run build`; Igor's Dockerfile builds
and embeds the same bundle. `deploy.sh` resolves Caddy's current address for
`SPEDA_TRUSTED_PROXY_IPS`. The thin `/solus/api/v1/` router calls app.state's
Solus service. `solus_accounting` uses the same command schema/reports as the UI.
New book/account/card commands are explicit; no owner configuration is hardcoded.

Validated: exact allocation/date cases, direct-SQL protections, owner/book scope,
concurrent retries, projection rollback, currency separation, card booking/closing
and changed statement dates, CSV atomicity/duplicates, complete reconciliation
and stale detection, invoice payments/credits, period closing, cost-lot conservation,
password/session/CSRF/throttling, browser retry recovery and real-browser owner
flows/mobile layout. Existing memory contract/monthly tests also pass.

This release does not implement payroll computation, statutory filings,
manufacturing/inventory, automatic bank payment execution, fuzzy bank import,
receipt attachment ingestion, fiscal-year consolidation/FX valuation, editable
invoice drafts, arbitrary custom lender schedules, account hierarchies, or a
Heartbreaker launch ticket. Use explicit journals where accounting meaning is
known; retain uncertain source data in the legacy review process rather than
guessing. A synthetic local benchmark with 100,000 events, 2,000 card purchases and
6,000 installments passed exact totals beyond SQLite's integer SUM range:
summary 3.09 s, journal page 0.18 s, statement 0.26 s, forecast 0.49 s and
schedule page 0.19 s. This measures read paths on one Windows machine, not
production concurrency or command/source-index throughput. Some reports still
traverse book history; investment positions and repeated backdated projection
rebuilds need separate scaling measurements. The architecture plan remains an extension roadmap, not a promise
of universal tax or accounting compliance.
