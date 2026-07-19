# Codex ControlStore v2 Runtime

Use this path only when `briefloop.db` is the workspace authority and the
selected runtime is `codex`.

1. Run `briefloop run --workspace <workspace> --runtime codex`.
2. Read the returned `CoreRunNextAction`. Do not infer another stage, role, or
   deterministic effect from files, prompts, or memory.
3. For a delegate action, run `briefloop runtime invocation-start --workspace
   <workspace>`, then invoke exactly the role named by the resulting
   `RoleTaskEnvelope`.
4. The role writes only the allowed files in its invocation scratch directory.
   The root host accepts them through the named deterministic service.
5. For deterministic actions, the root host applies the named transaction; it
   does not delegate merely because a role exists.
6. For human decisions, require the exact typed human request. A chat reply or
   CLI flag is not approval or delivery authority.

SQLite receipts and ledger records are the sole runtime authority. Config and
sources are read only during fresh initialization and frozen into Store
bindings. JSON, JSONL, Markdown, HTML, status, handoff, finalize and Quality
Panel files are projections only and are never read back for legality.

Never edit `briefloop.db`, frozen artifacts, receipts, projection files or
another invocation's scratch. A JSON-only workspace is unsupported; do not
migrate it, import it, dual-write it or fall back to it.
