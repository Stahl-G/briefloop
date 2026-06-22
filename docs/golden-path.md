# BriefLoop Golden Path

This is the shortest operator path for writers. It does not explain every
control surface. It answers one practical question: from zero, what sequence
gets a brief to delivery without bypassing accountability?

## 0. Before You Start

Confirm that you are in the BriefLoop project and that the `/briefloop` command is
available in Claude Code if you plan to use the Claude writer path.

From the repository directory, verify that the CLI points to the current
checkout:

```bash
which multi-agent-brief
multi-agent-brief version
```

If the version does not match the current repository, refresh the source setup:

```bash
bash scripts/setup.sh
source .venv/bin/activate
multi-agent-brief version
```

If `/briefloop` is not available in Claude Code, run:

```bash
multi-agent-brief claude install --repo-workdir .
```

## First Run Variant: Local Materials, No API Keys

If you only want to try BriefLoop on a few local files, do not configure a search
backend first. Use the smallest path:

1. Use `/briefloop new` to create a workspace.
2. Put a few prepared local text files into `input/sources/`.
3. Use `/briefloop run <workspace>` to create the handoff.
4. Use `/generate-brief <workspace>` to execute the delegated workflow.
5. Use `/briefloop status <workspace>` to see what is blocked.
6. Use `/briefloop deliver <workspace>` to deliver.

For a first run, use 3-5 Markdown or plain-text files. If PDF or DOCX files
are not readable through the current input-governance path, convert them to
text before placing them in `input/sources/`. Do not skip the Claim Ledger,
gates, or reader-final gate for speed.

## 1. `/briefloop new`

Use this to create a new brief workspace.

You will answer questions about:

- who the brief is for;
- what this issue covers;
- what topics matter;
- what output shape you want.

It creates the workspace, basic configuration, and handoff context. It does
not generate the brief and does not approve any long-term preference.

## 2. `/briefloop run <workspace>`

Use this to create or refresh the current runtime handoff.

This prepares the control context and handoff for the runtime. It does not run
the whole pipeline and does not mark stages complete.

To execute the delegated subagent workflow, follow the handoff and use:

```text
/generate-brief <workspace>
```

## 3. Use `/briefloop status <workspace>` Whenever You Are Unsure

`status` is read-only. It answers four things:

- what stage the run is in;
- whether source-trail surfaces are present or stale;
- which approved reader preferences were frozen into this run;
- which gates, feedback, or delivery checks are blocking.

If status says a record may be stale, treat that as an explicit instruction to
run the named command. It is not a failure by itself.

## 4. What To Do When The Run Is Blocked

Start with:

```text
/briefloop status <workspace>
```

Classify the blocker:

| Blocker | What to do |
|---|---|
| Missing artifact | Let the corresponding stage continue, or rerun that stage through the handoff. |
| Fact or source issue | Use feedback, repair, audit, or gate paths. Do not turn it into long-term preference memory. |
| Stable formatting issue | Record it as feedback first; repeated issues should become templates or delivery standards. |
| Reader preference | Write it as guidance, propose it to the Improvement Ledger, and approve it explicitly. |
| Already enforced by the system | Open the linked gate/report instead of duplicating it in memory. |

## 5. `/briefloop feedback <workspace> "..."`

When you review a draft, write the feedback in plain language:

```text
/briefloop feedback <workspace> "This section reads like a news digest. Lead with the impact on our company."
```

Feedback is recorded first. Acting on it requires a later explicit step:

- repair this run;
- create a repair plan;
- mark an issue resolved;
- propose a long-term reader preference;
- approve or revert an Improvement Ledger entry.

Fact problems are not long-term preferences. Stable format requirements should
become templates or delivery standards rather than living forever as memory.

## 6. When Approved Preferences Take Effect

Approving Improvement Ledger guidance does not change a run snapshot that has
already been created.

It takes effect the next time you run:

```text
/briefloop run <workspace>
```

or the equivalent `run` / `start` / `handoff` command that freezes a new
`output/intermediate/improvement_memory_snapshot.md`.

BriefLoop can observe and propose, but only human-approved guidance is remembered,
and it is remembered in a ledger you can inspect and revert.

## 7. `/briefloop deliver <workspace>`

Use this to deliver the final reader files.

Delivery must pass:

- quality gates;
- reader-final gate;
- `state finalize-complete`.

Only then should the reader-facing artifacts under `output/delivery/` be
treated as delivery output:

- `output/delivery/brief.md`
- `output/delivery/<named-brief>.docx`

Audit/control files remain in the workspace, but are not reader deliveries:

- `output/intermediate/claim_ledger.json`
- `output/intermediate/audit_report.json`
- `output/source_appendix.md`

If the reader-final gate fails, do not manually move a bad file. Open
`output/intermediate/finalize_report.json`, fix the residue, and rerun the
proper delivery path.

## Next Real Weekly Brief Test

The next real weekly brief should follow this golden path. Every moment where
you are unsure what button or command to use is a documentation bug to record.
