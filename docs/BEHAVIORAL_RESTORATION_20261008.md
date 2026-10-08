SPEDA's identity is still present. The confirmed problems are contradictory
behavior policies, lost execution evidence between turns, and selective-memory
gaps. The repairs below improve the inputs and instructions that reach the
model. Actual conversational improvement still needs a live comparison: the
available provider credential was rejected, and the incident model's OpenAI
credential is not configured locally.

The pre-change revision is `3e76e49`. The working tree was clean. All seven
`chat-*-debug-*.json` exports in the owner's Downloads were inspected; these
contain stored messages, tool disclosures and full audit results, but not the
historical final system prompt or reasoning settings. Every supplied session
reports `openai:gpt-6-luna` and has no compaction summary. They establish the
failures and executed actions, not which exact production instructions caused
every response.

Evidence and priorities:

| Priority | Finding | Evidence and interpretation |
|---|---|---|
| 1 | Execution evidence disappeared on the next turn | TurnRegistry persisted tool previews in `_speda_meta`; SessionManager stripped every such block. The next turn retained the assistant's claim but lost the execution supporting it. This is a confirmed runtime defect. |
| 1 | Explicit language requests were forbidden | `15_language.md` said nothing inside a turn could override the configured language. Session 1957, messages 24824–24825, requests Turkish and receives “I can't write Turkish in this chat.” The prompt conflict is explicit; historical configuration is not exported. |
| 2 | Grounding and brevity policies competed with judgment | The voice capped warmth at one line. Output policy required a receipt for every clause and restricted depth to explicit requests, while separately allowing inference. Session 1944, messages 24708–24714, repeatedly hedges about motives instead of interpreting the stated applicant treatment. These conflicts are a plausible contributor, not a measured causal attribution. |
| 2 | Known place facts could miss document recall | Document selection capped body overlap below its admission threshold unless the title/path matched. Coordinates in client context did not anchor a place document. The controlled baseline reproduces stored-but-not-retrieved dorm knowledge; session 1882, messages 24210–24214, establishes the real wrong inference and correction, but does not expose the original recall input. |
| 2 | Truncated preferences were treated as complete | Standing dossier excerpts were capped at 250–350 characters; unmonthly standing dossier paths were then excluded from per-turn document recall, and instructions prohibited rereading shown files. A regression test places the relevant preference in an omitted middle section and reproduces the gap. The specific historical self-harm preference's storage and injection cannot be verified from these exports. |
| 3 | Calendar policy triggered unnecessary operational work | It demanded reads whenever any time was mentioned, treated time statements as write instructions, and described the calendar as actual whereabouts. Session 1944's Tuesday boundary leads to repeated checks; a calendar booking also cannot establish dorm-versus-class attendance. |

The Atomix and SSH evidence changes the diagnosis in the brief. Session 1947
contains **two actual `dispatch_agent` calls to Atomix**, with returned guidance.
Message 24747 then denies the call. Session 1954's request
`d39b2b9f-7eda-4ea1-93fd-83911654d625` contains a successful `ssh-keygen` execution;
its public key matches the first install command in message 24783. The later
claim that this first key was invented contradicts the audit. Subsequent
fingerprint discrepancies and present sandbox availability are separate facts;
successful creation does not prove continued access. No keys or connection
details are reproduced here.

What changed:

- SessionManager now converts saved tool metadata into ordinary Anthropic text
  blocks containing bounded, labelled execution evidence. It excludes thinking
  and arbitrary input commands. Missing results stay unknown. The whole loaded
  history gets at most 2400 additional characters, newest tool turns first; no
  new database query, schema, tool or persistence surface was added.
- Compaction receives the same outcome evidence and explicitly preserves
  conversational boundaries, corrections and relationship context. This is
  preventive: compaction was not active in the supplied incident sessions.
- Place retrieval recognizes exact numeric coordinate pairs, including pairs
  disclosed by structured tools. It supplies candidates, not a deterministic
  attendance decision. No radius, geofence, location framework or dorm-specific
  behavior was added. Different or nearby coordinates still require judgment
  and deliberate lookup.
- Dossier documents can be retrieved selectively even when a standing excerpt
  exists. Selected documents prefer matching sections within the existing
  2800-character document budget. Warnings about authority/conflicts remain
  attached. Partial excerpts may be opened; complete records need not be reread.
- Recall strips the weekday-bearing timestamp SessionManager actually emits,
  while preserving useful client location context.
- The shared voice prompt is shorter and describes conversational character
  instead of phrase specimens or warmth quotas. Individual profile identities
  remain authoritative. Grounding distinguishes specific claims from general
  explanation and interpretation, and accepts historical execution receipts.
- Explicit requested languages take precedence for the requested reply or
  artifact without changing the configured default. Calendar checks depend on
  actual scheduling needs, and calendar plans are distinguished from attendance.

The standing-memory caps, observation search limits, output-token settings,
model pins, permissions, evidence-bound write transaction, authentication,
tool registry and n8n scheduling are preserved. No new agent, scheduler,
orchestration layer, memory framework or personality imitation was introduced.
The captured SPEDA system context is smaller after the prompt edits.

Verification and limits:

The initial repair passed all 974 Igor tests, including seven new continuity
and pipeline regression tests. Compilation and the normal Git whitespace check
passed. The initial test
run hit the machine's inaccessible default pytest temporary directory; using
a fresh, explicitly named local test directory resolved that environment issue.
The suite emits existing datetime deprecation warnings on Python 3.14.

The 13-case harness in `packages/igor/evals/behavior/` captures the actual
orchestrator model-call boundary in an isolated store. Before/after snapshots
are saved locally under `C:\Users\ahmet\.codex\behavior-restoration\`.
The known dorm mapping, Atomix receipt, SSH creation receipt, failed delivery
and unknown completion were absent in the baseline and present after repair.
The fresh-session preference and implied contribution context were already
present in their short baseline fixtures; their presence is not reported as
a new improvement. The separate long-dossier test covers omitted preferences.

These are **input-level improvements**, not observations of restored voice.
The harness can also save live replies under identical model/settings and
supports human review using contextual rubrics rather than style keywords.
The local DeepSeek credential returned authentication failure on its models
endpoint; no valid local OpenAI credential was available for the exported
incident model. No live before/after outputs or behavioral pass rate is claimed.

Still requiring investigation: exact production preference storage and
extraction, semantic relevance on the owner's full corpus, cancelled-turn
recovery, actual sandbox key persistence, and whether the model incorporates
the repaired evidence under the owner's chosen reasoning settings. Bound
receipts can omit older results, and their previews do not replace full audit
records. Language leak detection still observes the configured default; a
requested foreign-language chat artifact can be flagged by that lexical check.
Voice/push language repair is a separate path and needs a live language test.
The static language prompt fix does not claim that those paths were evaluated.

Follow-up evaluation preparation:

The harness now uses the actual streaming orchestrator request rather than
overriding it with low reasoning and 1024 output tokens. It requires a verified,
non-secret production configuration for live runs, checks profile allocation
and the provider API route, and refuses fallback. This also exposes the earlier
empty-tool limitation: GPT-6 tool-enabled requests use Responses, so tool-free
Chat Completions generations are not production-equivalent. Actual production
tool definitions/catalogs can be supplied for comparison, while any requested
execution stops the synthetic case as incomplete. Provider model identity,
responses, SSE traces, usage, exact requests, source/configuration hashes and
fixtures are saved. Explicit language expectations distinguish requested
artifacts from the default-language detector and do not inject extra prompts.

No further application architecture or prompt changes were made in this stage.
The final suite passes all 976 Igor tests, including the added checks for live
request equivalence, fallback refusal and requested-language evaluation. The
13-case input capture also completes with the final harness. Neither result
is a live behavioral score.
Valid credentials and verified production settings are still missing locally;
no live behavioral improvement, unchanged capability or regression is claimed.
The owner has now requested pushing the repairs for production testing.
Behavioral restoration remains unverified until actual conversations are
reviewed. No direct Contabo deployment was performed by this task.
