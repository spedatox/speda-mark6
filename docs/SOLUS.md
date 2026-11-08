# Solus and Sentinel

Native integration, 25 October 2026. Solus is the financial authority. Iacon owns
personal memory; it does not keep a competing set of balances or financial ledgers.
The original accounting domain is retained under `services/solus`, with source
hashes in the integration manifest. The old memory admission judge and mutable
finance-document projections are not used. Accounting dispatch makes no helper
model call.

## What the server owns

- Integer minor-unit money, supported currency precision and balanced postings
  separately for each currency. No floating-point amount calculations.
- Posting templates for expenses, income, transfers, card purchases/repayments,
  invoices, bills, their payments, loan disbursements and refunds. Sentinel supplies
  source facts rather than debit/credit lines.
- Relative dates anchored to the original owner message in the book's timezone.
  A later provider response, retry or midnight rollover cannot change that date.
  Due dates do not replace occurrence dates. Document uploads require an explicit
  original transaction date; upload time is not used as a transaction date.
- Installment counts read from the source, exact allocation of remaining minor
  units, monthly calendars and supplied statement editions. A stated per-installment
  amount is multiplied in code. Card purchase principal becomes spending once;
  later card repayment does not create another expense.
- Immutable journals, lines, receipts and original imports. Corrections use
  reversals. Closed periods reject posting; the retained command engine also
  supports exact cost lots, valuations, statement reconciliation and reviewed CSV
  imports through the owner interface.
- Stable occurrence and command identities from the source monetary token.
  Rephrased model descriptions and new tool-call IDs cannot post another occurrence.
  Re-uploading the same receipt retains its original file-hash identity. A conflicting
  interpretation of an already used key is refused. Distinct sources without an
  external identifier are not assumed to prove the same real-world transaction.
- Source, owner, room, admitted-input and active-executor checks inside the writer
  boundary. Later queued owner messages are not evidence for an earlier run.
  Revoked or mismatched sources are refused. Original files and extracted quotes
  retain their attribution; uploaded documents are not relabeled owner assertions.

The answering model still interprets the owner's language and selects an event
kind/category. Code cannot prove arbitrary free-text entailment. Contradictory
amounts/currencies, plans, negated payments, ambiguous dates, unknown accounts and
unsupported document extraction return an honest rejection or `needs_information`.
The owner is asked for the missing fact, not to construct an accounting entry.

## Sentinel workflow

`solus_accounting` is offered directly to Sentinel; anonymous workers do not get
financial write authority. `books` and `accounts` expose real IDs, account types,
currencies, card rules and the versioned owner defaults. `configure` admits native
book/account/card configuration and explicit bookkeeping defaults. Those defaults
must name existing accounts in an original owner instruction and carry a revision
check. Ordinary transaction evidence cannot change them. Their revision history
is retained, and a change racing a capture fences the stale write.

`capture` accepts an exact owner quote, or an admitted extracted attachment, plus
ordinary facts. Payment account names may come from that source or an explicitly
configured default. When a book/account/currency is ambiguous, the result provides
the concrete candidates and posts nothing. Original message source IDs can be
used up to this run's admitted input boundary. Default evidence is the current
owner input; an explicit original owner source can come from another conversation.
Later queued inputs, revoked originals and assistant assertions are excluded.

Payments and unpaid schedules are separate facts. With no matching instruction,
a repayment reduces the liability but does not claim to have paid a particular
installment. The receipt reports it as unallocated. An explicit owner default
`card_payment_allocation=oldest_billed` enables deterministic allocation to already
billed installments, in stable order, inside the writer transaction. Future
installments are not marked paid. Any excess remains disclosed as unallocated.
Bill/invoice payments with an exact original document number settle the unique
unreversed obligation in that book and verify the original principal account.

`summary` reports actual occurrence-date totals. `forecast` separates confirmed
unpaid obligations from recurring estimates. `statement` uses the card's real
calendar/statement edition. No estimate automatically creates an actual journal.
`receipt` resolves a saved result without authorizing another occurrence.

Native owner commands support the full retained command schema. Sentinel's capture
surface deliberately disallows arbitrary model-authored journal lines. Advanced
investment/import/reconciliation entry and reversal commands remain on the owner
interface; their exact math is in the retained engine. This distinction is not a
claim that every old agent write operation has been restored.

## Browser and deployment

Original `/solus/api/v1` reports, native commands, receipts, previews and browser
credential routes are mounted. Browser credentials authorize only this API, never
other application routes. Login throttling is persistent; passwords use bounded
scrypt workers. HttpOnly/SameSite cookies, origin/CSRF checks, expiry, idle timeout
and credential-version revocation apply. Setup needs the owner API key or a
one-use private setup token. Financial APIs require HTTPS outside loopback.

For a Docker deployment behind Caddy, see the [Solus HTTPS configuration](deployment/solus-proxy/README.md).

The public static shell contains no financial data. Build assets externally and
set `SOLUS_UI_DIR` to that output directory. The source Vite config's old monorepo
default output path should be overridden in the flat renovated deployment:

```
npm ci --ignore-scripts
npm run build -- --outDir <absolute external output directory>
```

SQLite immutable ledger triggers are installed at native startup and at the writer
boundary. **Solus writes currently require SQLite**, matching the retained engine's
activation restriction. Other renovated modules retain their PostgreSQL coverage;
that does not certify PostgreSQL Solus activation. There is no automatic legacy
financial-data migration, no combined legacy/Solus totals and no production cutover.

Tests exercise the native Sentinel loop with fixture providers, original ledger
invariants, concurrent retries, cancellation, original clocks, defaults, receipt
files, installments, settlements and cookie/CSRF boundaries. Live model quality,
operator book/account configuration and full browser acceptance remain separate
validation work. This integration has made no paid calls or production changes.
