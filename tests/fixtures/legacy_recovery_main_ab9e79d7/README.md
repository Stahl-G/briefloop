# Legacy Recovery Fixtures

These public-safe control-record snapshots were produced by real transactions
from `main` commit `ab9e79d77e73bbe48dd1ab6a42bcc5d5196de905` before the
versioned owner-revision contract existed.

Scenarios:

- `clean-repair`: normal editor repair start and repair complete on a clean run.
- `contaminated-repair`: a frozen editor artifact change, repair start, and
  repair complete.
- `supersede`: a frozen editor artifact change followed by editor
  `supersede-stage`.

The fixtures retain the old writer's workflow records and relevant transaction
events without field changes. Unrelated events are omitted, while runtime
manifest and artifact registry are reduced to the schema/run identity consumed
by this evaluator. Opaque UUID-style event and transaction values are replaced
consistently with descriptive public-safe IDs; their references and ordering
are unchanged. Tests copy the snapshots to a temporary workspace and evaluate
them with the current recovery producer. Do not hand-normalize the legacy
transaction event or workflow field structure.
