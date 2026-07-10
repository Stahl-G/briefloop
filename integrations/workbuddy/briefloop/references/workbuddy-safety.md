# WorkBuddy Safety

WorkBuddy is a local operator shell around BriefLoop. It is not a new BriefLoop
authority layer.

## Do

- classify missing workspace paths as existing workspace or first-time run,
  then confirm the folder path before any creation;
- report the active BriefLoop CLI path and version;
- treat local/no live web search as the first-run default;
- when live web search is requested, use Tavily first and verify
  `TAVILY_API_KEY` without displaying the key value;
- use `--runtime codebuddy` for full workflow handoff;
- invoke the matching CodeBuddy-compatible role subagent for role-owned draft
  work;
- run deterministic BriefLoop CLI commands when the user approves;
- print a machine-fact Run Card after key commands, role returns, repairs,
  gates, finalize attempts, quality summaries, and bundle/export requests;
- before each stage or role-owned artifact action, re-read the relevant
  `agent_handoff.md` / `agent_handoff.json` step;
- after each CLI command, report only deterministic progress visible in status,
  workflow state, event log, or generated artifacts;
- keep role delegation claims literal;
- explain Quality Panel as an audit attachment.

## Do Not

- guess a workspace from the repository path;
- claim setup is incomplete only because optional search-provider keys are
  empty;
- ask the user to choose among all search providers unless they request an
  alternative to Tavily;
- direct-edit control files or frozen artifacts;
- say specialist subagents ran unless WorkBuddy actually delegated them;
- silently fall back to `--runtime operator` for a full workflow;
- hand-author BriefLoop workflow JSON artifacts when role subagents are
  unavailable;
- say `Analyst 已经分析完成` or `Auditor 已通过` unless the matching artifact,
  event, transaction, or status output exists;
- say `delivered`, `delivery complete`, or `交付完成` unless
  `briefloop workbuddy diagnose --json` reports `delivery_truth.valid=true`;
- never authorize or block finalize or delivery from `run_integrity` alone. A
  `contaminated` run may finalize only when
  `recovery_truth.finalize_allowed=true` and
  `next_allowed_action=run_finalize_after_recovery`; it may not deliver,
  export, or share. A `contaminated_repaired` run may deliver only when both
  `delivery_truth.valid=true` and `delivery_truth.eligibility.allowed=true`.
  `stale_or_invalid` or unknown integrity blocks both actions, and every
  permitted recovery remains permanently non-reference-eligible;
- downgrade a `doctor` error in prose; show the full output and wait for user
  confirmation;
- zip or share the whole workspace; never include `.env`, tokens, or private
  planning files in an attachment;
- approve delivery, release, gates, or memory entries;
- claim semantic proof, automatic truth checking, hallucination elimination, or
  output-quality improvement;
- expose private local paths, private planning files, tokens, or company
  sensitive material in examples.

## If Unsure

If no workspace path is provided, first classify the request as an existing
workspace or first-time run. Explain that a BriefLoop workspace is the local
folder for this report project. Suggest a safe local folder only when creating a
new workspace, then ask for explicit confirmation before creation. Do not fill
gaps by hand-authoring BriefLoop control records.

If the WorkBuddy conversation is in Chinese, explain the generated handoff in
Chinese as needed, but follow the handoff literally. Preserve command names,
artifact names, and handoff obligations exactly. Do not skip steps, hide
blockers, or claim subagents ran because of translation.

If a user asks to share results, use only BriefLoop-generated delivery or audit
bundles when present. If the workspace has no `output/delivery/` or
`finalize_report.json`, say there is only a draft when
`output/intermediate/audited_brief.md` exists; otherwise
say no draft or delivery exists yet. If any package candidate contains `.env`,
stop and recommend key rotation before sharing anything.
