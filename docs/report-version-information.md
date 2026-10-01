# Version information

The report sidebar shows the selected saved version's author mode, writer engine,
model, reasoning setting and save time. Human edits and imports are labelled
separately; an original writer configuration is a disclosure, not attribution to
that human edit. The old `i` was conversational help, and is now labelled
“对话说明”. Existing task date, period, audience and source-count information stays
available beside the version card.

The backend never consults current workspace/session defaults for this view.
New writer publication receipts bind configuration to the newly admitted version
ID and content hash. Native Analyst attribution requires the actual accepted-copy
operation and its job/session/message/revision identity; a matching hash alone
is never author proof. No-op revisions never create a writer receipt. Analyst publication uses the actual staged Analyst configuration; automatic
revision uses its writing job, not `role_models.evaluator`. Chat revision uses the
active message's frozen runtime. CLI revisions lack trusted runner context and stay unknown; adjacent conversation
files do not establish authorship. Missing bindings stay unknown.
Only backend/model/effort are projected; credentials, commands and logs are excluded.

Legacy versions gather the runners' publication-ID and admitted-source snapshot
relationships. Only a unique job identity is accepted, including real random
brief IDs; multiple candidate jobs remain unknown. Assessment result references
are not writing evidence. Evidence-only children with unchanged prose retain the
parent writer; arbitrary unbound AI children do not inherit one. Missing model or
reasoning settings remain “未记录”. Requested settings are explicitly labelled
“请求”. Host-confirmed fields require a message-bound response with a known source
and are labelled “宿主确认”; the frozen request stays intact. Default/none requests
mean host defaults, not proof that reasoning was disabled.

Validation on MBP: frontend suite 227 passed / 3 skipped; related Python tests
61 passed; build, build:check, design:check and diff checks passed.
Real browser/native GUI acceptance and the corrected DMG Finder view remain pending
manual desktop unlock. No model calls or release freeze were performed for this change.

Independent read-only review of ed59ac41 found seven attribution/display issues.
The subsequent fix covers admitted-copy identity, no-op preservation, rejecting CLI
marker inference, native turn-ID binding, ambiguous legacy jobs, real random-ID
fallback, and requested versus confirmed fields. A fresh review and exact-head CI
are tracked separately; the first review is not a clean verdict for the fix.
