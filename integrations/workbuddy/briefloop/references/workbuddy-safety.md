# WorkBuddy Safety

WorkBuddy is a local operator shell around BriefLoop. It is not a new BriefLoop
authority layer.

## Do

- classify missing workspace paths as existing workspace or first-time run,
  then confirm the folder path before any creation;
- on Windows, bind one absolute `$BriefLoop` path in PowerShell and reuse it for
  doctor, run, secrets import, and diagnose;
- treat local/no live web search as the first-run default;
- when live web search is requested, use Tavily first and verify
  `TAVILY_API_KEY` without displaying the key value;
- import Tavily only from a user-confirmed private `$SecretSource` file verified
  with `Test-Path -LiteralPath $SecretSource -PathType Leaf`;
- use `--runtime codebuddy` for full workflow handoff;
- invoke the matching project role subagent by exact name in the active
  CodeBuddy/WorkBuddy host;
- run deterministic BriefLoop CLI commands when the user approves;
- print a machine-fact Run Card after key commands, role returns, repairs,
  gates, finalize attempts, quality summaries, and bundle/export requests;
- before each stage or role-owned artifact action, re-read the relevant
  `agent_handoff.md` / `agent_handoff.json` step;
- after every start, CLI command, role return, or interruption, re-read the
  handoff, run diagnose, and follow only the current handoff/diagnose action;
- keep role delegation claims literal;
- explain Quality Panel as an audit attachment.

## Do Not

- guess a workspace from the repository path;
- claim setup is incomplete only because optional search-provider keys are
  empty;
- mix Windows PowerShell with `bash`, `which`, `command -v`, `export`,
  `/c/Users/...`, `source .venv/bin/activate`, or `bash scripts/setup.sh`;
- mutate PATH or inject an API key into one command instead of using workspace
  secrets import;
- treat the environment variable itself as a secrets-import file source, or
  continue when the private `$SecretSource` file is absent;
- ask the user to choose among all search providers unless they request an
  alternative to Tavily;
- direct-edit control files or frozen artifacts;
- say specialist subagents ran unless WorkBuddy actually delegated them;
- silently fall back to `--runtime operator` for a full workflow;
- hand-author BriefLoop workflow JSON artifacts when role subagents are
  unavailable;
- say an exact Analyst/Auditor role returned without a host-visible invocation
  and return in the current handoff step, or say its stage/audit passed without
  current deterministic transaction/verdict truth; matching artifacts, stale
  events, manual files, or prior transactions are insufficient;
- treat `delivery_truth.valid=true` as proof that delivery occurred;
- say `delivered`, `delivery complete`, or `交付完成` unless WorkBuddy diagnose
  reports current-bound `event_truth.delivery_succeeded=true`;
- describe `delivery_bundle_prepared` or `delivery_draft_created` as delivered;
- infer recovery progress from `run_integrity` instead of following
  `recovery_state` and `recovery_action`;
- reconstruct next action, gate, finalize, or delivery truth from raw workflow
  state, event log, Registry, timestamps, or file existence; raw controls are
  audit evidence only;
- downgrade a `doctor` error in prose, or use `request_human_review`, user
  confirmation, or a standalone pass from another shell/environment to turn it
  into pass; fix the context and rerun doctor with the same `$BriefLoop`;
  diagnose's `doctor.status=not_run_read_only` cannot clear or route around the
  failure, and its completion action must not be followed;
- let `briefloop-formatter` run shell/CLI, convert Markdown to DOCX, write reader
  delivery artifacts, or claim reader-clean, gate/finalize success, or delivery;
- rename or describe hand-written Markdown/DOCX outside formal finalize as a
  formal delivery; it is `draft/manual/unverified`, and reader residue must be
  reported and routed through deterministic repair/finalize;
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

If a user asks to execute delivery, require `delivery_truth.valid=true`. After
the action, report delivered only for `delivery_event=delivery_succeeded`;
report local prepared and Gmail draft outcomes literally. If no valid reader
bundle exists, say there is only a role draft when
`output/intermediate/audited_brief.md` exists; otherwise
say no draft or delivery exists yet. If any package candidate contains `.env`,
stop and recommend key rotation before sharing anything.
