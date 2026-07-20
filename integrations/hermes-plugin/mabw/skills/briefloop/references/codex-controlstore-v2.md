# Codex ControlStore v2 Runtime

Use this path only when `briefloop.db` is the workspace authority and the
selected runtime is `codex`.

1. Run `briefloop run --workspace <workspace> --runtime codex`.
2. Read the returned `CoreRunNextAction`. Do not infer another stage, role, or
   deterministic effect from files, prompts, or memory.
3. For a role action, run `briefloop runtime invocation-start --workspace
   <workspace>` before producing any role output. The resulting
   `RoleTaskEnvelope` is the only execution contract.
4. When the envelope says `execute_in_current_session`, the current Codex main
   session performs only that named role task. When it says
   `delegate_exact_role`, delegate only that exact role. Never fall back between
   the two paths.
5. The executing role writes only the allowed files in its invocation scratch
   directory. The root host accepts them through the named deterministic
   service. A later role always requires a new invocation and envelope.
6. For deterministic actions, the root host applies the named transaction; it
   does not delegate merely because a role exists.
7. For human decisions, require the exact typed human request. A chat reply or
   CLI flag is not approval or delivery authority.

For `role_topology=single_session`, one shared Codex context executes separate
recorded role invocations in the strict stage sequence. This is
stage-separated self-review, not independent review. The session must not draft future stages
before their invocation-start Receipt exists.

SQLite receipts and ledger records are the sole runtime authority. Config and
sources are read only during fresh initialization and frozen into Store
bindings. JSON, JSONL, Markdown, HTML, status, handoff, finalize and Quality
Panel files are projections only and are never read back for legality.

Never edit `briefloop.db`, frozen artifacts, receipts, projection files or
another invocation's scratch. A JSON-only workspace is unsupported; do not
migrate it, import it, dual-write it or fall back to it.
