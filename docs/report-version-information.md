# Version information

The report sidebar shows the selected saved version's author mode, writer engine,
model, reasoning setting and save time. Human edits and imports are labelled
separately; an original writer configuration is a disclosure, not attribution to
that human edit. The old `i` was conversational help, and is now labelled
“对话说明”. Existing task date, period, audience and source-count information stays
available beside the version card.

The backend never consults current workspace/session defaults for this view.
New writer publication receipts bind configuration to version ID and content
hash. Analyst publication uses the actual staged Analyst configuration; automatic
revision uses its writing job, not `role_models.evaluator`. Chat revision uses the
active message's frozen runtime. CLI revisions may use an adjacent runner-owned
conversation binding with an active durable message. Missing bindings stay unknown.
Only backend/model/effort are projected; credentials, commands and logs are excluded.

Legacy versions use the runners' exact publication-ID contracts or an exact
version/hash match in their admitted-source snapshot. Assessment result references
are not writing evidence. Evidence-only children with unchanged prose retain the
parent writer; arbitrary unbound AI children do not inherit one. Missing model or
reasoning settings remain “未记录”; the product cannot claim the host's resolved
model when it was never recorded.

Validation on MBP: frontend suite 227 passed / 3 skipped; related Python tests
44 passed / 1 skipped; build, build:check, design:check and diff checks passed.
Real browser/native GUI acceptance and the corrected DMG Finder view remain pending
manual desktop unlock. No model calls or release freeze were performed for this change.
