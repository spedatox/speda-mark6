# Conversation-first recovery investigation — 8 October 2026

The repair removes a reproduced reviewer context gap and an instruction that
made memory clarification interrupt the conversation repeatedly. Conversational
recovery is **not demonstrated**: no matching live provider credential or
complete verified production request configuration is available locally.
There are no new before/after model generations or behavioral pass rates.

## Confirmed defects and bounded changes

Incident 1959 contains five failed observation attempts and repeated questions
after the owner answered the specific scope question. Its earlier `nope` is
ambiguous; its later `YES I DONT` has an immediately preceding explicit question.
The previous context reader omitted that question. The pending repair now adds
the adjacent visible assistant turn as bounded referent-only context. Owner
quotes and hashes remain owner evidence; assistant questions cannot be resolved
as evidence or copied into an observation's sources. Isolation, adjacency,
private-metadata exclusion and automated-session exclusion remain tested.

The observation reviewer now explicitly distinguishes a contextual owner answer
from the assistant's testimony. It still decides whether the proposed scope is
supported. No deterministic yes/no acceptance, reviewer bypass, health-history
assumption or automatic acceptance of uncertain facts was introduced.

The interruption had a second, explicit instruction path:

1. Observation capture was encouraged during the conversation.
2. A reviewer hold returned `Ask the owner exactly: ...`.
3. Shared memory policy required that question in the next reply.
4. The next answer was reviewed without its preceding question, resulting in
   another hold. Even a numeric message citation did not resolve the incident.

The specific reviewer rationale is absent from the export. Thus the missing
context and forced-question instructions are established; attribution of every
hold to either defect is not established. The final incident response accurately
reported the failed save, but the original conversation had already been displaced.

The tool description, reviewer-hold result and shared memory policy now require
checking the exchange before clarifying. An answered question need not be
restated. A genuine material uncertainty can still be clarified once at an
appropriate point. A persistent hold must be reported accurately once while
continuing the actual conversation. New evidence, a concrete correction or an
explicit owner retry can reopen the write. An acknowledgment alone cannot.

A reviewer that requests confirmation without identifying a question now fails
closed instead of inventing a date question. Original owner messages and tool
audit results remain the existing recovery evidence. They are not a committed
observation or a new pending-memory queue. There is no new scheduler, recovery
framework or guarantee that a held proposal will later be stored automatically.

## Controlled prompt investigation

Measured unconditional prompt sections total 73,548 characters for SPEDA and
73,155 for Atomix after the targeted change, before the skills manifest and
request-specific context. Visual instructions contribute 15,486 characters and
shared memory policy 12,022. This repair increases the memory section slightly;
length reduction was not used as a success criterion.

| Group | Conflict or burden examined | Evidence and decision |
|---|---|---|
| Memory | Immediate clarification versus conversation first; automatic capture versus unresolved writes | Explicit forced-question path and incident trace support the targeted change. |
| Voice | Independent interpretation versus repeated interview questions and caution | Akın conversation repeatedly asks what happened next and hedges over motives despite concrete applicant treatment. Earlier voice changes remain; no new voice rewrite or causal claim. |
| Grounding/output | Useful judgment versus action receipts, generic status patterns and rigid lookup brevity | Baseline wording is stricter than the current grounding policy. Compare this group alone; no additional output-policy change without live evidence. |
| Calendar | Dates as operational triggers versus conversational boundaries; booking versus attendance | Dorm and Akın transcripts establish wrong interpretation/unnecessary operational checks. The earlier repair already addresses these inputs; no additional calendar change. |
| Visual output | Moderation versus mandatory presentation, unrelated catalog examples in ordinary chats | Large unconditional catalog; Canvas requirements are conditional on mode. Evaluation-only scope sentence preserves catalog and authorization. No production removal or reduction. |
| Identity | Shared dry humor versus Atomix's blanket restriction; supportive guidance versus `When in doubt, defer` | Actual Atomix replies show situational humor before the loop. No evidence that relaxing clinical boundaries would fix the defect; identity and medical boundaries preserved. |
| Tools/environment | Escalation ladders and engineering role instructions in unrelated conversations | Conditional rules are nevertheless always loaded. Possible attention burden, not proven behavioral causation; unchanged. |
| Patterns, signatures, Skyfall, agent network | Background maintenance and tool protocols in ordinary chat | Existing limits distinguish pattern priors from authority and restrict external actions. No demonstrated need to remove their protections; unchanged. |
| Language/budget | Prior language conflict; short-answer/ask-to-expand rule | Language was repaired earlier. Exported budget mode was off, so its rule is not an established cause of this incident. |

The existing harness now supports private cases, the existing Atomix profile,
and replacing exactly one prompt section inside an isolated evaluation. Across
five comparisons (voice, grounding/output, memory, calendar, visual scope), all
20 cases retained identical configuration, case bytes, messages, tools and
non-system parameters. Mechanical comparison verifies that only the selected
section changed. These are controlled **input comparisons**, not ablations
with measured live outcomes. Security, action authorization and safety rules
were not removed. No production setting was changed for evaluation.

## Evidence stages and baseline comparison

The same harness captured 20 cases against preserved `3e76e49`, prior `4434e4e`
and the repaired source. Seven added cases cover real incident 1959 answer and
frustration turns, the real Akın interpretation/correction exchange, a
reconstructed prior-contribution memory and a long preference dossier. Private
transcripts and raw inputs remain outside Git. Their source hashes, fixtures,
configuration hashes, application hashes and exact requests are retained in
the task's output evidence bundle.

| Stage | Observed result | Limit |
|---|---|---|
| Retrieval | Dorm mapping and omitted-middle preference absent in baseline, present in prior and repaired inputs | Improvements belong to the earlier repair; not new gains from this patch. Full production corpus not evaluated. |
| Execution continuity | Atomix dispatch, SSH creation, failed delivery and unknown completion receipts absent in baseline, present in prior and repaired inputs | Historical receipts do not establish present tool availability. |
| Reviewer interpretation input | Context-only reproduction of call 6168 adds the actual question with identical reviewer instructions | No live reviewer verdict. |
| Admission integrity | Replayed historical hold stores zero observations before, with context-only repair, and after the policy change | A replay is not a new reviewer decision. Mocked acceptance/rejection tests establish wiring, not model understanding. |
| Existing short memory fixtures | Short preference and contribution context already present in baseline | No retrieval improvement claimed for these fixtures. |
| Behavioral application | No completed matching live replies | Natural memory use, distinctive identity, judgment, initiative, adaptation and cognitive workload remain unmeasured. No behavioral improved/unchanged/regressed labels are justified. |

Actual historical responses are retained for human evaluation, separately from
the captures. No generated personality demonstration or invented after-response
is used. The real incident exports identify a stored conversational model, but
not the returned provider identity, effective historical reviewer configuration,
reasoning parameters or deployed revision.

The earlier read-only production snapshot records `openai:gpt-6-luna` for SPEDA
and Atomix and `openai:gpt-5-nano` for memory review. The reviewer code explicitly
requests low reasoning; a model-picker display of medium alone does not prove
the reviewer call used medium. Neither model allocation nor reasoning settings
were changed here. Local input captures use one identical, explicitly unverified
configuration. The snapshot omits exact production tools/API route, conversation
cache TTL and deployed SHA. Igor desktop authentication is not a provider key.
The harness refuses unverified live configurations and provider fallback.

## Verification and remaining work

Targeted tests cover contextual positive/negative answers, rejection, ambiguity,
confirmation holds, malformed questions, outages, source ownership and a single
section override that restores the loader after evaluation. The full Igor suite
and whitespace check are run before finalizing. The first full-suite attempt
loaded a different Forge checkout from the Python environment; the corrected
run explicitly selects this repository's `packages/forge`.

Completion still requires identical production-equivalent model calls on both
revisions, actual reviewer decisions on the incident's explicit and ambiguous
answers, and human evaluation of retained replies. Specifically verify that an
unsaved nonessential operation no longer crowds out the original answer, and
that retrieved preferences and prior experiences affect behavior silently.
Review credible-danger cases alongside ordinary-frustration boundaries. Audit
held-proposal recovery on the full owner corpus separately. No deployment or
production configuration change was performed by this work.
