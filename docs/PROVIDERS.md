# Provider and thinking controls

The renovated answering loop uses one policy resolver for interactive turns,
background capture and read-only workers. It adds no mandatory classifier,
planner or judge call. Default model choices remain in the SPEDA profile.
No real provider requests or production deployment were performed for this port.

## Kimi setup

Configure `KIMI_API_KEY` (or `MOONSHOT_API_KEY`) in your local `.env`. The default
connection is `https://api.moonshot.ai/v1`; `KIMI_BASE_URL` can select the owner's
intended regional/proxy connection. Provider selection never uses a subscription
login or switches providers by itself.

Set `LLM_MAIN_MODEL=kimi:kimi-k2.6`, or select Kimi in a chat request:

```json
{
  "message": "Help me reason through this.",
  "model": "kimi:kimi-k3",
  "thinking": {"mode": "effort", "value": "high"}
}
```

Kimi K3's declared efforts are `low`, `high` and `max`. It has no off switch.
K2.5/K2.6 use `enabled` or `off`; they do not expose a pretend four-level slider.
K2.7 Code is available at provider default with its always-on thinking behavior.
The adapter retains `reasoning_content` through tool calls and stored history;
displaying it is a separate preference. It does not set temperature.
See [Moonshot's thinking documentation](https://platform.kimi.ai/docs/guide/use-thinking-models)
and [K2.5's official examples](https://github.com/MoonshotAI/Kimi-K2.5).

## Available connections

| Route prefix | Credential | Protocol |
|---|---|---|
| bare Claude name or `anthropic:` | `ANTHROPIC_API_KEY` | native Anthropic SDK, retries disabled |
| `openai:` | `OPENAI_API_KEY` | Responses for every turn |
| `gemini:` | `GEMINI_API_KEY` | native Google generateContent SSE |
| `vertex:` | ADC or `VERTEX_CREDENTIALS_FILE`, plus project/location | native Google generateContent SSE |
| `kimi:` | `KIMI_API_KEY` or `MOONSHOT_API_KEY` | Chat Completions with thinking replay |
| `deepseek:` | `DEEPSEEK_API_KEY` | Chat Completions with thinking replay |
| `zai:` | `ZAI_API_KEY` | compatible Chat Completions |
| `nvidia:` | `NVIDIA_API_KEY` | compatible Chat Completions |
| `ollama:` | optional `OLLAMA_API_KEY` | compatible local `/v1` connection |

The latter two expose provider default unless the owner adds a verified model
descriptor. Unknown models do not get guessed thinking or vision capabilities.
Unknown provider prefixes fail before any request. Native model declarations
live in `app/profiles/speda.py`, with owner extensions in the policy document.
Google AI Studio and Vertex retain separate credentials and billing routes.

The deployed GLM-5.3, Flash and FlashX routes have native `low`, `high` and `max`
efforts with thinking enabled. They cannot disable thinking. The offline legacy
policy importer explicitly reports the old `none`/`minimal` → `low`, `medium` →
`high` and `xhigh` → `max` compatibility mappings rather than calling them off.
See [Z.ai's thinking contract](https://docs.z.ai/guides/capabilities/thinking).

OpenAI Responses preserves structured output and encrypted continuation items
with `store:false`. See [OpenAI's reasoning guide](https://developers.openai.com/api/docs/guides/reasoning).
Daybreak is an explicit, per-model owner policy in `cyber_programs`. Set an
approved OpenAI Responses model as `main_model` (or choose it for a turn), then
save the matching program through `PUT /model-policy` with its current revision:

```json
{
  "expected_revision": 0,
  "policy": {
    "main_model": "openai:gpt-6-sol",
    "cyber_programs": {"openai:gpt-6-sol": "daybreak_blue"}
  }
}
```

Read `GET /model-policy` first and preserve its other fields when saving the
complete document. `gpt-6-sol`, `gpt-6.1-sol`, and `gpt-6-astra` use
`daybreak_blue`; `gpt-5.6-cyber` and `gpt-6-cyber` use `daybreak_red`.
The Blue-value `gpt-6.1-sol` and `gpt-6-astra` models still require Daybreak
Red organization and project access.
`gpt-5.5-cyber-preview` is existing-access only and retires February 1, 2027.
For eligible Blue models, `standard` explicitly selects standard safeguards.
An absent mapping omits `access_programs` and leaves the API default in force.
Invalid model/program pairs are rejected before saving. The selected program and
response model, when returned by OpenAI, appear in the private provider receipt.
Daybreak requires organization and project approval plus an API key from that
project; this setting does not grant access. GPT-6-Cyber additionally needs model
approval and an IP allowlist. Its requests include a stable pseudonymous safety
identifier derived from the owner ID. See [OpenAI Docs: Daybreak](https://developers.openai.com/api/docs/guides/daybreak).
Anthropic signatures and Google thought-signature parts are preserved in their
original protocol shape. DeepSeek no longer forces reasoning off just because
tools are present; its adapter replays the original reasoning field. See
[DeepSeek's tool-thinking contract](https://api-docs.deepseek.com/guides/thinking_mode/).

## Acknowledged settings

All endpoints below require `X-API-Key`. The renovated backend has no desktop
or Android settings UI port yet; original `final` clients remain unchanged.
These endpoints and request fields are the implemented control surface.

- `GET /models`: live chat model inventory from configured providers, with native
  thinking controls and vision flags from versioned descriptors. Listings use
  each configured connection, run concurrently with bounded timeouts and cache
  for 60 seconds. Failed/empty listings fall back to that provider's declared
  models; an unavailable Ollama daemon is omitted. New model IDs appear without
  a code change, at provider default until their capabilities are declared.
- `GET /model-policy/catalog`: declared capabilities, without provider requests.
- `GET /model-policy`: current owner document and revision.
- `PUT /model-policy`: complete updated document plus `expected_revision`.
  Returns the acknowledged document and new revision; stale writes return 409.
  Invalid native settings return 422. Treat pending/failed saves as unconfirmed.

Example after reading revision 0:

```json
{
  "expected_revision": 0,
  "policy": {
    "main_model": "kimi:kimi-k3",
    "models": {"kimi:kimi-k3": {"mode":"effort","value":"low"}},
    "roles": {"worker": {"mode":"inherit"}},
    "show_thinking": false,
    "fallbacks": []
  }
}
```

Native modes are `provider_default` (omit the optional parameter), `off`,
`enabled`, `effort`, `budget`, and `inherit` for a child/parent context.
Unsupported modes/values are rejected, never silently translated to a different
effort. Numeric budgets must fit the model's range and leave answer room below
the request's output allowance. The model catalog drives which modes are valid.

Priority: explicit turn policy, agent policy, role policy, model policy, provider
default. Explicit model selection wins routing; then background/agent pins,
owner main model, deployment configuration and profile defaults apply. Without
an explicitly different background route, a routed non-Anthropic turn's capture
uses the same provider. Settings changes do not modify an admitted turn.

SSE emits `policy` receipts containing the actual model/protocol, requested and
applied controls, source/revision, outcome, attempts and raw provider usage.
Completed assistant messages retain that receipt with their private continuation
envelope. `show_thinking` controls only separate `thinking` events; it never
changes the wire effort or discards required continuation state.

## Failure and execution behavior

One deadline covers a turn's model calls, configured fallback attempts and
durable tool executions. Connect timeout is 10 seconds; the default whole-request
deadline is 90 seconds and configurable. This is a failure ceiling, not a speed
claim. SDK retries are disabled; explicit fallback routes are tried only for
connection errors, 429 or server errors while opening a request. There is no
automatic fallback after opening a stream or after a tool exchange. A fallback
must support the requested native policy; incompatible settings are rejected.

EOF is not success. Failed, refused, cancelled, interrupted and exhausted
responses never become successful empty answers. Only complete, schema-valid,
currently authorized tools can execute. A durable receipt is admitted before
side effects; a completed repeated call returns its existing result. An orphaned
started receipt is an unknown outcome requiring inspection. External side
effects are not claimed to be transactional or exactly-once.

In-process Task workers inherit the actual parent model, policy snapshot and
deadline unless an explicit worker policy is provided. They see only read-only
skills and cannot start nested workers. This is a bounded worker implementation,
not a port of final's full Legion/Forge execution system.

## Offline migration

`scripts/import_model_policy.py SOURCE OUTPUT REPORT` stages legacy
`agent_models` and `model_thinking` from an offline runtime-state JSON. Both
output paths must be new. The report retains a source hash and per-setting
disposition. Known legacy mappings are explicit; unsupported settings remain
in the original and are listed for review. No database or live settings are
activated. Review the staged document before saving it through the API with the
current revision. Other legacy configuration stays in the original source.

## Validation boundary

Tests exercise real database policy writes, owner scope, revision conflicts,
stream framing, the Anthropic SDK with a mocked HTTP transport, native Google
parts, Responses state, Kimi tool cycles, workers and execution receipts.
Provider HTTP responses are fixtures. Working credentials, account/model access,
real vision recognition, answer quality and end-to-end latency remain live
evaluation work; structural tests do not establish them.
