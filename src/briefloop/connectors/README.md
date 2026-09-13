# Local MCP connection service

`ConnectorService(workspace_path)` is a synchronous facade for local settings.
Construction loads configuration without executing saved commands or connecting
HTTP endpoints. One AnyIO portal owns SDK clients, and each client is entered and
exited in the same async owner task. The official `mcp==2.2.0` SDK is required for
connection operations; configuration operations can still work without it.

```python
from briefloop.connectors import ConnectorError, ConnectorService

service = ConnectorService(workspace_path)
try:
    saved = service.save(
        {"name": "Documents", "transport": "http", "url": "https://example.com/mcp"},
        secrets={"bearer_token": "user-supplied-token"},
    )
    tested = service.test(saved["id"])
    connected = service.enable(saved["id"])
    service.disable(saved["id"])
finally:
    service.close()
```

The example URL is illustrative, not a verified provider. Tests use independently
started real SDK servers on loopback sockets and stdio processes.

## Settings API

- `list()` returns an array of public connection objects.
- `save(config, *, connector_id=None, secrets=None)` creates or updates one.
  Updating disconnects the old revision and saves the new revision disabled.
  Omitting `secrets` preserves them; `{}` explicitly clears them. Configuration
  must contain only `name`, `transport`, the relevant endpoint fields, and limits.
- `test(id)` uses an isolated preview connection, obtains actual catalogs, closes
  it, and stores the test result. It does not enable the connector or grant a report
  permission. Returned shape: `{id, ok, checked_at, duration_seconds, protocol,
  capabilities, error}`.
- `enable(id)`, `disable(id)`, and `status(id)` return public connection objects.
  Enable failures return `state="error"`, `enabled=false`, and a safe error.
  Calling `enable` again explicitly reconnects. Saved enabled preferences do not
  execute commands at service construction; their initial state is disconnected.
- `delete(id)` disconnects, removes its config and local credential binding, and
  returns `{id, deleted: true}`.
- `close()` closes owned connections and joins the portal thread; it is idempotent.

Public connection objects are flat:

```text
id, name, transport, url? / command?+args?+cwd?, timeout_seconds,
max_response_bytes, revision, enabled, has_credentials, credential_type, env_names,
state, protocol, last_test, error, capabilities
```

`state` is disabled/disconnected/connecting/connected/error. Connected means an
active owner whose last negotiation/catalog succeeded, not continuous upstream
availability or material verification. A known failed connection takes error
priority even if another scope still has a live connection. `last_test` retains
its actual check time; it is not silently refreshed by viewing settings.
`capabilities` has tools/resources/resource_templates arrays. `error` is null or
`{code, message}`. ConnectorError inherits ValueError and has a `code`; messages
are safe descriptions, not original SDK exceptions or HTTP headers.

Config defaults: timeout 20 seconds, maximum response 1 MiB. Timeout can be
0.1–120 seconds; response limit can be 1 KiB–8 MiB. HTTP requires HTTPS except
loopback HTTP. URLs cannot carry userinfo, query secrets, or fragments. This first
slice deliberately does not support endpoints requiring URL query parameters.
Stdio requires an absolute installed executable, argument array, and optional
absolute existing cwd. The service never inserts package installation commands;
the explicitly configured executable remains ordinary local code, not a sandbox.

Secrets are separate `{bearer_token: string, authorization_header: string,
env: {NAME: value}}`. HTTP accepts either a Bearer token or an exact Authorization
header value; the two are mutually exclusive. No arbitrary header map is accepted.
Stdio uses env values. Public `credential_type` reports bearer/authorization/env/none,
never the saved value. Their values are stored under workspace `.connectors/` in
separate credential files (POSIX mode 0600, directory 0700), not public DTOs.
On Windows, protected DACLs restrict these files to the current user and reparse
points are rejected; POSIX mode bits are not treated as Windows access control.
An internal `.gitignore` excludes this runtime directory from normal Git staging.
This is local file protection, not encryption or a Keychain integration. Stdio
inherits the SDK's small default environment plus explicit env values; Python and
dynamic-library injection environment settings are rejected. HTTP disables ambient
proxy/netrc inheritance and retains certificate verification.

## Transport and lifecycle boundaries

HTTP response hooks stop 401/403/429/5xx before legacy negotiation fallback.
Responses use bounded streams before SDK JSON/SSE parsing. The client requests
identity encoding and rejects other content encodings; post-parse trimming is not
used as the memory bound. The official SDK handles same-origin redirects. General
network/DNS policy, OAuth, legacy HTTP+SSE and third-party compatibility remain
separate work.

On POSIX, stdio goes through a small byte-forwarding supervisor. It bounds a line
before forwarding it to the SDK, bounds forwarded stderr, and uses argument lists
without a shell. The service verifies the SDK-created subprocess group at startup
and clears that owned group even when the main subprocess already exited. Windows
uses a supervisor-owned Job Object to clear only its command and descendants,
including on forced supervisor exit. Native HTTP/stdio and owned-process cleanup
have been checked on Windows; see [platform validation](../../../docs/windows.md).
These lifecycle controls are not a file/network sandbox or Reviewer isolation.

Caller stop/disable cancels startup and active operations. Unknown post-send
results are never replayed. Both normal RPC failures and SDK context teardown
ExceptionGroups are handled. SDK automatic input-required continuation is disabled;
this service does not supply sampling, elicitation, roots or model callbacks.

## Internal operations (not an Agent API)

`call(id, tool_name, arguments, *, scope_id)` and
`read(id, opaque_resource_uri, *, scope_id)` use one connection per connector
revision and scope. Different scopes do not share SDK sessions. `close_scope`
closes a scope. Results distinguish received/cancelled/unknown, SDK isError,
unassessed material, and SDK-decoded payload/hash. These are **not raw wire
receipts and are not yet persisted source evidence**. Resource URIs are sent to
the selected server, including file URIs; the client never opens them locally.

Report generation uses `TaskMaterials` and `ConnectorMaterials`, which bind trusted
run grants, atomic durable budgets, receipt persistence and source admission.
Raw service methods remain internal: merely passing a scope string is not
authorization. Config management uses the existing app's local settings
authentication boundary. One service owns one
workspace; multiple writers to the same workspace require the existing host lock.

Behavior checks:

```sh
PYTHONPATH=/absolute/path/to/repo/src /path/to/python -m unittest discover -s tests -p test_connectors.py -v
```

The checks need the official SDK installed for actual protocol cases. They cover
save without execution, secret isolation, real HTTP and stdio, separate scopes,
stop during startup/call, descendant cleanup, HTTP auth-error fallback, and bounded
large resource responses. No report, paid connector, or model invocation is used.

Run-scoped acquisition and durable source admission are provided separately by
`ConnectorMaterials`; see [MATERIALS.md](MATERIALS.md). Raw service `call/read`
remain internal and must not bypass that facade.
