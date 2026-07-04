# Quickstart

This quickstart is for WorkBuddy users operating BriefLoop locally. The Skill
bundle is source-clone-only in this release; Python wheel/sdist package installs
do not include the WorkBuddy files yet.

## 1. Confirm The Active CLI

Run:

```bash
BRIEFLOOP_CLI="$(command -v briefloop || command -v multi-agent-brief)"
test -n "$BRIEFLOOP_CLI"
"$BRIEFLOOP_CLI" version
```

Report the resolved command path and version before making changes. If neither
command exists, stop and ask the user to install BriefLoop or open the source
checkout.

## 2. Create A Workspace

Use one product entry:

```bash
briefloop new industry-weekly <workspace>
briefloop new management-monthly <workspace>
briefloop new document-review <workspace>
briefloop new solar-periodic <workspace>
```

`industry-weekly`, `management-monthly`, and `document-review` are the baseline
supported product entries. `solar-periodic` is experimental.

## 3. Run Operator Handoff

Run:

```bash
multi-agent-brief run --workspace <workspace> --runtime operator
```

Then inspect:

```bash
multi-agent-brief status --workspace <workspace>
multi-agent-brief state check --workspace <workspace>
```

## 4. Summarize Quality

When the workspace has enough artifacts to summarize:

```bash
multi-agent-brief quality summarize --workspace <workspace>
```

Open `output/intermediate/quality_panel.html` for the static operator view.
Quality Panel is traceability and process accountability, not semantic proof,
delivery approval, or release authorization.
