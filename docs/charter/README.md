# MABW Charter

This charter defines the architecture and operating disciplines for Multi-Agent
Brief Workflow. It is an arbitration manual for humans and development agents
when a design, implementation, runtime behavior, or public claim reaches a
boundary dispute. Public capability claims still depend on implemented code,
tests, docs, and the support matrix.

## MABW Architecture Charters

### 1. Smart agents have no authority; authority is deterministic; effects require human approval; approved effects leave records.

LLMs and agents may understand, suggest, split, summarize, and draft, but they
must not directly take effect. Persistent state writes, workflow advancement,
evidence freezing, and gate passage belong to deterministic control surfaces.
Anything that changes future runs requires human confirmation and a record.

Decision test: if a proposal lets an agent directly write persistent state,
advance a stage, freeze evidence, pass a gate, approve delivery, or modify
content that future runs will read, treat it as overreach by default. Move it to
a deterministic CLI, validator, or transaction, or make it a recorded effect
after human approval.

### 2. If a machine can enforce it, do not leave it to memory.

Schema, validators, gates, transactions, event logs, and tests are the reliable
parts of the system. Rules that live only in prompts, handoffs, oral guidance,
or memory will drift in real runs. If a rule can be captured by deterministic
checks, it should not remain guidance.

Decision test: if a rule can be checked through a schema field, artifact hash,
path existence, status transition, gate result, event presence, reader-residue
pattern, or test, it must move into schema, validator, gate, transaction, or
test. If it depends on semantic judgment, an agent may propose, explain, or
summarize it, but must not present it as a mandatory control-plane obligation;
it belongs in a typed finding, candidate, human review, or approved record.
Mixed rules must be split: the checkable part goes into the machine layer, and
the semantic remainder stays in the agent or human layer.

### 3. One field has one writer.

Every control-plane field must have exactly one authoritative writer. Python
writes state, ledgers, events, hashes, gates, and deterministic projections.
Agents draft content. Humans approve preferences and delivery. Multiple modules
"helpfully" updating the same field breaks auditability, rollback, and
attribution.

Decision test: if two modules, commands, agents, or projections want to write
the same field, assign exactly one authoritative writer. The other side may
read, request a transaction, or write its own derived artifact. Any
"backfill", "reinitialize", or "sync while we are here" implementation for the
same field should be treated as a likely single-writer violation.

### 4. A source is not support; traceability is not proof.

A source record only shows when, where, and through which step a claim entered
the workflow. It does not prove that the source semantically supports the claim.
Search plans, source candidates, model summaries, and search snippets are
discovery material, not factual evidence. Evidence support must be separated by
support strength, source tier, and freshness. Fresh does not mean authoritative;
a link does not mean proven. A material claim may enter reader-facing delivery
only if it satisfies the gate for its claim class, scope, support strength, and
evidence contract; otherwise it must be downgraded, blocked, or sent to human
review.

Decision test: if a claim is supported only by a link, search plan, source
candidate, search summary, or model summary, do not mark it as supported. If a
claim's qualifiers, numbers, timing, attribution, scope, or freshness exceed
the evidence contract, downgrade it, block it, or send it to human review. Do
not use "traceable" as a substitute for "supported".

### 5. Frozen artifacts are not rewritten; gaps are not hidden.

Once a deterministic control surface freezes an artifact, it must not be
silently overwritten. Legal change must appear as a new revision, new artifact,
new event, explicit supersede or revert, or contamination record. Do not rewrite
a frozen artifact in place so that it looks as if it was always correct. Even
the single authoritative writer may not go back and edit frozen history.
Missing artifacts, unaudited evidence, failed gates, failed transactions,
rejected claims, and human-decision gaps must become findings, blockers,
contamination, human-review records, or events. They must not be hidden as prose
caveats or disappear from the narrative.

Decision test: if an implementation needs to change a frozen artifact, it must
create a new revision, artifact, event, supersede, revert, or contamination
record instead of overwriting in place. If a negative result cannot be found
three days later through grep, schema query, event log, or run archive, that is
not merely poor recordkeeping; it violates this charter.

### 6. Conflicts are resolved by precedence, not persuasion.

When user requests, agent suggestions, audience preferences, improvement memory,
repair plans, gates, schemas, and contracts conflict, the system does not ask a
model to explain which one sounds more reasonable. Declared precedence decides.
Fact contracts and deterministic gates outrank style preferences. Current-run
repair outranks cross-run taste memory. Control-plane duties cannot be skipped
by a prompt, handoff, or temporary user request. Brief objective, reader,
time window, source policy, and delivery standard are the run direction. Agents
may suggest changes, but must not silently change direction during a run.
Direction changes must become an explicit user decision, config change, or new
run.

Decision test: if two instructions or artifacts conflict, do not let the model
adjudicate by persuasion. Check the declared precedence. If the conflict touches
brief objective, reader, time window, source policy, or delivery standard, record
it as an explicit user decision, config change, or new run. Do not let it drift
silently inside the current run.

### 7. Cross-cutting invariants are closed structurally, not path by path.

A cross-cutting invariant — run integrity, staleness, freeze/supersede
semantics, repair routing, delivery truth, gate authority — holds only if every
writer and recomputer of the affected state upholds it. Enforcing it path by
path decays into repeated fix rounds: each recompute path silently drops the
invariant, and review becomes the enumeration mechanism. The merge unit for
such a change is the whole invariant lifecycle, not a file or layer slice.
Authority for a cross-cutting fact lives in exactly one record that
recomputation reads. Control files load through one shared fail-closed helper
rather than per-consumer readers. Operator command flows have one source of
truth, swept to every contract, adapter, guidance emitter, and string-asserting
test in the same change.

Decision test: before implementing, enumerate the state × path matrix — every
writer and recomputer of the affected state, the expected outcome per cell, and
one test per row. If the paths cannot be enumerated, redesign so enumeration is
unnecessary by moving the fact into one authoritative record. If an
implementation stores a cross-cutting fact in stage metadata or any structure
that state recomputation rebuilds, treat it as a design error, not a
preservation chore. Deferral is legal only for a named propagation gap such as
a consumer not yet migrated; an input accepted without validation or a path
that skips the invariant must close in the same change. In review, a second
finding of the same shape means stop patching paths and fix the structure; a
third means the change is at the wrong altitude and must be redesigned before
further fix commits.

## MABW Operating Disciplines

### Product Spine: speed must not steal accountability.

MABW may become faster by reusing frozen evidence, reducing repeated inference,
improving onboarding, and parallelizing independent work. It must not become
faster by removing ledgers, gates, human approvals, events, snapshots, archives,
or human delivery. Lightweight paths may lighten the shell, not remove the
spine.

### Public Claims Discipline: do not say what artifacts cannot support.

Public docs, README text, release notes, demos, paper drafts, and launch posts
must not claim more than current artifacts can support. If it is unmeasured, say
NOT MEASURED. If the system only traces, say traceability rather than proof. Do
not package human-discovered errors as model self-verification. If a failure
case changes the capability boundary, make it part of the public system
evidence.

### Data Boundary: private facts must not justify public mechanisms.

MABW may distill patterns, failure types, control-plane rules, and test shapes
from real workflows. Private business facts, customer facts, employer material,
IR content, and non-public information must not enter the repo, fixtures, public
demos, or unapproved external APIs. Public mechanisms must be reproducible from
public-safe or synthetic material.
