# Hermes Cron Patterns

Use cron jobs for durable scheduling. Use `delegate_task` only inside a single run.

## Daily Source Cache Prompt

Run daily source cache collection for this BriefLoop workspace.

Workspace: `<workspace>`  
Cache directory: `<workspace>/input/hermes_cache`

Collect source signals, write `YYYY-MM-DD.json`, then report saved item count and source gaps. Daily cache mode ends after cache reporting.

## Weekly Or Monthly Brief Prompt

Run a Hermes-native delegated BriefLoop brief workflow.

Workspace: `<workspace>`  
Repository workdir: `<repo>`

Use the `multi-agent-brief-hermes` skill. Read `output/intermediate/audience_profile_snapshot.md` at run start and pass a concise taste summary to delegated roles. Run doctor, then use Hermes `delegate_task` children for scout, screener, claim-ledger, analyst, editor, and auditor. After audit readiness, run gates/state controls, then finalize; report delivery only when the Store-native status projection (`briefloop status --workspace <workspace> --json`) reports `delivered=true` for the current run. Run optional provenance projection only when an audit/debug graph is useful.

## Runtime Notes

- Attach this skill to each cron job when the Hermes UI or CLI supports skill selection.
- Use the repository root as workdir so commands resolve correctly.
- Pin the Hermes profile when the profile already exists.
- Keep daily cache jobs separate from weekly/monthly brief generation jobs.
