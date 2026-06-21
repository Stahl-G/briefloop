# Evidence Span Registry

The Evidence Span Registry is an optional experimental control surface on
`main` after the v0.9.1 release. It binds declared evidence spans to durable
workspace source bytes so later support-record surfaces can consume hashable
evidence locations.

It is not a support-sufficiency gate, not semantic proof, and not a
Claim-Support Matrix.

## Artifact

```text
output/intermediate/evidence_span_registry.json
```

The artifact is optional. If it is absent, normal workflow execution, finalize,
delivery, and archive behavior continue unchanged.

When it is present, BriefLoop validates the registry in layers:

1. Payload schema and span identity.
2. Source-pack byte binding.
3. Run archive hash projection.
4. Reader-safe Source Appendix summary plus a separate audit trace copy.

## What Python Checks

Python validates only machine-checkable facts:

- `schema_version`, source IDs, span IDs, span roles, and raw-excerpt hashes.
- `source_path` is workspace-relative and under `input/sources/`.
- Source paths do not escape the workspace through absolute paths, parent
  traversal, or symlinked source roots.
- `raw_excerpt` appears in the source bytes.
- Optional `char_start` / `char_end` offsets exactly match the raw excerpt.
- JSON sources use top-level string `content` when present; otherwise raw JSON
  text is used.
- Archived runs preserve registry hashes, source file hashes, span IDs,
  raw-excerpt hashes, offsets, and archived source-pack paths.

Python does not judge whether the span semantically supports a claim or atom.

## Source Appendix Trace View

During finalize, if the registry is present and valid:

- the reader-facing Source Appendix may show source-level span counts and role
  summaries;
- raw span details are written only to
  `output/source_appendix_trace.md`;
- `output/source_appendix_trace.md` is an audit copy and is not copied into
  `output/delivery/`.

Reader-facing brief output must not expose raw excerpts, span IDs, source IDs,
source paths, atom IDs, local paths, or support-sufficiency claims.

## What This Does Not Claim

Do not use the Evidence Span Registry to claim:

- BriefLoop proves truth.
- A source semantically supports every claim.
- A report is automatically ready to send.
- Evidence Span Registry by itself decides Claim-Support Matrix rows.
- support sufficiency has been decided.

It provides span-level traceability and archive reproducibility. Current
Claim-Support Matrix controls are separate experimental support-record surfaces;
they can validate and project explicit support records, but still do not assess
semantic support or decide release eligibility.
