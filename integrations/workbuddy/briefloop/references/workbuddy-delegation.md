# CodeBuddy / WorkBuddy Role Delegation

Read this before delegating Scout, Screener, Claim Ledger, Analyst, Editor,
Auditor, or Formatter work from a CodeBuddy or WorkBuddy main session.

## Documented Host Contract

BriefLoop uses the same checked-in role definitions whenever the active
CodeBuddy/WorkBuddy host exposes project-subagent dispatch:

```text
.codebuddy/agents/briefloop-scout.md
.codebuddy/agents/briefloop-screener.md
.codebuddy/agents/briefloop-claim-ledger.md
.codebuddy/agents/briefloop-analyst.md
.codebuddy/agents/briefloop-editor.md
.codebuddy/agents/briefloop-auditor.md
.codebuddy/agents/briefloop-formatter.md
```

CodeBuddy's official subagent contract documents project agents under
`.codebuddy/agents/`, per-agent tool frontmatter, automatic selection, and
explicit invocation by agent name. WorkBuddy's official product contract
documents autonomous multi-step execution plus project Skills, Experts, and
Expert Teams. In supported WorkBuddy sessions, the same CodeBuddy-compatible
BriefLoop project roles can be dispatched by exact name.

Official references:

- <https://www.codebuddy.cn/docs/cli/sub-agents>
- <https://www.codebuddy.cn/docs/workbuddy/>
- <https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Expert-Center>

Do not infer successful delegation from the product label or from a narrative
reply. The host-visible invocation and return of the exact `briefloop-*` role
is the evidence that the subagent ran.

Full BriefLoop execution also requires the **main** CodeBuddy/WorkBuddy session
to expose command execution capable of invoking `briefloop`. The host may call
this a terminal, shell, or another command tool; the UI label need not be
`Bash`. This main-session capability is separate from each role subagent's tool
allowlist.

## How To Invoke A BriefLoop Role

1. Confirm that the BriefLoop source checkout contains the project role file.
2. Re-read the current `agent_handoff.md` and `agent_handoff.json`.
3. Explicitly ask the host to use the exact role name, for example:

   ```text
   Use the briefloop-scout subagent for the Scout work assigned by the current
   BriefLoop handoff. Return the written artifact paths to the main session.
   ```

   In Chinese:

   ```text
   使用 briefloop-scout 子代理执行当前 BriefLoop handoff 指派的 Scout
   工作；完成后把写入的工件路径返回主会话。
   ```

4. Verify that the host actually invoked and returned from
   `briefloop-scout`; do not relabel a generic helper as that role.
5. Back in the main session, run only the deterministic validation or
   transaction currently allowed by the handoff.

Repeat the same pattern with the other exact `briefloop-*` role names. Do not
substitute a generic agent, Expert, or Expert Team while claiming that a
checked-in BriefLoop role ran.

## Tool Surface Is A BriefLoop Choice

The drafting roles declare:

```text
tools: Read, Write, Grep, Glob
```

The Formatter is a read-only readiness reporter and declares:

```text
tools: Read, Grep, Glob
```

This is a BriefLoop least-authority choice, not a CodeBuddy or WorkBuddy
platform restriction. The official CodeBuddy contract permits other project
subagents to include `Bash`; BriefLoop role agents intentionally omit it.
They draft handoff-assigned artifacts only. The main session owns all
`briefloop` / `multi-agent-brief` CLI transactions.

Scout work does not require `Bash`: it reads approved evidence and writes only
the candidate/screened artifacts assigned by the handoff.

## Main-Session Duties

The main CodeBuddy/WorkBuddy session is the Orchestrator for deterministic
control:

- read `output/intermediate/agent_handoff.md` and `agent_handoff.json`;
- explicitly invoke the matching `briefloop-*` role for role-owned draft work;
- run `briefloop` / `multi-agent-brief` CLI transactions after the role returns;
- re-read the handoff after each transaction;
- print a Run Card after key commands;
- never direct-edit control files or frozen artifacts.

Role subagents must not run CLI transactions, gates, finalize, delivery, or
release actions. They cannot spawn another subagent; delegation depth remains
one.

The executable sequence is therefore two-phase at every role boundary:

1. the role subagent reads or writes only its handoff-assigned artifact and returns;
2. the main session re-reads the handoff and runs the permitted deterministic
   CLI validation or transaction.

If the main session has no command-execution capability, full BriefLoop cannot
run in that session; stop before role work or state advancement. The operator
fallback below addresses missing project-role dispatch, not a missing CLI.

## If Project-Role Dispatch Is Unavailable

Stop before role-owned draft work and before claiming any role stage completed.
Setup, `status`, `state check`, `quality summarize`, `doctor`, and demo
inspection remain allowed because they are deterministic main-session actions.

Tell the user that the current host did not dispatch the checked-in project
role. The user may either:

1. continue in a CodeBuddy/WorkBuddy session where the project roles are
   available; or
2. explicitly regenerate the handoff with the host-agnostic operator runtime:

   ```bash
   briefloop run --workspace <workspace> --runtime operator
   ```

Do not silently switch runtime, edit the role agents' tool frontmatter, draft
role-owned artifacts under the existing codebuddy handoff, or claim that a
`briefloop-*` subagent ran.
