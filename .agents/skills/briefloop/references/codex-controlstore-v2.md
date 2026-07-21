# Codex ControlStore v2 Runtime

This is the executable protocol for a fresh BriefLoop workspace whose selected
runtime is `codex`. SQLite receipts and ledger relations are the sole authority.

## 1. Install And Enter The Runtime

Install the packaged workspace-local Codex kit when it is absent:

```bash
briefloop runtime install --workspace <workspace> --runtime codex
```

Open and trust the workspace in Codex so its project `.codex/config.toml`,
skill, and exact role agents load. Start or reopen the run with:

```bash
briefloop run --workspace <workspace> --runtime codex
```

The command emits one strict `CoreRunNextAction`. It does not emit a legacy
handoff and does not authorize work beyond that action.

## 2. Snapshot The Exact Action

Before executing an action, write the exact command output to a regular file
inside the workspace. A safe refresh pattern is:

```bash
briefloop runtime next --workspace <workspace> \
  > <workspace>/runtime_action.next.json \
  && mv <workspace>/runtime_action.next.json \
        <workspace>/runtime_action.json
```

Pass that exact file to `invocation-start` or `apply`. The file is an
untrusted snapshot, not authority; the runtime re-verifies every field against
the current Store. If it returns `runtime_action_stale`, stop, preserve any
already-recorded invocation result, fetch a fresh action, and do not retry the
old action.

Never edit an action JSON, copy selected fields into a new object, or infer the
next action from a transaction result, projection, filename, prompt, or memory.

## 3. Dispatch By `action_kind`

Exactly five action kinds exist.

### `delegate`

1. Start the exact action:

   ```bash
   briefloop runtime invocation-start --workspace <workspace> \
     --action <workspace>/runtime_action.json
   ```

2. Read the host-materialized
   `scratch/<invocation_id>/role_task_envelope.json`. The
   `RoleTaskEnvelope` fixes the role, stage, action fingerprint, context mode,
   dispatch instruction, scratch directory, allowed filenames, and proposal
   schema.
3. Obey `dispatch_instruction` exactly:
   - `execute_in_current_session`: the current Codex session performs only the
     named role task.
   - `delegate_exact_role`: invoke only the exact installed Codex role named by
     `role_id` and give it the envelope. Do not let root substitute for it.
   - `use_declared_route`: use only the declared existing route.
4. The executing role writes only the permitted proposal files under its own
   `scratch_directory`. It must not call BriefLoop runtime commands, write the
   Store, or write canonical artifacts.
5. When the proposal is complete, the root host accepts it through:

   ```bash
   briefloop runtime invocation-accept --workspace <workspace> \
     --envelope <workspace>/scratch/<invocation_id>/role_task_envelope.json
   ```

6. If execution cannot produce a valid proposal, record exactly one allowed
   failure reason:

   ```bash
   briefloop runtime invocation-fail --workspace <workspace> \
     --envelope <workspace>/scratch/<invocation_id>/role_task_envelope.json \
     --reason <allowed-reason>
   ```

   Allowed reasons are `dispatch_unavailable`, `child_failed`,
   `child_timed_out`, `session_interrupted`, `proposal_missing`, and
   `proposal_invalid`. Do not place private text, paths, or model output in a
   reason.

If an invocation is already active and the current action says
`effect_kind=invocation_accept_or_fail`, do not call `runtime apply`. Continue
from the already-materialized envelope. `invocation-start` without `--action`
may only be used to recover that exact active envelope; it is not permission to
start a different role.

### `deterministic`

Apply the exact action through the root host:

```bash
briefloop runtime apply --workspace <workspace> \
  --action <workspace>/runtime_action.json
```

The host derives the strict transaction request from verified Store state. Do
not make a role perform a deterministic effect. The sole extra input is for an
`artifact_supersede` action: supply the exact strict
`briefloop.runtime_repair_content_input.v2` through `--action-input`. Do not
invent another repair or content path.

`source_acquire` is also a deterministic `runtime apply` action. Its internal
provider invocation does not turn search snippets into evidence and does not
authorize a specialist to bypass source eligibility.

### `human_decision`

Stop for the human decision identified by `request_schema_id`. A chat reply is
not the request. Materialize one complete strict request inside the workspace,
show its consequential fields to the human, obtain explicit confirmation, then
run:

```bash
briefloop runtime apply --workspace <workspace> \
  --action <workspace>/runtime_action.json \
  --human-request <workspace>/<strict-request>.json
```

Current strict request families cover human source material, internal approval,
and delivery authorization/reconciliation. Do not guess Store revision, run id,
hashes, decision vocabulary, or authorization scope. Missing or mismatched
requests fail closed.

### `blocked`

Do not delegate, apply, edit files to hide the block, or choose a fallback.
Report the exact `effect_kind`, `reason_code`, stage, Store revision, and action
fingerprint. Use `briefloop runtime diagnose --workspace <workspace>` only for
read-only typed diagnosis.

### `complete`

Do not apply or delegate. Report the exact terminal effect:

- `effect_kind=package_ready`: the local delivery package is ready; delivery
  has not succeeded.
- `effect_kind=delivered`: the recorded delivery succeeded.

File existence, HTML, Quality Panel, checkout bytes, or a prior delivery event
cannot upgrade `package_ready` to `delivered`.

## 4. Refresh And Continue

`invocation-accept`, `invocation-fail`, and `runtime apply` return a typed result
that includes `next_action`. Treat it as a convenience view, then refresh and
snapshot the exact current action with `runtime next` before the next mutation.
Repeat until the action is `blocked`, `human_decision`, or `complete`.

## 5. Authority And Isolation

- Never edit `briefloop.db`, SQL, receipts, ledger records, invocation records,
  frozen artifacts, or canonical artifact revisions.
- Never write another invocation's scratch or any filename absent from
  `allowed_output_filenames`.
- JSON, JSONL, Markdown, HTML, status, handoff, finalize, Quality Panel, and
  checkout files are projections only. Never read them back for legality.
- A JSON-only workspace is unsupported. Do not migrate, import, dual-read,
  dual-write, or fall back to it.
- Do not use `operator`, `start`, `--skip-doctor`, legacy state/gate/repair/
  finalize/delivery commands, or another runtime as a recovery path.
- `role_topology=single_session` is one shared Codex context with separate
  Receipt-backed invocations and stage-separated self-review. It is not
  independent review. Future-stage drafting before its invocation is forbidden.
