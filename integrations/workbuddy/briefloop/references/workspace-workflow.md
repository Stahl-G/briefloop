# Workspace Workflow

BriefLoop workspaces are advanced by deterministic CLI transactions and
role-owned draft artifacts. WorkBuddy may help the user operate the loop, but it
must not hand-edit control files.

## Normal Loop

1. Confirm the workspace path.
2. Run CodeBuddy handoff:

   ```powershell
   & $BriefLoop run `
     --workspace "<workspace>" `
     --runtime codebuddy `
     --repo-workdir "<canonical BriefLoop source checkout>"
   ```

3. Read `output/intermediate/agent_handoff.md` and
   `output/intermediate/agent_handoff.json`.
4. Before each stage or role-owned artifact action, re-read the relevant
   `agent_handoff.md` / `agent_handoff.json` step.
5. Invoke the matching role subagent for role-owned artifact work assigned by
   the handoff.
6. Use the owning CLI transaction when an artifact is ready.
7. After every CLI command, re-read the relevant handoff step before continuing.
8. Run diagnose after every start, CLI command, role return, or interruption;
   follow only the current handoff/diagnose action before repair, finalize,
   quality summary, or delivery.

## Common Inspection Commands

```powershell
& $BriefLoop workbuddy diagnose --workspace "<workspace>" --json
& $BriefLoop status --workspace "<workspace>" --json
& $BriefLoop state check --workspace "<workspace>"
& $BriefLoop quality summarize --workspace "<workspace>"
```

## Progress Updates

After each deterministic CLI transaction, summarize progress visible in the
handoff/diagnose result. Raw workflow state, event log, Registry, timestamps,
and file existence are audit evidence only; they are not an action router and
must not reconstruct gate, finalize, delivery, or next-action truth.

Use a Run Card after every key CLI command, role return, repair action, gate
check, finalize attempt, quality summary, or bundle/export request:

```text
runtime:
current_stage:
run_integrity:
recovery_status:
recovery_action:
blocked:
latest_gate_status:
finalize_report:
delivery_truth:
delivery_event:
next_allowed_action:
```

Read these fields only from `& $BriefLoop workbuddy diagnose --workspace
"<workspace>" --json`. Do not rebuild them from raw control files.

Allowed examples:

```text
已创建工作区。
已生成 CodeBuddy handoff。
当前状态：等待 source/scout artifact。
Quality Panel 已生成。
```

Say an exact Analyst/Auditor role returned only after a host-visible invocation
and return in the current handoff step. Stage completion and audit/gate success
require current deterministic transaction/verdict truth. A matching artifact,
stale event, manual file, or prior transaction proves none of those facts by
itself.

`delivery_truth.valid=true` means the current reader bundle is eligible for a
delivery action. Do not say `交付完成`, `delivered`, or `delivery complete`
unless `delivery_event=delivery_succeeded`. Report
`delivery_bundle_prepared` as local ready and `delivery_draft_created` as draft
created; neither is delivered.

## Hard Stops

- If `doctor` reports any error, stop. Show the full doctor output, workspace
  path, current user, output path existence/writability result, and permission
  or ACL output. Do not downgrade the error yourself. User confirmation,
  `request_human_review`, or a standalone pass in another environment cannot
  turn it into pass; fix it and rerun with the same `$BriefLoop`. A following
  diagnose's `doctor.status=not_run_read_only` cannot clear or route around the
  failure, and its completion action must not be followed. Rerun doctor after
  interruption or uncertain session continuity.
- Do not infer recovery from `run_integrity`; follow `recovery_action` and do
  not deliver while recovery is nonterminal or invalid.
- If `delivery_truth.valid` is not true, do not execute delivery. If
  `delivery_event` is not `delivery_succeeded`, do not claim delivery. Report
  role-draft-only status only when
  `output/intermediate/audited_brief.md` exists; otherwise
  report that no draft or delivery exists yet. This is normal before finalize
  and must not block earlier handoff-assigned stages by itself.
- If a zip, export, or attachment candidate contains `.env` or secrets, stop.
  Do not share it; recommend rotating any exposed key.

## Role Delegation

Read `workbuddy-delegation.md` first. The CodeBuddy/WorkBuddy main session must
delegate role-owned draft work explicitly and use the checked-in project role
names exactly:

```text
briefloop-scout
briefloop-screener
briefloop-claim-ledger
briefloop-analyst
briefloop-editor
briefloop-auditor
briefloop-formatter
```

Role subagents draft only handoff-assigned artifacts. They do not run
BriefLoop CLI commands, edit control files, run gates, complete stages, freeze
the Claim Ledger, finalize, approve/report delivery, or authorize release. A
role return is not a stage pass. The main WorkBuddy session runs deterministic
CLI transactions and diagnose after a role returns.

Formatter is a read-only finalize-readiness reporter. It must not run shell or
CLI, convert Markdown to DOCX, write reader delivery artifacts, or claim
reader-clean, gate/finalize success, or delivery.

Hand-written Markdown/DOCX is `draft/manual/unverified`. A formal finalize
claim must bind actual finalize, valid Finalize Report,
reader-clean/promoted/current render, gate, finalize-complete, current finalize
event, valid delivery truth, and literal outcome; residue routes to
deterministic repair/finalize.

Do not claim Scout, Screener, Claim Ledger, Analyst, Editor, Auditor, or
Formatter subagents ran unless WorkBuddy actually delegated those roles.

If the host does not actually dispatch the exact `briefloop-*` project role,
stop before full workflow execution. You may still run deterministic setup,
`status`, `state check`, `quality summarize`, `doctor`, or demo commands, but
you must not relabel a generic helper or hand-author role-owned artifacts under
the codebuddy handoff. The user must explicitly choose either a
CodeBuddy/WorkBuddy session with project-role dispatch or a regenerated
`--runtime operator` handoff.

If the user is chatting in Chinese, explain the next action in Chinese when
useful, but follow the generated handoff literally. Preserve command
names, artifact names, and handoff obligations exactly. Translation must not
drop steps, soften gate/blocker language, or turn main-session work into a claimed
subagent run.
