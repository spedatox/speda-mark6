# Initiative and memory reliability

## Maintenance recovery

Read the authenticated `GET /iacon/maintenance` endpoint before changing maintenance configuration. Its health object shows the current maintenance route, policy revisions, admission status, oldest pending interpretation and last completed interpretation. `missing_rate`, `provider_unconfigured`, `paused` and `daily_limit` distinguish common pre-dispatch blocks.

To keep Orion's chat model while choosing an independently priced maintenance model, send the current policy and `expected_version` to `PUT /iacon/maintenance`, with `policy.model` set to the intended configured model reference. Preserve the daily limits and specify a conservative rate for that model under `policy.prices`. Setting `model` to null follows the existing background model resolution. The model override is stored separately with the policy revision, so older versions can continue to read the base policy. Existing scheduled drains can pick up pending work. Held provider calls and invalid evidence remain subject to the existing review and reconciliation paths.

## Foreground behavior

For an eligible owner turn, Mark VI can read a small relevant sample of earlier visible messages in parallel with Iacon. The reads share the elapsed-time and context-size caps. The archive path uses the original run input as a fence, excludes hidden messages and private consultations, and separates human replies from automation triggers. Recent excerpts stay out of persisted system prompts and shared peer context. A deliberate archive query can request ranked episode evidence. The original literal search retains dates and cursors. If a broad archive query fails, the same lookup can spend its remaining allowance on the independent recent window.

Mark VI offers a few relevant authorized read tools before its first provider call. Follow-ups have the original room sequence and run ID for earlier unfinished work. An action suggestion has no authority to mutate data. Known adapter outcomes are stored on the existing execution receipt. Calendar creation treats an accepted provider ID as an accepted effect and holds an uncertain receipt. Replanning with a different model call ID reuses the same calendar-create receipt within the owner run when the arguments match. The run inspection API includes the observed effects, gaps and a state distinct from transport completion. Desktop and Android render an uncertain run as one durable reconciliation notice, including on history reload.

## Mail-watch pilot

Opt into `attention_only` on an existing read-only native mail watch. Optionally set `attention_quiet` to two local integer hours between 0 and 23. The default is 22:00–09:00 and equal hours mean no quiet window. Set `attention_snoozed_until` to an ISO timestamp with a timezone offset for a one-time snooze. The model decides whether new mail creates a timely consequence. The server checks the admitted watch revision, current activation, quiet hours, snooze and age both when committing a notice and when claiming it for delivery. Dispositions are available with the run, and a suppressed disposition has no delivery. The existing n8n callback and receipt identities handle duplicate admissions.

Set `attention_shadow` to true to record `would_notify` whenever a candidate passes the checks. This mode leaves delivery empty. Broader cross-watch suppression, delayed digest scheduling, explicit cross-turn obligation graphs and provider-backed behavior benchmarks remain release work. Measure recall and unnecessary interruptions using held-out data before expanding deployment.

## Proactive Cortex

Owner chat now assembles bounded, attributed excerpts from related sessions before the first response. A short opening can use fresh dialogue without matching keywords. Local-time references to the previous evening narrow the archive window. A missing recent match can use the existing source index to find older sessions. Visibility, agent, project and admission boundaries apply before prompt hydration. Step-local source spans support recovery. The default path uses no embedding service or additional model call.

The personal assistant owns routine scheduling decisions within the owner's delegation. Interactive Google Calendar creation requires a supporting quote from owner dialogue or a read result. The runtime validates quote presence and strips that field before provider dispatch. The model assesses what the quote actually establishes. Scheduled personal briefings can connect upcoming commitments to consequences and surface one unresolved question.

Read-only attention watches support a source-bound question or a deferred digest item. The same agent and connected account deduplicate identical source sets. Eligible questions appear in subsequent private chats with that agent and can be answered or dismissed in ordinary conversation. Deferred items join a subsequent scheduled briefing within two days. Shadow mode records the proposed disposition. A saved watch revision, time window, snooze, and delivery eligibility control interventions. The implementation uses the existing chat and notification transports.

Evaluate first-call retrieval, unsupported inferences, unnecessary questions and interruptions separately. Semantic episode embeddings remain an experimental extension gated on held-out improvement. A production canary or a scripted provider response establishes neither an aggregate recall rate nor owner satisfaction.
