# Conversation source comparison — 2026-10-09

The owner requested examination of Open WebUI and LibreChat while repairing
SPEDA's conversational continuity and personality. Both official repositories
were cloned outside this repository and inspected without installing dependencies
or running their applications. The findings below concern source behavior;
neither application's conversational quality was measured in a live comparison.

| Reference | Examined revision | Local checkout |
|---|---|---|
| [Open WebUI](https://github.com/open-webui/open-webui/tree/8bd8b4fac5e059578ac0c74b3c18d11139f88b7d) | `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d` | `C:/Users/ahmet/.codex/behavior-restoration/reference-repos/open-webui` |
| [LibreChat](https://github.com/LibreChat-AI/LibreChat/tree/e1dfc10449ff713faffacd60273fddcfe2c0a698) | `e1dfc10449ff713faffacd60273fddcfe2c0a698` | `C:/Users/ahmet/.codex/behavior-restoration/reference-repos/LibreChat` |

No upstream implementation or persona text was copied into SPEDA. These are
reference checkouts, not additional runtime dependencies.

## Findings that apply to the reported failures

| Area | Observed source behavior | Implication for SPEDA |
|---|---|---|
| Executed-action awareness | Open WebUI loads structured assistant output from the database, reconstructs tool calls/results and removes orphaned pairs. LibreChat reconstructs assistant calls with corresponding `ToolMessage` results. | Past actions need observable execution evidence in subsequent model inputs. SPEDA's existing bounded receipt repair addresses the same requirement without replaying private reasoning or changing its internal content format. |
| Optional tool arguments | Open WebUI's Chat Completions-to-Responses converter sets `strict` to the original value, defaulting to false. | Corroborates the local Responses conversion fix. Optional search filters must remain optional. This does not establish that strict normalization caused every observed repeated search. |
| Memory in context | Open WebUI uses recent user turns for retrieval, deduplicates memory IDs and bounds user/context blocks separately. LibreChat adds scoped formatted memory to the dynamic instruction tail and checks capability/permission gates. | Natural memory use depends on correct scoped input delivery. SPEDA already has standing/relevant recall; importing a new memory framework would duplicate existing machinery. |
| Skills | Open WebUI supplies a skill catalog and loads bodies for selected/mentioned skills. LibreChat has a bounded catalog and explicit skill priming. | SPEDA already exposes metadata plus `read_skill`. Its large unconditional core sections deserve controlled evaluation rather than replacing the skill system. |
| Instructions | LibreChat composes configured agent instructions and relevant MCP instructions separately from shared runtime context. Open WebUI applies model/folder instructions and selected feature context. | These applications are configurable; source inspection cannot establish their actual prompts are always smaller or that their replies are more natural. It does show that an application need not impose SPEDA's entire fixed operational manual on every interaction. |
| Memory writes | LibreChat's fallback extraction instructions restrict writes to explicit memory requests. | This default conflicts with the owner's desired proactive assistant. Copying it would reinforce the reported need to supervise memory saves. Preserve SPEDA's evidence-bound owner-memory policy instead. |

Pinned source anchors:

- Open WebUI [database replay and tool-pair handling](https://github.com/open-webui/open-webui/blob/8bd8b4fac5e059578ac0c74b3c18d11139f88b7d/backend/open_webui/utils/middleware.py#L2221), [memory assembly](https://github.com/open-webui/open-webui/blob/8bd8b4fac5e059578ac0c74b3c18d11139f88b7d/backend/open_webui/utils/memory.py#L290), [optional-tool conversion](https://github.com/open-webui/open-webui/blob/8bd8b4fac5e059578ac0c74b3c18d11139f88b7d/backend/open_webui/routers/openai.py#L1405), and [feature-scoped builtins](https://github.com/open-webui/open-webui/blob/8bd8b4fac5e059578ac0c74b3c18d11139f88b7d/backend/open_webui/utils/tools.py#L520).
- LibreChat [tool-result reconstruction](https://github.com/LibreChat-AI/LibreChat/blob/e1dfc10449ff713faffacd60273fddcfe2c0a698/api/app/clients/prompts/formatMessages.js#L242), [instruction composition](https://github.com/LibreChat-AI/LibreChat/blob/e1dfc10449ff713faffacd60273fddcfe2c0a698/packages/api/src/agents/context.ts#L101), [memory context and default extraction policy](https://github.com/LibreChat-AI/LibreChat/blob/e1dfc10449ff713faffacd60273fddcfe2c0a698/packages/api/src/agents/memory.ts#L76), and [skill catalog limits](https://github.com/LibreChat-AI/LibreChat/blob/e1dfc10449ff713faffacd60273fddcfe2c0a698/packages/api/src/agents/skills.ts#L98).

## Concrete continuity defect found during the comparison

SPEDA's durable queue invokes handlers with five arguments:
`session_id, request_id, user_id, model, payload`. Three registered services
accept four: `update_session_log`, `update_session_recap` and
`run_daily_maintenance`. They were registered directly, so invocation raised
`TypeError` before any service work or model request. The same mismatch exists
at remote-main revision `4434e4e` and local revision `8b4fb67`.

This is a deterministic defect in the carryover path. The owner's exported
conversations lacked recaps, which is consistent with this failure. The exports
contain neither the deployed SHA nor queue errors, so they cannot prove this
specific defect caused those exact production conversations.

The repair adapts these three registrations using the queue's existing wrapper
pattern. It also makes recap generation report provider failures, empty results
and incomplete responses to the existing retry mechanism instead of marking
them done or advancing the history watermark. No new queue, scheduler, retrieval
layer, model pin, thinking setting, authentication path or personality prompt was
introduced. The disabled narrative-composition switch remains disabled.

Seven new regression cases failed before the repair. The corrected focused set
passed all 28 cases, including actual queue dispatch, persisted recap delivery to
the next session, exclusion from its own session and failure recovery. Model
responses in these tests are explicitly mocked; these are delivery tests, not
actual conversations or evidence that personality recovered.

The full Igor suite also passed: **1,000 tests**, with 475 existing datetime
deprecation warnings. `git diff --check` passed. These checks cover the local
candidate, including the previously prepared citable-owner-message and optional
Responses-tool-argument corrections.
Byte compilation and application import passed with an isolated bytecode cache
and the declared `feedparser` dependency installed in a temporary local directory.

## Instruction burden and behavioral limits

With the inspected local profile sections rendered for English,
Europe/Istanbul and `openai:gpt-6-luna`, SPEDA's joined core sections contain
73,550 characters and Atomix's contain 73,157. Both append a 2,096-character
skill manifest before memory, tool schemas and other request context. Shared
visual rules alone contain 15,486 characters and shared memory instructions
12,022. These are character counts, not token counts or measured causal effects.

No speculative prompt pruning was applied. The existing harness can compare
one instruction group at a time while preserving all other inputs. A valid
`3e76e49` versus repaired live comparison still requires the normal OpenAI
credential plus verified production request settings and actual tool definitions.
The locally configured OpenAI credential was absent during this review; the
desktop API credential only authorizes the backend and is not a provider key.

Behavioral status remains unproven: natural voice, independent interpretation,
initiative and preference use require actual responses, not input presence or
passing tests. The original exports and private side-by-side input evidence stay
outside Git under `C:/Users/ahmet/.codex/behavior-restoration/`.
