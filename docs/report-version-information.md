# Version information

The report sidebar shows the selected saved version's author mode, writer engine,
model, reasoning setting and save time. Human edits and imports are labelled
separately; an original writer configuration is a disclosure, not attribution to
that human edit. The old `i` was conversational help, and is now labelled
“对话说明”. Existing task date, period, audience and source-count information stays
available beside the version card.

The backend never consults current workspace/session defaults for this view.
New writer publication receipts are inserted in the same SQLite transaction as
the version itself. Only the insertion winner can own that version; identical
retries and no-op revisions cannot claim or change it. An explicit unknown
receipt is final too. Earlier non-atomic receipts are not treated as proof.

Native Analyst attribution requires the validated accepted-copy operation and
its exact job/session/message/revision identity, recorded before the outer draft
becomes visible. Plain quick-writer output is bound where the trusted transport
copies the exact assistant message into response.txt, before deterministic fence
and source-alias conversion. The copied-output hash checks integrity within that
explicit relationship; it never discovers an author among unrelated jobs.

A restored general-host draft retains only the writing job's frozen request.
The mutable current conversation marker cannot add a later message's host-confirmed
model or effort. Native chat revisions bind their active message at publication.
CLI revisions without trusted runner context stay unknown.
Only backend/model/effort are projected; credentials, commands and logs are excluded.

Legacy versions without atomic publication receipts remain unknown. Neither a
derived filename nor a matching generated-source snapshot establishes who won
publication. Evidence-only children explicitly copy their unchanged parent's
writer configuration at insertion; they do not acquire the evidence worker's
configuration. Requested and host-confirmed fields remain distinct.

Browsing summaries carry a bounded execution-metadata revision. A changed
revision refreshes the selected version's metadata separately from its body;
unsaved editor contents are preserved. Both body caches compare this revision,
so a same-hash cached draft cannot indefinitely show an older attribution.

Validation on MBP: frontend suite 227 passed / 3 skipped; related Python tests
61 passed; build, build:check, design:check and diff checks passed.
The integrated frontend suite passed 246 tests (3 skipped), and all four exact-head
CI jobs passed at 991b7f31de241d2a3abbe2fdc9db9aca6fd42eaa. Native GUI acceptance
and the corrected DMG Finder view remain unverified because the computer-control
tool is unavailable. This is not evidence of a locked desktop. No model calls were
performed for this change. Release freeze and packaged checks are recorded separately.

Independent read-only review of ed59ac41 found seven attribution/display issues.
The subsequent fix covers admitted-copy identity, no-op preservation, rejecting CLI
marker inference, native turn-ID binding, ambiguous legacy jobs, real random-ID
fallback, and requested versus confirmed fields. A fresh review and exact-head CI
are tracked separately; the first review is not a clean verdict for the fix.

Cloud correction after the 4f2466eb re-review replaces post-publication attribution
with atomic ownership, rejects later-message restoration and snapshot inference,
binds quick-writer transport output, and refreshes same-body metadata caches.
Native copy integrity includes the full normalized draft fingerprint, so a
same-body/different-metadata file cannot acquire the newer copy's author.
Focused race, rollback, no-op, restore, quick-writer and cache regressions pass;
release CI and Windows/native GUI acceptance remain separate gates.
