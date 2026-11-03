# Personal recall repair — incident 1861

The local renovation could answer a personal-memory question with only timezone
context because automatic memory assembly failed. A legacy observation had equal
validity endpoints; strict evidence validation raised and discarded every already
selected section. The explicit sibling-name lookup failed on another such row.
Both sibling identities were present in the imported person records.

## Behavior

- Invalid raw-source intervals and invalid prepared evidence sections are withheld
  individually. Other valid evidence remains available in the bounded packet.
  The compact result prioritizes the invalid-evidence reason over quota messages.
  Original text, dates, provenance and source identities are unchanged. Equal and
  reversed date ranges are not silently reinterpreted; affected evidence remains
  available for inspection and needs temporal review before normal serving.
- Retrieval reads the content of server-generated room envelopes, preserving the
  distinction between owner messages, attributed agent contributions and external
  retrieval cues. Room sequence labels and operational decision packets do not
  become search terms. Ordinary owner-supplied JSON is retained as owner content.
- Short referential follow-ups, including `their names?`, `theri names?`, `names?`
  and `adları?`, borrow the nearest useful conversational referent. Name follow-ups
  omit unrelated apology/correction sentences from that referent.
- English and Turkish family-relation queries route to explicit owner-relative
  identity assertions. Routing uses the owner's stored name or canonical dossier
  identity when the user display name is the generic `Owner`. It does not contain
  the actual owner's or relatives' names. A named person's family is not assumed
  to be the owner's family; family mentions in event bullets and indirect claims
  such as a sister's friend do not establish the identity relationship.
- Relevant family identity sections outrank unrelated generated records. Name
  requests prioritize the identity assertions rather than retrieving the whole
  relationship history. Existing evidence attribution, dependency fences,
  historical qualifiers, revocation, deadlines and shared budgets still apply.
- Existing messages from the same chat are not duplicated as retrieved sources.
  The prompt asks the assistant to use available recall for an unanswered personal
  detail and distinguish failed retrieval from a fact never having been stored.

## Verification

`tests/test_memory_recall_regressions.py` reproduces the bad temporal metadata,
coordination envelopes, follow-up typo, bilingual family identities, unrelated
records, cross-owner isolation and indirect relationship traps. It includes the
automatic personal-fact → siblings → names retrieval flow.

Targeted suite: **76 passed**, comprising memory runtime and regressions,
contextual capture, Iacon and integration, chat integration, coordination and
lifecycle. Pytest scratch was outside the checkout and its cache remained disabled.

Read-only replay against the owner's local database passed all five captured
automatic contexts and the explicit sibling lookup, with both identity names in
the names packets. Valid packets stayed within the original 6,000/4,000 automatic
and lookup UTF-8 upper bounds. No provider call was added to retrieval. The replay
used current local data, so it is corroborating evidence rather than a historical
snapshot reconstruction.

Local verification artifacts and the original investigation are under
`C:/Users/ahmet/.codex/local-testing/renovated-mark-vi/investigation-1861/`.
The managed local backend was reloaded while idle and reported a new ready
instance without configuration changes. Live chat **1864**, titled **Memory
repair verification — chat 1861**, then completed all three prompts:

- `tell a fact about me`: recalled the owner's childhood work with his father
  in the family's beekeeping operation.
- `Do I have siblings?`: named Alper Kaan and İlayda.
- `theri names?`: answered “Your brother is **Alper Kaan**, and your sister is
  **İlayda**.”

The full live debug export is `verified-chat.json`; `reload.json` and
`chat-check.json` record the instance change and checked replies.
This repair does not deploy the renovation to production or replace a dataset.
