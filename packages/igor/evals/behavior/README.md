# Behavioral recovery evaluation

Only actual completed conversations establish recovery. Input captures show
what reached the model; retrieval ranking shows what the search found. Neither
is a personality score. Keep reports, incident fixtures, vectors and isolated
databases outside Git.

The runner imports SessionManager, CapabilityRegistry, profiles and
AgentOrchestrator from the selected revision. It replays production registry
metadata through that revision's disclosure logic. Local guides, tool loading,
history retrieval and fixture-only memory operations execute their real
handlers. Unsupported external actions stop the conversation as incomplete;
the runner supplies no fabricated result and touches no real accounts.

`turns` extends the original case schema. Each step can supply `message`,
`new_session`, `client_context`, `requested_language`, `cache_state` (`cold`,
`warm` or retained), `custom_instructions`, and `post_turn`. Replies and receipts are actually saved;
subsequent steps use normal history loading. `prior_sessions` seed raw saved
exchanges without requiring an observation or recap. Canvas uses the normal
ClientContext annotation. `index_history: false` tests missing vectors.
`embedding_outage: true` explicitly injects a query-embedding outage while
leaving generation on the verified model; it is labelled in the report.
Normal post-turn work runs after streaming unless the fixture disables it.

`cases.json` covers the six capabilities. `memory_cases.json` adds paraphrase,
Turkish/English recall, old conversations, persisted new information,
corrections, irrelevant similarity, missing embeddings, an outage, cold/warm
caches and 20,635 background messages. Volume fixture provisioning is separate
from foreground request timing. Seed vectors use the configured embedding model
and a shared compact float32 fixture with verified provider identity. Later
revisions reuse the same vectors. Historical sources/attempts remain evidence;
retrieval never admits facts.
Seeded vectors also receive the selected revision's normal keyword projection,
so older revisions are evaluated with their real indexed state. Missing-vector
fixtures keep the distinction between the old path and immediate raw-text indexing.

## Production equivalence

Read `GET /admin/evals/behavior-config` using the normal `X-API-Key`. The
allowlisted snapshot includes generation, retrieval, indexing and runtime
settings, per-agent conversational/background allocations, API routes, tool
catalog metadata and source hashes. It excludes credentials and owner memory.
Saved personality preferences are configuration and are included explicitly.
An older revision that cannot apply nonempty personality settings is refused;
evaluate customization separately on the candidate, keeping default preferences
for the historical baseline comparison. Runtime writes and databases are isolated.
The service holds the export logic; the router remains thin and authenticated.
Observation-only instrumentation may be deployed separately, without deploying
the behavioral candidate. A local template is explicitly unverified:

```powershell
python packages/igor/evals/behavior/run_eval.py --model MODEL --write-local-config PRIVATE_PATH/config-template.json
```

Do not label that template production-verified by guessing omitted settings.
Use the authenticated running snapshot. On the production host, run isolated
processes with normally configured credentials, separate storage/output paths
and clean original/repaired source trees. Do not copy keys into artifacts,
bypass authentication, substitute models or enable fallback to make a run pass.
The runner rejects differing allocations/API routes and missing supporting
configuration. It records actual provider-returned identities for generation,
embeddings, translation and supporting calls. Authentication failure, unknown
identity, substitution or incompatible vectors makes the comparison invalid.

```bash
python packages/igor/evals/behavior/compare_eval.py \
  --original /isolated/3e76e49/packages/igor \
  --repaired /isolated/41d932b/packages/igor \
  --candidate /isolated/candidate/packages/igor \
  --model VERIFIED_MODEL --config /private/behavior-config.json \
  --cases packages/igor/evals/behavior/memory_cases.json \
  --live --output-dir /private/comparison-memory
```

Use the same command with `cases.json` and representative private incident cases.
The driver defaults to three repetitions per scenario and alternates revision
order. Original/repaired sources must match `3e76e49`/`41d932b` unchanged. Use Git
checkouts or verified archive manifests. Candidate source and working changes
are hashed. Each subprocess imports only its selected application. The normal
configuration is applied identically; settings absent in an older engine are
not retrofitted into it.

Outputs checkpoint after turns and include responses, exact assembled requests,
ranked candidates/selected sources, tool receipts, SSE events, supporting wire
requests, source/configuration/fixture hashes, token usage, indexing state,
deadline misses and timing. Live runs retain the isolated SQLite store.
`comparison.json` indexes runs and `review.html` displays valid actual replies
side by side with links to full evidence. Reports never auto-accept a candidate.
Without `--live`, runs capture inputs and make no behavioral claims.

For individual instruction groups use the existing
`--section-override core/08_memory.md PRIVATE_SECTION.md` control: exactly one
section changes in memory, with both hashes retained. Evaluate groups separately
against repaired before reviewing their combination. Preserve authorization,
clinical and essential memory boundaries in comparison sections.

## Human review and rollout

For natural memory, voice, reasoning, initiative, preferences/boundaries and
executed-action awareness, record improved, unchanged, regressed or inconclusive
with passages from actual replies. Ordinary memory must appear in the first
response without a recall tool. Review casual, serious and technical voice;
catchphrases, keywords and response length do not prove personality.

Separate absent storage, unretrieved evidence, retrieved-but-unused evidence and
evidence overridden by conflicting instructions. Inspect latency, deadline
misses, misleading matches, token use and total request overhead. Explicit
language metadata is an expectation, not an extra instruction: distinguish
artifact language from surrounding prose and keep production language telemetry.

Acceptance requires visible gains in memory and voice plus another capability
against original, without recurring material regressions. Evaluate simplification
against repaired separately. Keep the candidate off production main until human
review passes. Deploy the evaluated revision only after acceptance, inspect fresh
production conversations and retain rollback to the last accepted revision.
Passing infrastructure tests does not establish behavioral recovery.
