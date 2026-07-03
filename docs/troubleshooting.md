# Troubleshooting

This guide covers common first-user blocks. BriefLoop is designed to stop when
control files, checks, or delivery state are not ready. A block is usually a
signal to inspect and repair, not a reason to edit control files by hand.

## First Command To Run

```bash
briefloop status --workspace <workspace>
```

For machine-readable details:

```bash
briefloop status --workspace <workspace> --json
```

Status is read-only. It can show the current stage, missing files, failed
checks, repair state, delivery state, and recommended next actions.

## Gate Blocked

Symptoms:

- status mentions a failed check;
- delivery is unavailable;
- a stage cannot be completed.

What to do:

1. Open the referenced check or quality summary.
2. Find whether the issue is a missing source, unsupported claim, stale source,
   reader-clean problem, or incomplete artifact.
3. Repair through the workflow.
4. Rerun status.

Do not edit frozen intermediate files to make a check look green.

## Missing Sources

Symptoms:

- claims are missing source context;
- source files do not appear in status or audit output;
- a source pack or appendix is incomplete.

What to check:

```text
input/sources/
sources.yaml
output/source_appendix.md
```

For document-review work, make sure sources were registered with `briefloop
extract` and that binary inputs have a supported text representation before
asking agents to use their contents as evidence.

## Reader-Clean Failure

Symptoms:

- delivery is blocked;
- final output still contains internal markers, claim IDs, source IDs, local
  paths, or repair instructions.

What to do:

1. Open the finalize or delivery report named by status.
2. Repair the reader-facing text.
3. Run the deterministic finalize or delivery path again.

Do not move files manually into `output/delivery/` to bypass reader-clean
checks.

## Stale Or Frozen Artifact Issue

Symptoms:

- status mentions frozen-artifact integrity;
- an artifact is marked stale, contaminated, or invalid;
- a file was changed after its producing stage completed.

What to do:

1. Treat the artifact as untrusted until the workflow repairs or regenerates it.
2. Use the named transaction or repair path.
3. Keep the old event trail intact.

Frozen artifacts are not meant to be overwritten quietly. A real change should
leave a new event, revision, repair record, or contamination record.

## No API Key Or No Runtime

The deterministic demo does not need an API key:

```bash
bash scripts/demo.sh
```

A real run may require a configured agent runtime or model access depending on
how you operate the generated handoff.

If you only want to inspect the repository mechanics:

```bash
python3 scripts/check_launch_smoke.py
```

If `briefloop run --workspace <workspace>` succeeds, it means the runtime
handoff was created. It does not mean agents have completed the brief.

## Active Repair

Symptoms:

- status says a repair is active;
- the current stage has moved back to an owner stage.

What to do:

1. Inspect status.
2. Complete or cancel the repair through the supported command path.
3. Do not continue unrelated stages while a repair is active.

Repair state is part of the control trail. Editing it directly makes later
status and delivery decisions untrustworthy.

## When To Start A New Run Or Workspace

Start fresh when:

- the audience changes;
- the objective changes;
- the source policy changes;
- the time window changes materially;
- the workspace has a contamination record you do not intend to repair;
- you are trying to use feedback as a substitute for evidence.

Starting fresh is better than making the old run appear cleaner than it was.

## What Not To Do

Do not:

- edit `workflow_state.json`, `artifact_registry.json`, `runtime_manifest.json`,
  or `event_log.jsonl`;
- edit gate reports to make status pass;
- copy files into delivery after delivery was blocked;
- treat feedback, instructions, or search plans as evidence;
- describe a blocked run as ready to send.
