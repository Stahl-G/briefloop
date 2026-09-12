# Frozen run grants and persisted MCP material

`ConnectorMaterials(store, connector_service)` composes the existing workspace
Store, source originals/provenance, run_sources and Reviewer packet builder. It
creates two small SQLite tables in the same ControlStore, not another report
pipeline. It does not choose models, create jobs, raise budgets, generate reports,
or run a Reviewer. The trusted host must bind these interfaces to its existing
run/job and local user authorization boundary before exposing material operations.

```python
from briefloop.connectors.materials import ConnectorMaterials

materials = ConnectorMaterials(store, connector_service)
grant = materials.freeze(
    run_id,
    [{"connector_id": selected_id, "resources": ["m0://document"], "tools": []}],
    max_calls=2,
    max_total_bytes=131072,
)
result = materials.read(grant["id"], run_id, selected_id, "m0://document",
                        request_id="stable-host-request-id")
```

This example URI is a controlled test resource, not a real provider. Both budget
arguments are required; there is no implicit allowance. Freeze requires currently
connected and enabled configurations. It captures each configuration revision,
selected catalog descriptions, protocol, per-response limit, exact resource URIs
and tool names, with an immutable snapshot hash. Only selected catalog resources
are supported; URI templates are not implicitly expanded or followed. Neither a
server annotation nor tool name proves read-only behavior: the host must get
explicit authorization for each selected tool, and must not authorize side effects
merely because the server advertises a `readOnlyHint`.

## Host integration

- `freeze(run_id, selections, *, max_calls, max_total_bytes, grant_id=None)` is a
  **host-only user action**. A supplied stable `mcpgrant_...` ID supports identical
  request reuse; it cannot overwrite a frozen grant or reactivate a revoked one.
- `read(grant_id, run_id, connector_id, uri, *, request_id)` and
  `call(grant_id, run_id, connector_id, name, arguments, *, request_id)` are material
  operations. The host supplies the already-bound run/grant, not the Agent. A
  same-ID request with different content is rejected before network activity.
- `revoke(grant_id, run_id)` commits persistent revocation and stops its private
  service scope. It never removes earlier admitted snapshots.
- `status(grant_id, run_id)` returns `{grant, usage}`. Usage contains `calls`,
  `charged_bytes`, `max_calls`, `max_total_bytes`.
- `receipt(grant_id, run_id, receipt_id)` returns a bounded operation summary,
  only after verifying both run and grant ownership.

Operation summaries contain `receipt_id`, `status`, `source_id`, `delivery`,
`error`, `charged_bytes`, `material_status="unassessed"`, `replayed=false`.
`status="admitted"` means a source row and its run binding committed atomically.
`not_admitted` retains the request and receipt/error without a source. A duplicate
request can report `in_flight` or `received` if the first worker has not completed
local admission. Such a reservation, including one orphaned by process death, is
never automatically resent. An admitted duplicate returns its saved source even
when the connector is now offline or its grant revoked. This is local reuse, not
a new permission or live verification.

Run, job and Agent integration remains owned by the host: freeze and save the
grant ID before acquiring material, preserve stable request IDs through recovery,
route only permitted material operations to the acquisition facade, expose revoke,
and do not expose configuration or grant mutation to an Agent. Reviewer hosts
must retain their existing read-only isolation and receive only saved source
snapshots; they must not receive ConnectorService or material call APIs.

## Budget, cancellation and configuration races

The existing Store transaction uses `BEGIN IMMEDIATE`. Before sending, it reserves
one call and the connector's entire configured response limit against the grant's
byte budget. Concurrent callers cannot overbook this reservation. Successful
SDK-decoded payloads settle to their actual serialized byte length. Ambiguous
post-send outcomes retain the worst-case byte charge; failures still consume a
call. This is a bounded material/request budget, not a monetary or token estimate.
Local provenance duplication is not counted as another upstream response. A small
remaining byte balance that cannot cover the next worst-case response blocks it;
no automatic increase or retry occurs.

`ConnectorService.read/call(..., expected_revision=...)` verifies the pinned
configuration while holding its lock, including immediately before submit. Each
returned result carries an opaque `connection_epoch`.
`material_admission(..., expected_revision, connection_epoch, scope_id)` holds the
same local service lock while the short source transaction runs. Disabling,
changing configuration, closing or re-enabling a connection makes an old receipt
ineligible for new admission. `revoke_scope` permanently tombstones that service
scope, including calls not yet started; persistent grant state is checked again
inside the source transaction. Revoke commits its DB state before waiting on the
service lock, so it cannot invert the admission lock order. An already committed
source remains immutable after revocation.

## What is saved and what is proven

The operation table retains the request and original **SDK-decoded** result with
SHA-256. No wire-capture claim is made. Successful text resources, text tool
content, embedded text resources and structured tool JSON can become sources.
An explicit tool error, timeout, cancellation, unknown outcome, empty result or
unsupported binary/image content is not silently converted to a ready source.
Unsupported material receipts remain available for a future explicit extraction
path. An unflagged business error written as ordinary text is still unassessed
source content; this layer does not guess success/facts from prose keywords.

Each admitted source has the usual `.txt`, `.provenance.json` and
`.original.json` files. The original is a self-contained receipt envelope with
unchanged decoded payload and its hash, grant/hash/run/connector/revision IDs,
request hash/target, protocol and acquisition time. The envelope has its own hash
in provenance. It contains no saved connector credentials or command environment.
Server-returned content and explicit tool arguments are task material, not trusted
instructions. Source text, receipt envelope and the existing packet fingerprint
are separate integrity bindings; source admission is not fact verification.

`source_files` verifies the original hash, and `build_packet` copies that immutable
JSON original and readable source text into the existing offline Reviewer packet.
No live connector is needed to inspect this saved evidence.

## Transaction and file recovery

`Store.add_source(..., connection=connection)` uses the caller's existing Store
transaction without committing or closing it. Default callers keep their previous
behavior. Text files precede DB admission; composing callers own rollback cleanup.
This module writes immutable original/provenance files, then adds the source and
run binding in the same transaction after the grant/epoch checks. On failure it
removes only files newly created by this operation, and only when no source row
references their ID. Existing/reused files are never deleted. A process crash can
leave unreferenced forensic files or a reserved request, but cannot make that
partial attempt a visible ready source. No automatic upstream replay is performed.

## Checks

```sh
PYTHONPATH=/absolute/repo/src /python-with-mcp-2.2/bin/python -m unittest discover -s tests -p 'test_connector*.py' -v
```

The material checks start real official-SDK HTTP servers over loopback, read actual
local files, save actual sources and build packets after shutdown. A scheduling
barrier pauses an actual response only to exercise revoke/disable admission races;
it does not mock the wire result. Other checks cover bounded duplicate requests,
quota, configuration changes, timeout/error receipts and composable source rollback.
The deterministic brief used for the packet is a fixture, not a newly generated or
reviewed model report. No enterprise account, new fee or Windows process work is
part of this backend slice.

## Trusted task binding

`TaskMaterials` in `tasks.py` wraps acquisition with a frozen `jobs.id` binding.
Only `generate` jobs that are still queued may bind. After catalog capture it
rechecks job status and run identity and inserts the binding in one SQLite write
transaction, serialized against Worker claiming that job. If the job started in
between, the newly created grant is revoked; a running job never gains authority.
A second bind cannot overwrite or enlarge the first. Host revocation remains
available after task completion.

Trusted browser routes (normal page session header required):

- POST `/api/connectors/task-bind`: `job_id`, `selections`, `max_calls`, `max_total_bytes`.
- POST `/api/connectors/task-status`: `job_id`.
- POST `/api/connectors/task-access`: `job_id`; returns `access_token`, `tool_path`.
- POST `/api/connectors/task-revoke`: `job_id`.

Host access issuance never changes the grant. Tokens exist only in process memory;
restart invalidates them and resume requires fresh trusted issuance. Give the token
only to the generating process. Do not place it in requirements, prompts preserved
for review, source metadata, events, or review packets.

Agent POST `/api/connectors/task-tool` requires `Authorization: Bearer <access_token>`.
The accepted bodies are exactly:

- `{"action":"status"}`
- `{"action":"receipt","receipt_id":"..."}`
- `{"action":"read","connector_id":"...","uri":"...","request_id":"stable-id"}`
- `{"action":"call","connector_id":"...","name":"...","arguments":{},"request_id":"stable-id"}`

No run/job/grant IDs, authorization commands, or budget overrides are accepted.
Every request verifies the bound job is still queued/running and still targets the
same run. Review/assessment jobs cannot receive access. Tokens do not authorize
browser configuration routes. Revocation invalidates all issued task tokens and
revokes the material scope, including late-result admission.

### Remaining host integration

The routes and scoped tool are executable, but the frontend/Worker must still wire
creation, bind-before-claim, process token injection and cancel-to-revoke. Binding
an already enqueued job can safely fail if Worker wins; product integration should
bind before making that job runnable using the existing creation boundary. No new
state machine is provided here. Existing source-empty run validation also needs an
explicit selected-MCP path; do not enable web search merely to bypass that check.
Tests use allowed-web runs but make only local fixture HTTP requests, no searches.

This boundary constrains these application APIs; it is not OS isolation against a
host process with arbitrary access to the workspace/database or browser session.
Reviewer must retain its actual existing read-only snapshot sandbox and receive
neither live tool capabilities nor credentials. The offline packet test uses a
deterministic citation-bearing draft, not a model-generated or model-reviewed report.
