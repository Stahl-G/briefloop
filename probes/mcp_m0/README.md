# MCP M0 protocol and lifecycle experiment

This isolated experiment uses the official Python MCP SDK (`mcp==2.2.0`, MIT).
It does not register application connectors or modify the application dependencies.
It starts real local server processes, reads a synthetic UTF-8 file from disk,
and exercises JSON-RPC through stdio and loopback Streamable HTTP. It does not
use an in-process transport, fabricated RPC responses, an LLM, credentials, or a
paid connector. HTTP rejection endpoints deliberately return real HTTP errors.

## Run (macOS / POSIX, Python 3.11+)

From the repository root, in a dedicated virtual environment:

```sh
python3.11 -m venv /tmp/briefloop-mcp-probe-venv
/tmp/briefloop-mcp-probe-venv/bin/python -m pip install -r probes/mcp_m0/requirements.txt
/tmp/briefloop-mcp-probe-venv/bin/python probes/mcp_m0/probe.py --output /tmp/briefloop-mcp-run-01
```

Use a **new output directory** for each run. The command refuses to overwrite
one. It does not install anything while running. Server commands use an explicit
interpreter and argument list without a shell. HTTP uses an owned ephemeral
loopback socket; it does not attach to existing services. The lifecycle probe is
POSIX-only; it rejects Windows rather than claiming Windows cleanup coverage.

To check coexistence with a BriefLoop wheel, install that wheel into the same
fresh environment, run `python -m pip check`, import `briefloop.models`,
`briefloop.store`, and `briefloop.server`, then run the probe. `report.json`
records the interpreter, OS, and resolved dependency versions. The requirements
file pins the MCP SDK only; transitive versions are recorded, not locked.

## What is exercised

- Real stdio and HTTP, both automatic negotiation and explicit legacy mode.
  Expected negotiated versions: `2026-07-28` and `2025-11-25` respectively.
  Both sides use SDK 2.2.0; legacy mode is **not** a test against an older SDK.
- Tools/resources/templates listing, actual tool invocation, file content and
  SHA-256 comparison, and resource reads. A server-scoped `file://` URI travels
  through MCP to the server; it is not opened as a local client path.
- `isError=true` from `ToolError`, versus an unflagged business-failure text.
  Protocol delivery alone does not establish business success or usable material.
- Caller cancellation and SDK timeouts, independent server cancellation events,
  and a working call after cancellation. This controlled cooperative server is
  not evidence that arbitrary external operations can always be stopped.
- Server exit during a call, one observed server invocation, no application retry,
  and errors captured around the entire client context, including `__aexit__`.
- Normal stdio process exit, a deliberately spawned disposable descendant, and
  explicit probe cleanup if the SDK leaves it alive. HTTP client disconnect must
  leave the separately owned HTTP server running; its owner then stops it.
- Real HTTP 401/403/500 responses, first with SDK negotiation unguarded, then with
  a supported `httpx2.AsyncClient` response hook rejecting non-protocol failures.
  The guarded cases assert that only `server/discover` is sent.

`report.json` and per-case `summary.json` contain the results. `server.jsonl`
contains events observed by the actual server process. `*.sdk-result.json`
contains actual SDK-decoded result models, dumped by alias with unset fields
omitted. **These files are not raw wire bytes**; preserve that distinction when
building future evidence receipts. Failed runs are retained in their own output
directory. Expected transport failures include their traceback.

The success flag describes the bounded required checks, not universal SDK safety.
Check the descendant and handshake observations explicitly; the experiment can
succeed while exposing a behavior that the application must handle.

## Boundaries before production integration

The experiment provides no grants, account isolation, persisted budget, source
adoption, Reviewer integration, configuration UI, or model-driven report loop.
It does not verify OAuth, third-party providers, legacy HTTP+SSE, media limits,
Windows, or a second host. It is not a runtime sandbox.

SDK 2.2.0 observations on macOS:

1. Normal stdio parent exit can leave a descendant alive; its shutdown path only
   escalates to process-tree termination if the parent itself remains running.
   The probe records this and cleans only the isolated group it created. A
   production manager still needs an explicit process-ownership policy.
2. Default automatic negotiation attempts `initialize` after HTTP 401/403/500.
   The tested response hook prevents those fallback attempts. The hook is a
   narrow feasibility demonstration, not a complete endpoint/authentication or
   transport-fallback policy.
3. Abrupt HTTP server death can raise an `ExceptionGroup` from client context
   teardown. A manager must handle owner lifecycle failures, not only RPC errors.
4. Client byte ceilings need separate work. The inspected SDK stdio reader grows
   a line buffer and HTTP JSON responses use `aread()` before parsing. A parsed
   result size check is not a pre-parse memory bound. Production integration needs
   bounded framing/HTTP streams, decompression and media limits, with real probes.

Official API references:
[SDK](https://github.com/modelcontextprotocol/python-sdk),
[client transports](https://py.sdk.modelcontextprotocol.io/client/transports/),
[protocol](https://modelcontextprotocol.io/specification/2026-07-28).
