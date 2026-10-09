# Natural memory and personality candidate — 2026-10-09

The candidate is based on repaired revision `41d932b`. On 2026-10-09, the owner
requested committing it and pushing it directly to `main` before live evaluation,
superseding the earlier instruction to keep it local. A backend push to `main`
triggers the repository's deployment workflow; a successful push alone does not
confirm deployment. Behavioral restoration is **not accepted**: no valid live
comparison has produced actual replies from original `3e76e49`, repaired
`41d932b` and this candidate.

## Reference applications and personalization

The official reference clones remain outside this repository:

| Reference | Examined SHA | Local directory |
|---|---|---|
| LibreChat | `e1dfc10449ff713faffacd60273fddcfe2c0a698` | `C:/Users/ahmet/.codex/behavior-restoration/reference-repos/LibreChat` |
| Open WebUI | `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d` | `C:/Users/ahmet/.codex/behavior-restoration/reference-repos/open-webui` |

LibreChat has [editable agent instructions](https://github.com/LibreChat-AI/LibreChat/blob/e1dfc10449ff713faffacd60273fddcfe2c0a698/client/src/components/SidePanel/Agents/Instructions.tsx#L64)
and [separate base and dynamic instruction composition](https://github.com/LibreChat-AI/LibreChat/blob/e1dfc10449ff713faffacd60273fddcfe2c0a698/packages/api/src/agents/context.ts#L101).
Its editor waits for the stored instructions before enabling changes. Open WebUI
[persists the model's custom system field](https://github.com/open-webui/open-webui/blob/8bd8b4fac5e059578ac0c74b3c18d11139f88b7d/src/lib/components/workspace/Models/ModelEditor.svelte#L426)
and [resolves it into the request](https://github.com/open-webui/open-webui/blob/8bd8b4fac5e059578ac0c74b3c18d11139f88b7d/backend/open_webui/utils/middleware.py#L3221).
These are concrete configuration-to-input paths. Their source does not establish
that either application is universally small or produces better conversations.
No upstream implementation, default persona or runtime dependency was copied.

A concrete SPEDA defect was found: both clients sent their instructions in
`system_prompt`, which the orchestrator then replaced with its assembled prompt.
The request now carries these instructions separately in `AgentContext`; the
orchestrator adds the profile's customization once during assembly.

Heartbreaker and SPEDA GO now have a per-agent editor with saved instructions
and four controls: tone, humor, directness and reply length. Normal authenticated
`GET`/`POST /agents/personalities` uses the existing runtime store. The profile
owns the selected style wording; the service validates and persists preferences;
the router stays thin. War Room shares its parent profile's settings.

Empty preferences add no customization text. Saved instructions take priority
over the controls; current-device instructions can refine them. Domain, clinical,
authorization, factual memory and executed-action boundaries remain applicable.
There are no scripted personality catchphrases. Saving requires a successful
server acknowledgement, persistence failures retain drafts, and resetting is an
explicit draft change followed by Save. The existing device-wide field is now
labelled to distinguish it from saved per-agent preferences.

## Automatic recall and instruction burden

Initial recall shares the existing hybrid conversation search with
`recall_conversations`. It supplies stored facts and raw past exchanges before
the first model call. The latest owner message is the main query; short follow-ups
can use bounded adjacent context or the most recently active owner conversation.
Assistant statements remain role-labelled historical evidence.

The selected defaults are a 1,000 ms relevance-search budget, at most three
windows, and 4,000 combined characters of automatic facts and excerpts. Both
limits are exposed through existing settings. Retrieval honors profile history
scope and deduplicates against visible messages, standing memory and recaps.
Excerpts retain message IDs, roles, dates and bounded execution receipts.

Facts and conversation search share one translation and query embedding per
request. Local evidence is collected first and retained on provider failure or
deadline expiry, with degraded recall recorded. A search miss cannot establish
that something was never said. Retrieval performs no memory admission.

New raw messages receive immediate local keyword indexing through the existing
indexer. Failed local indexing has an exact-message job in the existing durable
queue; it remains retryable after a message leaves the bounded embedding tail.
Embeddings and fact admission remain background work. Keyword search no longer
depends on embedding coverage. The local pass searches scoped IDs in SQL before
loading matched metadata; this avoids a full metadata scan on a short deadline.
App-owned vector caches refresh incrementally; matrix construction runs outside
the event loop. Cold refresh can finish for the next turn without poisoning the
chat transaction when initial recall times out. An initially empty cache also
picks up the first background embeddings without requiring a process restart.

Fixed profile instructions plus the skill manifest, rendered in English with
Europe/Istanbul and `openai:gpt-6-luna`, now measure:

| Profile | Characters, including manifest |
|---|---:|
| SPEDA | 31,075 |
| Atomix | 30,789 |

The manifest is 2,588 characters, already included in these totals. These are
character counts, not token counts or behavioral scores. Detailed procedures
moved into existing skill guides; identity, voice and essential boundaries stay
available. Canvas remains mandatory for substantive voice turns. Skill reading,
browser escalation and calendar reads are selective; duplicate source footers
and Atomix's blanket ban on casual rapport were removed. Tool search returns
loaded names and availability, with schemas supplied through the subsequent
tools array. Permissions, ordering and session persistence are preserved.

Instruction-group evaluation against `41d932b` remains pending. The runner's
single-section override records hashes for independent group trials. Combined
simplification has not been accepted on the basis of these counts or tests.

## Harness and local evidence

The runner uses each selected revision's real registry disclosure, SessionManager,
persisted replies, normal history loading and client/Canvas annotations. Actual
local guides and fixture-only memory operations run; unsupported external actions
remain incomplete. The authenticated read-only configuration snapshot allowlists
generation, retrieval, indexing, runtime and tool-catalog metadata, excluding
credentials and owner memory. Its export logic lives in a service.

Comparisons isolate databases, runtime state and output paths. They verify
generation, translation, embeddings and supporting model identities, reject
substitution/fallback/dimension mismatch, reuse verified fixture vectors, and
seed the older revisions' normal keyword projection alongside those vectors.
Source, configuration and fixture hashes accompany assembled requests, candidates,
selected evidence, responses, SSE events, receipts, usage and timing. Live runs
retain their isolated SQLite store. The comparison driver alternates revisions
and defaults to three repetitions; its HTML review never auto-accepts a result.

Local verification passed **1,034 Igor tests**, Heartbreaker type checking and
web build, SPEDA GO Kotlin compilation, and the Git whitespace check. Tests cover
scope, missing vectors, shared preparation, corrections, receipts, timeouts, cold
cache recovery, durable retries and authenticated personality persistence. The
desktop editor was also exercised against an authenticated isolated fixture
backend, including failure and retry. Mock replies in tests are not behavioral
evidence.

Private incident inputs were captured in all three revisions with three
alternating repetitions. The candidate delivered the previous conversation in
all three; both historical revisions omitted it in all three. Candidate local
recall took 27.5–33.3 ms in this small fixture while degraded by the absent provider
credential. This establishes delivery to the first request only. No model reply
or demonstrated use followed, and these are not production semantic latency
measurements.

The fully indexed volume fixture exposed an additional SQLite defect: an `IN`
scope filter made FTS perform a lookup for each eligible message ID. Using
`EXISTS` keeps full-text matches as the search driver while still applying owner
and history scope before the ranking limit. A file-backed diagnostic with 20,635
indexed background messages then delivered the expected evidence in **65.2 ms**
of automatic recall, with no deadline miss. It used keyword fallback, no fixture
vectors and no live generation. The stronger regression test includes indexed
background text and a provider timeout. Actual elapsed time determines deadline
miss telemetry even when synchronous provider work delays the timeout callback.
Production semantic and cold/warm vector performance remain unmeasured.

Private artifacts remain under `C:/Users/ahmet/.codex/behavior-restoration/`:
`natural-memory/verified-local-input-comparison/` contains the comparison index,
HTML review and nine full input captures; test/build logs and the editor preview
are alongside it. Export-derived fixtures, responses and traces are outside Git.
`natural-memory/volume-query-verified-local.json` and its provenance sidecar hold
the file-backed diagnostic; `local-candidate-review-summary.json` records the
inconclusive capability judgments and remaining access blockers.

## Behavioral verdict and remaining work

| Capability | Verdict against original and repaired |
|---|---|
| Natural memory use | Inconclusive: input delivery improved; actual conversational use is unmeasured. |
| Recognizable voice | Inconclusive: customization reaches inputs; actual voice is unmeasured. |
| Independent reasoning and context | Inconclusive: no valid live replies. |
| Appropriate initiative | Inconclusive: no valid live replies. |
| Preferences and boundaries | Inconclusive: persistence/boundary tests pass; actual use is unmeasured. |
| Previously executed actions | Inconclusive: receipts survive retrieval; actual awareness is unmeasured. |

No capability is classified as behaviorally improved, unchanged or regressed yet.
The authenticated production configuration snapshot request returned **404**.
No OpenAI provider credential is configured locally, and the available SSH key
was rejected by the production host. The backend API key does not authorize model
requests. These prevent a production-equivalent isolated live comparison.

An observation-only patch is prepared privately at
`natural-memory/behavior-config-observation.patch` and passes `git apply --check`
against the unchanged repaired source. It adds the read-only endpoint, catalog
export and settings declarations without the candidate's conversation, instruction,
tool-disclosure or personalization changes. It has not been deployed.

With normally authenticated host access, retrieve the allowlisted configuration
snapshot, then run isolated original/repaired/candidate conversations using
identical verified settings. The observation-only patch remains available if
the candidate has not deployed. Evaluate instruction groups separately against
repaired before accepting their combination. Review actual first replies,
multi-turn/cross-session memory, bilingual paraphrases, corrections, irrelevant
matches, missing vectors, outages and realistic cold/warm history volume. Record
ranking/coverage separately from use, and measure timing, deadline misses,
misleading matches, tokens and total request overhead.

Acceptance still requires visible gains in memory and voice plus another
capability against `3e76e49`, without recurring material regressions. The owner's
request to push before evaluation does not satisfy that criterion. Inspect fresh
production conversations after deployment and retain the previous production
revision `4434e4e` as a rollback point. Passing infrastructure checks does not
establish recovery.
