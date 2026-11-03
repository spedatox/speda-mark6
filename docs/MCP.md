# Mark VI MCP integration

The capability registry uses the official Python SDK **2.3.0**, pinned in
`uv.lock`. The local wire test negotiated **2026-07-28** over Streamable HTTP;
the stdio test launches a real child process. See the
[specification](https://modelcontextprotocol.io/specification/2026-07-28) and
[official SDK](https://github.com/modelcontextprotocol/python-sdk/releases).

## Implemented

- Stdio and Streamable HTTP, bounded connection/request deadlines and concurrency.
- Progressive tool discovery, schema fingerprints and collision-resistant names.
- Explicit agent/tool grants; anonymous workers also need explicit read-only grants.
  A server's annotation never grants permission.
- Tools, structured results, resources and prompts; bounded output with truncation
  disclosed. Returned content is evidence/data, not instruction authority.
- A dedicated task owns each SDK connection and closes it in the same lifetime.
- Durable pre-effect receipts. Cancellation/connection loss leaves an unknown
  outcome for inspection; no automatic retry of a tool effect.
- Low-level session calls deliberately avoid the high-level SDK's automatic
  header-mismatch retry and input-required continuation. No model sampling or
  implicit filesystem-root callbacks are installed.
- Credential-bearing HTTP connections require TLS outside loopback; stdio receives
  only essential OS variables and explicitly configured credentials.
- Authenticated diagnostics and explicit catalogue refresh at `/mcp/servers`.

## Operator configuration

`MCP_ENABLED` preserves the deployment's built-in connection allowlist.
`MCP_CONNECTIONS_FILE` optionally names a bounded JSON array of additional
connections. Commands and endpoints are configured by the operator, not a model.
For example:

```json
[
  {
    "name": "research",
    "transport": "http",
    "url": "https://research.example/mcp",
    "allowed_agents": ["speda", "friday"],
    "allowed_tools": ["search"],
    "read_only_tools": ["search"],
    "headers_from": {"Authorization": "RESEARCH_MCP_AUTHORIZATION"},
    "timeout": 15,
    "concurrency": 2
  }
]
```

The environment variable holds the entire required header value. Configuration
contains references to secrets, rather than the secrets themselves. Pin stdio
package versions or image digests in production. Tool visibility is pinned for a
run; revoking a current grant still blocks subsequent execution.

## Release limits

`tests/test_mcp_runtime.py` verifies actual local HTTP/stdio negotiation, tool and
data access, schema changes, environment isolation, cancellation and no replay.
These tests do not certify remote vendor availability or OAuth grants. The old
Google/Microsoft REST dispatch now shares registry grants, schema pins, output
bounds and effect receipts. Thirty tool definitions are schema-checked; local tests
cover token refresh/rotation/revocation, owner grants and uncertain writes. Their
token caches belong to app instances. Native OAuth/connection management and
custom-server editing now implement the original client routes. Full per-operation
live vendor validation remains a release gate.
The existing isolated Playwright sidecar is retained;
production container/network isolation and version pins still need deployment
validation. No remote MCP effect was performed during these tests.

## Connection management and sign-in

`GET/POST /connections` lists truthful transport status and persists enablement.
`GET/POST /connections/mcp` and `DELETE /connections/mcp/{name}` manage owner-created
connections. Existing desktop and mobile forms keep their layouts. Desktop grants
include agent IDs, allowed tools and the explicit worker read-only whitelist;
mobile supports agent and worker whitelist inputs. Older clients default new
connections to Speda alone and cannot overwrite existing grants by omitting them.
Built-in and operator-file identities are reserved. At most 32 custom connections
are stored. Secrets are masked in responses; echoing a mask retains that value,
while removing its key removes that credential. HTTP credentials belong in headers,
not query parameters; TLS is mandatory with credentials outside loopback.

`POST /mcp/servers/{name}/reconnect` is explicit and never repeats tool effects.
Failed replacements first revoke the previous connection; a saved configuration
is reported separately from a successful handshake. A new incarnation under the
same name/schema cannot inherit a running turn's grants, including resource/prompt
access. Disabled connections do not handshake at startup. Revocation prevents new
effects; an already dispatched effect still requires its normal receipt and
reconciliation. No automatic effect retry or fallback transport was added.

Authenticated `GET /connections/{google|microsoft|notion}/login` returns a one-use
browser start URL, using the configured callback origin rather than the request's
Host header. The browser start binds the authorization request to an HttpOnly,
SameSite=Lax cookie. Callback state is owner/provider/configuration bound, expires
after ten minutes and is consumed transactionally before code exchange. Google
and Microsoft additionally use S256 PKCE. Notion uses its documented confidential
client flow with state and browser binding; PKCE is not assumed for that provider.
Only these exact browser GET endpoints bypass API-key headers. Missing/changed,
expired or replayed state fails closed; a timeout requires a new sign-in. Browser
responses prohibit caching/framing, and OAuth query parameters are removed before
the outer access log formats its path. Denial/token errors never echo provider
error bodies or credentials. See [Google OAuth](https://developers.google.com/identity/protocols/oauth2/web-server),
[Microsoft authorization codes](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
and [Notion authorization](https://developers.notion.com/guides/get-started/authorization).

`status` distinguishes saved credentials from the count of live services.
`disconnect` invalidates pending sign-ins, clears the shared app-owned cache and
revokes registry connections. Local disconnect does not revoke grants at the
vendor's account portal. Microsoft and Notion refresh rotation persists. A Notion
stdio process with superseded credentials becomes unavailable and requires an
explicit reconnect, rather than silently using its old account or replaying work.
Google consent requests only the five currently adapted services; the old Chat
service and directory-only scopes were not reintroduced.

## Package pins, persistence and production review

Every built-in npx/uvx launcher requires an exact operator-reviewed version in
`MCP_PACKAGE_PINS`, a JSON object mapping the package name to a version. Scoped
package names remain whole keys. Missing/unreviewed pins leave that connection
degraded; startup never resolves `latest`. Custom and operator-file package-runner
commands must themselves contain exact versions. Absolute installed executables
remain operator-managed. These checks are a launch policy, not a dependency or
supply-chain certification. No package versions were guessed or installed by the
integration tests.

`SPEDA_DATA_DIR/connections.json` uses atomic replacement and retains credentials
outside the source tree, with the same host-account protection required for the
existing `runtime_state.json`. Never serve or commit either file. OAuth requests
use the additive `oauth_attempts` table; raw start tickets and authorization codes
are not stored. Tokens remain in the existing runtime credential writer. This
file-backed configuration/token authority requires a single application process;
multiple independent writer processes need a shared transactional credential
store before deployment. PostgreSQL verifies the one-use OAuth claim contract,
but does not make the JSON credential store a multi-process writer.

No universal ITPM limit or measured tool-token count is asserted by connection
diagnostics: `itpm_limit` is null and clients show on-demand loading. Native model
and coordination/global budgets retain spending authority.

Production isolation was reviewed from source only. The retained Playwright image
pins `@playwright/mcp@0.0.79`, runs unprivileged and requests isolated sessions;
its base image digest and browser-install CLI resolution still need deployment
review. The renovated compose does not yet wire the sidecar topology. Actual
container mounts, internal-only network exposure, host policy and restart tests
remain in the deployment milestone. No production safety certification follows
from these local tests.
