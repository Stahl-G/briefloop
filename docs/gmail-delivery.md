# Gmail Draft Delivery

BriefLoop can create a Gmail draft through the optional
[gws](https://github.com/googleworkspace/cli) CLI.

This is an experimental connector. It creates a draft only; it does not send
mail, approve delivery, authorize publication, or attach audit/control files.

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

Optional subject and body:

```bash
briefloop deliver \
  --workspace <workspace> \
  --target gmail \
  --channel draft \
  --recipient someone@example.com \
  --subject "Weekly brief for review" \
  --body "Please review the attached BriefLoop delivery draft."
```

BriefLoop attaches `output/delivery/<named>.docx` when present, otherwise it
attaches `output/delivery/brief.md`. It does not attach `claim_ledger.json`,
`audit_report.json`, `source_appendix.md`, Quality Panel files, runtime state,
or other audit/control artifacts.

## Event Boundary

The delivery event log records:

- `delivery_attempted`
- `delivery_draft_created` when the Gmail draft is created
- `delivery_failed` when draft creation fails

Event metadata records only whether a recipient was present and the recipient
SHA-256. It does not record the email address, subject, or body.

Direct send is intentionally not supported. A future send path would require a
separate human-approval record bound to the recipient, subject, and delivery
artifact hash.
