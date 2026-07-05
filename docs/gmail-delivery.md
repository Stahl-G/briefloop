# Gmail Delivery

BriefLoop can create a Gmail draft or explicitly send a Gmail message through
the optional [gws](https://github.com/googleworkspace/cli) CLI.

This is an experimental connector. It does not approve delivery, authorize
publication, prove semantic truth, or attach audit/control files. Sending mail
requires an explicit `--channel send` command from the operator.

## Prerequisites

Install and authenticate `gws` outside BriefLoop:

```bash
gws auth setup
gws auth login
gws auth status
```

`gws` is a Google Workspace CLI, not a Google-officially supported product.
BriefLoop treats it as an optional local connector, not a core runtime
dependency.

## Create A Draft

After finalize and delivery checks pass:

```bash
briefloop deliver \
  --workspace <workspace> \
  --target gmail \
  --channel draft \
  --recipient someone@example.com
```

## Send A Message

To send the delivery attachment directly, use `--channel send` explicitly:

```bash
briefloop deliver \
  --workspace <workspace> \
  --target gmail \
  --channel send \
  --recipient someone@example.com
```

Use `draft` when the recipient or message content still needs review. Use
`send` only when the operator intends the external email side effect.

Optional subject and body work for both channels:

```bash
briefloop deliver \
  --workspace <workspace> \
  --target gmail \
  --channel send \
  --recipient someone@example.com \
  --subject "Weekly brief for review" \
  --body "Please review the attached BriefLoop delivery."
```

BriefLoop attaches `output/delivery/<named>.docx` when present, otherwise it
attaches `output/delivery/brief.md`. It does not attach `claim_ledger.json`,
`audit_report.json`, `source_appendix.md`, Quality Panel files, runtime state,
or other audit/control artifacts.

## Event Boundary

The delivery event log records:

- `delivery_attempted`
- `delivery_draft_created` when the Gmail draft is created
- `delivery_succeeded` when a Gmail message is sent
- `delivery_failed` when draft creation or sending fails

Event metadata records only whether a recipient was present and the recipient
SHA-256. It does not record the email address, subject, or body.

If a Gmail draft is created or message is sent but the local event cannot be
recorded, the command returns failure and tells the operator to inspect Gmail
before retrying. This avoids marking an unrecorded external side effect as a
clean success.
