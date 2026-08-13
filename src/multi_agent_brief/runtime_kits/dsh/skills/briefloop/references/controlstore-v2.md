# BriefLoop DeepSeek Harness / ControlStore Reference

BriefLoop is the only current project and product name. The former project
acronym is retired.

Use this reference when `runtime continue` cannot complete the next bounded
step, when the current action requires a strict Human request, or when an
external-call result is uncertain.

## Authority model

`briefloop.db` plus verified Receipts is the sole run authority. Files such as
`runtime_action.json`, a `RoleTaskEnvelope`, scratch proposals, Markdown, HTML,
and status JSON are untrusted inputs or projections. Never read them back for
legality; every write command revalidates against the current Store.

Exactly five `CoreRunNextAction.action_kind` values exist:

- `delegate`: one exact role invocation;
- `deterministic`: one host-owned transition;
- `human_decision`: one explicit strict Human request;
- `blocked`: no mutation, typed diagnosis only;
- `complete`: no further runtime action.

A successor start is a separate Human root transaction, not a
`CoreRunNextAction` kind.

## DSH operating kit

The experimental DSH runtime kit is workspace comfort material. It never
writes the Store and the Store does not re-bind on install. Fresh Store
initialization stays Codex-bound in this slice:

```bash
briefloop runtime install --workspace <workspace> --runtime codex
briefloop run --workspace <workspace> --runtime codex
```

Then prepare the DSH kit for the same workspace:

```bash
briefloop runtime install --workspace <workspace> --runtime dsh
briefloop run --workspace <workspace> --runtime dsh
```

`run --runtime dsh` prints the DSH handoff and never generates the brief.
Install copies eight role presets and this operator skill under `.dsh/`:

```text
.dsh/presets/briefloop-source-planner/
.dsh/presets/briefloop-source-provider/
.dsh/presets/briefloop-scout/
.dsh/presets/briefloop-screener/
.dsh/presets/briefloop-claim-ledger/
.dsh/presets/briefloop-analyst/
.dsh/presets/briefloop-editor/
.dsh/presets/briefloop-auditor/
.dsh/skills/briefloop/SKILL.md
```

Copy each preset directory into the DSH preset root
(`${DSH_HOME:-$HOME/.dsh}/.agent-presets/` by default) so the DSH roster can
mount it. Dispatch a role by starting a DSH subagent on the matching preset
and handing it the exact `RoleTaskEnvelope`; the preset persona carries the
role contract, and the envelope—not the preset—fixes the proposal schema and
scratch directory. The operator skill stays in this session.

DSH plugin tools that wrap the `briefloop` CLI are a comfort layer. They call
the CLI only; they never open `briefloop.db` and never decide legality.

## Ordinary loop

```text
diagnose
  → runtime continue
  → role_work_required | needs_human | needs_attention | finalized_local
  → one lawful action
  → runtime continue again
```

`runtime continue` re-verifies Store state before each effect. It can apply
safe deterministic work, materialize the exact role envelope, accept an
already-valid proposal, and stop at a Human or attention boundary. It does not
perform semantic role work itself and does not authorize a provider retry.

## Granular action protocol

Use this only for recovery or unsupported/manual paths.

### Snapshot the action

```bash
briefloop runtime next --workspace <workspace> \
  > <workspace>/runtime_action.next.json \
  && mv <workspace>/runtime_action.next.json \
        <workspace>/runtime_action.json
```

Do not edit or reconstruct this JSON. On `runtime_action_stale`, fetch a fresh
action and preserve any recorded invocation or transaction outcome.

### `delegate`

Start the exact action:

```bash
briefloop runtime invocation-start --workspace <workspace> \
  --action <workspace>/runtime_action.json
```

Read `scratch/<invocation_id>/role_task_envelope.json`. The
`RoleTaskEnvelope` fixes `role_id`, `dispatch_instruction`, context, proposal
schema, `scratch_directory`, and `allowed_output_filenames`.

For strict JSON proposals, run the exact schema command before writing:

```bash
briefloop contract show <proposal_schema_id> --example full
```

Write only allowed scratch outputs, then validate:

```bash
briefloop runtime invocation-validate --workspace <workspace> \
  --envelope <workspace>/scratch/<invocation_id>/role_task_envelope.json
```

After `status=valid`, accept:

```bash
briefloop runtime invocation-accept --workspace <workspace> \
  --envelope <workspace>/scratch/<invocation_id>/role_task_envelope.json
```

If no valid proposal can be produced, record one value-free allowed failure:

```bash
briefloop runtime invocation-fail --workspace <workspace> \
  --envelope <workspace>/scratch/<invocation_id>/role_task_envelope.json \
  --reason <allowed-reason>
```

Do not start a second invocation while the action is
`invocation_accept_or_fail`. Recover the exact active envelope and either
accept its valid proposal or fail it.

### `deterministic`

```bash
briefloop runtime apply --workspace <workspace> \
  --action <workspace>/runtime_action.json
```

The host derives the transaction from verified Store state. A role never
performs this effect. When an action explicitly requires typed repair content,
pass only the exact `--action-input` schema requested by the action.

### `human_decision`

1. Run `briefloop contract show <request_schema_id> --example full`.
2. Materialize a complete request bound to the current run, action
   fingerprint, expected Store revision, and frozen inputs.
3. Show the Human the consequential fields: decision, scope, provider/cost,
   retry count, delivery effect, or guidance-reuse effect.
4. Obtain explicit confirmation.
5. Apply exactly once:

```bash
briefloop runtime apply --workspace <workspace> \
  --action <workspace>/runtime_action.json \
  --human-request <workspace>/<request>.json
```

A message such as “继续” is sufficient only when the exact pending decision
and consequences were already shown and no ambiguity remains. It is not a
request file and does not itself mutate the Store.

### `blocked`

Run only read-only diagnosis:

```bash
briefloop runtime diagnose --workspace <workspace>
```

Report `effect_kind`, `reason_code`, stage, revision, and fingerprint. Never
edit content or state merely to hide a block.

### `complete`

Do not apply again. `effect_kind=finalized_local` is a local reader brief;
`effect_kind=package_ready` is a Human-controlled package boundary; only
`effect_kind=delivered` records delivery.

## Terminating an unresolvable Human review

When the current Store action is `gate_repair_human_review` or
`audit_human_review`, it binds
`briefloop.run_termination_request.v2`. Termination is irreversible and
preserves the failed run; it never turns a failed Gate or audit into a pass.

1. Run `briefloop contract show briefloop.run_termination_request.v2 --example full`.
2. Bind the request to the exact current run, Store revision, and
   `action_fingerprint`. Include the Human `actor_id`, a value-bearing `reason`,
   and a compatible typed `reason_code`.
3. Show the Human that the result is terminal and that any further work needs
   a new run.
4. After explicit confirmation, apply it once with
   `briefloop runtime apply --human-request`.

`gate_repair_unresolvable` is valid only for gate-repair review;
`negative_audit_truth_accepted` only for audit review;
`operator_abandon` is valid for either. Success appends one `run_terminated`
event. The next action is `complete/run_terminated`, and continuation returns
`terminated` without role, provider, finalization, post-final review, or
delivery effects.

## Role dispatch discipline

- `execute_in_current_session`: this session performs the one exact role task.
- `delegate_exact_role`: dispatch only the role named by the envelope as a DSH
  subagent on its matching BriefLoop preset; do not spawn a broad swarm or
  multiple same-stage roles.
- `use_declared_route`: use only the declared route.

Specialists write proposals only. The host owns Store writes, provider I/O,
source promotion, validation, Gate evaluation, artifact freezing, and receipts.

## Failure and recovery matrix

| Signal | Meaning | Lawful response |
|---|---|---|
| `runtime_action_stale` | Store advanced after the snapshot | Fetch a fresh action; do not replay the old one |
| active `invocation_accept_or_fail` | One invocation already owns the stage | Recover its envelope; validate/accept or fail it |
| `proposal_invalid` | Scratch bytes violate the bound contract | Fix only that proposal from typed violations |
| `control_store_integrity_invalid` | Authority graph cannot be trusted | Stop; preserve bytes; diagnose read-only |
| provider failure with complete execution evidence | Provider outcome is known | Derive `unable_to_assess` or replay locally; no redial |
| `local_derivation_failed` | Provider evidence is frozen, local derivation failed | Retry derivation only; adapter, credential, and network stay unused |
| true `outcome_unknown` | Claim exists but no execution receipt | Preserve uncertainty; a new provider attempt needs new Human authorization |
| presentation `browser_unavailable` | Static projection exists but browser did not open | Return the safe relative HTML path |
| presentation `projection_unavailable` | No safe projection was written | `projection_unavailable` has no path; diagnose projection separately |

Provider failure is not “no event” and a zero-finding valid assessment is not
provider failure.

## Source discovery and Solar Stock Periodic

The Store-frozen search plan is authority. Solar Stock Periodic uses 20 atomic
first-pass tasks, 20 results per task, advanced search, and up to one
deterministic 30-day backfill, targeted to each under-covered task. Search may
discover up to 800 unique URLs; Extract batches of 20 are transport batching,
not a product total. There is no one-wide-query or top-five fallback.

`RunSourceDiscoveryAuthorization` governs the frozen atomic task matrix and
provider attempt; `RunExecutionAuthorization` governs authorized local
continuation. Every task requests 20 advanced Search results. Search snippets are
never source-pack members or claims-eligible; successful Extract content is claims-eligible.
Runs without either authorization stop at their
typed Human/manual boundary instead of inferring provider or execution rights.

The runtime owns Tavily Search and Batch Extract. Source Planner may propose a
plan but cannot silently change it. Source Provider never calls Tavily, reads a
credential, writes the Store, or promotes a search snippet. Only successful
non-empty Extract bytes can become source members; failed tasks remain visible.

Market data is a separate authority surface:

- manual JSON/CSV must include security, exchange, date, currency, value, and
  source;
- verified manual values win and Yahoo fills gaps;
- conflicts are findings, never silent overwrites;
- adjusted close drives 1-week, 1-month, and YTD returns;
- unavailable or non-meaningful valuation multiples remain visibly `N/M`.

## AI Second Opinion

`briefloop quality laj review-open --workspace <workspace>` owns a loopback
review server and waits. Keep its process alive while using the page; a stale
HTML tab cannot write.

The request freezes full reader report context, exact rubric/profile, bounded
RunDirection context, prompt identities, model identity, and budget. Raw API
credentials never enter prompt or HTML.

Provider execution evidence is append-only and precedes local derivation. Once
an execution receipt exists, `retry` is recovery-only and must not access the
adapter factory, credential, or network. The page distinguishes:

- completed unit with no finding;
- provider unable to assess;
- local derivation failed with recoverable execution evidence; and
- true outcome unknown.

Human observations are report-origin records and may exist without a model
finding. Supersession creates a new revision. Guidance draft, approval,
deactivation/revert/supersession, and successor inclusion are separate recorded
choices.

The explicit successor command is:

```bash
briefloop runtime successor-start --workspace <workspace> \
  --direction-json '<strict RunDirection JSON>' \
  --run-id <new-run-id> \
  --include-approved-guidance
```

The reuse snapshot is complete-or-fail within 16 items and 65,536 combined
UTF-8 bytes. `review-open` remains current-head-only; exact historical status
commands may resolve compatible archived results. `FrozenGuidanceContext`
reaches only Analyst and Editor and applies to audience fit, structure, style,
and expression. Current `RunDirection` and evidence govern.

## Security and evidence boundaries

- Never display, hash for display, log, or copy API-key values.
- Never let prompt text authorize provider I/O or Store mutation.
- Never use search snippets, generated summaries, or discovery candidates as
  claim evidence.
- Never treat traceability as proof of semantic support.
- Never mutate or replace frozen history; use a new revision/record.
- Never claim approval, delivery, or successful recovery without its Receipt.
