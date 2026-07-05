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
- use `--runtime operator` for handoff;
- run deterministic BriefLoop CLI commands when the user approves;
- before each stage or role-owned artifact action, re-read the relevant
  `agent_handoff.md` / `agent_handoff.json` step;
- after each CLI command, report only deterministic progress visible in status,
  workflow state, event log, or generated artifacts;
- when support material is needed, run
  `briefloop workbuddy support-bundle --workspace <workspace> --output <outside-workspace-dir>`;
- keep role delegation claims literal;
- explain Quality Panel as an operator/audit attachment.

## Do Not

- guess a workspace from the repository path;
- claim setup is incomplete only because optional search-provider keys are
  empty;
- ask the user to choose among all search providers unless they request an
  alternative to Tavily;
- direct-edit control files or frozen artifacts;
- say specialist subagents ran unless WorkBuddy actually delegated them;
- say `Analyst 已经分析完成` or `Auditor 已通过` unless the matching artifact,
  event, transaction, or status output exists;
- approve delivery, release, gates, or memory entries;
- claim semantic proof, automatic truth checking, hallucination elimination, or
  output-quality improvement;
- expose private local paths, private planning files, tokens, or company
  sensitive material in examples.
- zip, upload, or share the whole workspace; use a finalized delivery/audit
  bundle or `workbuddy support-bundle` instead.

## If Unsure

If no workspace path is provided, first classify the request as an existing
workspace or first-time run. Explain that a BriefLoop workspace is the local
folder for this report project. Suggest a safe local folder only when creating a
new workspace, then ask for explicit confirmation before creation. Do not fill
gaps by hand-authoring BriefLoop control records.

If the WorkBuddy conversation is in Chinese, explain the operator handoff in
Chinese as needed, but follow the generated English handoff literally. Preserve
command names, artifact names, and handoff obligations exactly. Do not skip
steps, hide blockers, or claim subagents ran because of translation.
