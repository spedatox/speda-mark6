The cases cover natural memory use, conversational voice, interpretation,
initiative, preferences and awareness of earlier actions. They use synthetic
fixtures in an isolated SQLite store. No production memory or external account
is changed. Context presence is pipeline evidence, never a personality score.

The harness runs SessionManager and AgentOrchestrator from the selected Igor
package directory. Live replies use the orchestrator's actual streaming call,
including its model, output limit, thinking settings, cache key and tool
definitions. It no longer replaces that call with a 1024-token, low-effort
non-streaming generation. The live client refuses fallback and model changes.
Provider-returned model names are recorded; missing/different names make the
case incomplete pending verification, rather than silently treating it as the
same model. Authentication failures remain failures.

Start with a non-secret configuration template:

```powershell
$env:PYTHONPATH = "$PWD\packages\igor;$PWD\packages\forge"
python packages/igor/evals/behavior/run_eval.py --model openai:gpt-6-luna --write-local-config eval-config.json
```

The template captures LOCAL settings and is explicitly unverified. Verify its
values against the running production instance before setting
`production_verified` to true, and record the source/date in `source`. Include
the per-agent model pins, per-model thinking levels, language, output ceiling
and runtime flags. Do not guess omitted settings from chat text. Credentials
must be configured through the application's normal environment/managed config;
never put credentials in this JSON or commit the local configuration artifact.

For live runs, `api_by_agent` must identify the actual production endpoint:
`responses`, `chat_completions` or `anthropic_messages`. Supply the actual
model-boundary tool definitions in `tool_definitions_by_agent` and the registry
catalog in `tool_index_by_agent` when present. This matters: this backend routes
tool-enabled GPT-6 requests to Responses, but tool-free calls to Chat Completions.
An empty tool list cannot be called equivalent to a production tool-enabled
request. A mismatched/missing endpoint stops the call before generation.

These tools are definitions only. If the model requests execution, the harness
saves the request and partial response, then marks the case incomplete without
inventing a result or operating a real account. This evaluates conversational
use of recorded outcomes; it does not establish current SSH access or live tool
execution. No post-turn extraction or background scheduling is exercised.

Use `--cases PRIVATE_CASES.json` for representative incident excerpts outside
Git. The same schema as `cases.json` applies; retained historical tool results
go in each assistant turn's `tools` array. Atomix is available alongside the
other evaluation profiles. Keep identical fixture bytes for both revisions.
Actual incident excerpts and storage fixtures must be labelled separately:
an isolated, reconstructed memory fixture is not proof of production storage.

For a controlled instruction comparison, use
`--section-override core/08_memory.md PATH_TO_COMPARISON_SECTION.md`.
Exactly one existing prompt section is replaced in the evaluation process;
all other groups, memory, model settings and tool definitions remain unchanged.
The report retains the original and replacement hashes. Use the same case,
configuration and application tree for both calls. This option does not alter
production configuration or source files. Preserve security and authorization
requirements in the comparison section. Input capture establishes the changed
instructions; only completed live replies can establish behavioral effects.

Use this same harness, cases and configuration against both application trees:

```powershell
# BASELINE_IGOR points to packages/igor in a clean checkout of 3e76e49.
python packages/igor/evals/behavior/run_eval.py --app-root BASELINE_IGOR --model openai:gpt-6-luna --config eval-config.json --live --output before-live.json
python packages/igor/evals/behavior/run_eval.py --model openai:gpt-6-luna --config eval-config.json --live --output after-live.json
```

`--app-root` imports the historical application without changing the harness.
Both runs retain exact requests, responses, SSE traces, normalized provider
content, available wire parameters, returned model identities and usage. Source
file hashes, revision/diff hashes, harness/case hashes, configuration hashes and
complete fixtures make the context reviewable. Reports checkpoint each case so
a later failure does not lose earlier conversations. Omitting `--live` captures
inputs without making conversational model calls or claiming behavioral success.

Explicit language requests are case metadata, not extra model instructions.
`turkish_draft` expects a Turkish artifact even when the configured default is
English. Reports distinguish the configured default, requested language and
artifact/reply scope. Lexical hints are checked against that expectation and
are not pass/fail scores. For an artifact, review its language separately from
surrounding prose; keep the production default-language telemetry in the raw
trace so discrepancies remain visible.

Review the completed replies side by side against each rubric, preferably with
revision labels hidden. Record improved, unchanged, regressed or inconclusive
for each of the six capabilities, citing actual passages. Check serious, casual
and technical voice together; catchphrases, keywords and length do not prove
personality. Respect for ordinary-conversation preferences must coexist with an
appropriate response to explicit danger.

For memory failures, distinguish absent storage, stored but unretrieved evidence,
retrieved but unused evidence, and incorporated evidence overridden by a
conflicting instruction. `fixture`, `stored_fixture_paths`, `context_checks`
and exact model inputs expose these stages. Do not describe absent baseline
evidence as a reasoning failure or an available fact as proof the reply used it.

The initial 13-case input baseline was captured at `3e76e49` before application
repairs. Local artifacts are under
`C:\Users\ahmet\.codex\behavior-restoration\`; they contain no live comparison
yet. The supplied real incident exports remain in Downloads and are not copied
into the repository or sent to a model by this harness. Production conversations
can supply additional human evidence, but sequential production tests alone do
not establish a controlled same-configuration baseline.
