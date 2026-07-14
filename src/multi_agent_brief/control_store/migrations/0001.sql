BEGIN IMMEDIATE;

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY CHECK(typeof(version) = 'integer' AND version > 0),
    name TEXT NOT NULL UNIQUE CHECK(typeof(name) = 'text' AND length(name) > 0)
);

INSERT INTO schema_migrations(version, name) VALUES (1, '0001');

CREATE TABLE workspaces (
    workspace_id TEXT PRIMARY KEY
        CHECK(typeof(workspace_id) = 'text' AND length(workspace_id) > 0),
    revision INTEGER NOT NULL DEFAULT 0
        CHECK(typeof(revision) = 'integer' AND revision >= 0)
);

CREATE TABLE runs (
    run_id TEXT PRIMARY KEY
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    workspace_id TEXT NOT NULL
        CHECK(typeof(workspace_id) = 'text' AND length(workspace_id) > 0),
    schema_version TEXT NOT NULL
        CHECK(
            typeof(schema_version) = 'text'
            AND schema_version = 'briefloop.run_identity.v2'
        ),
    runtime TEXT NOT NULL CHECK(typeof(runtime) = 'text' AND length(runtime) > 0),
    created_at TEXT NOT NULL
        CHECK(typeof(created_at) = 'text' AND length(created_at) > 0),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    UNIQUE(workspace_id, run_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transactions (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    transaction_id TEXT NOT NULL
        CHECK(typeof(transaction_id) = 'text' AND length(transaction_id) > 0),
    workspace_id TEXT NOT NULL
        CHECK(typeof(workspace_id) = 'text' AND length(workspace_id) > 0),
    schema_version TEXT NOT NULL
        CHECK(
            typeof(schema_version) = 'text'
            AND schema_version = 'briefloop.transaction_receipt.v2'
        ),
    transaction_type TEXT NOT NULL
        CHECK(typeof(transaction_type) = 'text' AND length(transaction_type) > 0),
    prior_revision INTEGER NOT NULL
        CHECK(typeof(prior_revision) = 'integer' AND prior_revision >= 0),
    committed_revision INTEGER NOT NULL
        CHECK(
            typeof(committed_revision) = 'integer'
            AND committed_revision = prior_revision + 1
        ),
    committed_at TEXT NOT NULL
        CHECK(typeof(committed_at) = 'text' AND length(committed_at) > 0),
    projection_status TEXT NOT NULL CHECK(projection_status = 'stale'),
    fingerprint TEXT NOT NULL
        CHECK(
            typeof(fingerprint) = 'text'
            AND length(fingerprint) = 64
            AND fingerprint NOT GLOB '*[^0-9a-f]*'
        ),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, transaction_id),
    UNIQUE(workspace_id, committed_revision),
    FOREIGN KEY(workspace_id, run_id) REFERENCES runs(workspace_id, run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE stage_states (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    stage_id TEXT NOT NULL
        CHECK(typeof(stage_id) = 'text' AND length(stage_id) > 0),
    schema_version TEXT NOT NULL
        CHECK(
            typeof(schema_version) = 'text'
            AND schema_version = 'briefloop.stage_state.v2'
        ),
    status TEXT NOT NULL CHECK(typeof(status) = 'text' AND length(status) > 0),
    revision INTEGER NOT NULL
        CHECK(typeof(revision) = 'integer' AND revision >= 0),
    updated_at TEXT NOT NULL
        CHECK(typeof(updated_at) = 'text' AND length(updated_at) > 0),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, stage_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE agent_invocations (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    invocation_id TEXT NOT NULL
        CHECK(typeof(invocation_id) = 'text' AND length(invocation_id) > 0),
    schema_version TEXT NOT NULL
        CHECK(
            typeof(schema_version) = 'text'
            AND schema_version = 'briefloop.invocation.v2'
        ),
    role_id TEXT NOT NULL CHECK(typeof(role_id) = 'text' AND length(role_id) > 0),
    runtime TEXT NOT NULL CHECK(typeof(runtime) = 'text' AND length(runtime) > 0),
    status TEXT NOT NULL CHECK(typeof(status) = 'text' AND length(status) > 0),
    started_at TEXT NOT NULL
        CHECK(typeof(started_at) = 'text' AND length(started_at) > 0),
    completed_at TEXT CHECK(
        completed_at IS NULL
        OR (typeof(completed_at) = 'text' AND length(completed_at) > 0)
    ),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, invocation_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE artifacts (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    artifact_id TEXT NOT NULL
        CHECK(typeof(artifact_id) = 'text' AND length(artifact_id) > 0),
    schema_version TEXT NOT NULL
        CHECK(
            typeof(schema_version) = 'text'
            AND schema_version = 'briefloop.artifact_record.v2'
        ),
    current_revision INTEGER NOT NULL
        CHECK(typeof(current_revision) = 'integer' AND current_revision >= 0),
    current_revision_ref INTEGER CHECK(
        current_revision_ref IS NULL OR typeof(current_revision_ref) = 'integer'
    ),
    status TEXT NOT NULL CHECK(typeof(status) = 'text' AND length(status) > 0),
    required INTEGER NOT NULL
        CHECK(typeof(required) = 'integer' AND required IN (0, 1)),
    path TEXT NOT NULL CHECK(typeof(path) = 'text' AND length(path) > 0),
    format TEXT NOT NULL CHECK(typeof(format) = 'text' AND length(format) > 0),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, artifact_id),
    CHECK(
        (current_revision = 0 AND current_revision_ref IS NULL)
        OR
        (current_revision > 0 AND current_revision_ref = current_revision)
    ),
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, artifact_id, current_revision_ref)
        REFERENCES artifact_revisions(run_id, artifact_id, revision)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE artifact_revisions (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    artifact_id TEXT NOT NULL
        CHECK(typeof(artifact_id) = 'text' AND length(artifact_id) > 0),
    revision INTEGER NOT NULL
        CHECK(typeof(revision) = 'integer' AND revision > 0),
    schema_version TEXT NOT NULL
        CHECK(
            typeof(schema_version) = 'text'
            AND schema_version = 'briefloop.artifact_revision.v2'
        ),
    path TEXT NOT NULL CHECK(typeof(path) = 'text' AND length(path) > 0),
    sha256 TEXT NOT NULL
        CHECK(
            typeof(sha256) = 'text'
            AND length(sha256) = 64
            AND sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    size_bytes INTEGER NOT NULL
        CHECK(typeof(size_bytes) = 'integer' AND size_bytes >= 0),
    frozen INTEGER NOT NULL
        CHECK(typeof(frozen) = 'integer' AND frozen IN (0, 1)),
    producer_kind TEXT NOT NULL
        CHECK(typeof(producer_kind) = 'text' AND length(producer_kind) > 0),
    producer_id TEXT NOT NULL
        CHECK(typeof(producer_id) = 'text' AND length(producer_id) > 0),
    created_at TEXT NOT NULL
        CHECK(typeof(created_at) = 'text' AND length(created_at) > 0),
    blob_relpath TEXT NOT NULL
        CHECK(typeof(blob_relpath) = 'text' AND length(blob_relpath) > 0),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, artifact_id, revision),
    FOREIGN KEY(run_id, artifact_id) REFERENCES artifacts(run_id, artifact_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE events (
    event_id TEXT PRIMARY KEY
        CHECK(typeof(event_id) = 'text' AND length(event_id) > 0),
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    schema_version TEXT NOT NULL
        CHECK(
            typeof(schema_version) = 'text'
            AND schema_version = 'briefloop.event_envelope.v2'
        ),
    event_type TEXT NOT NULL
        CHECK(typeof(event_type) = 'text' AND length(event_type) > 0),
    created_at TEXT NOT NULL
        CHECK(typeof(created_at) = 'text' AND length(created_at) > 0),
    actor TEXT NOT NULL CHECK(typeof(actor) = 'text' AND length(actor) > 0),
    transaction_id TEXT CHECK(
        transaction_id IS NULL
        OR (typeof(transaction_id) = 'text' AND length(transaction_id) > 0)
    ),
    stage_id TEXT CHECK(
        stage_id IS NULL
        OR (typeof(stage_id) = 'text' AND length(stage_id) > 0)
    ),
    artifact_id TEXT CHECK(
        artifact_id IS NULL
        OR (typeof(artifact_id) = 'text' AND length(artifact_id) > 0)
    ),
    decision TEXT CHECK(
        decision IS NULL
        OR (typeof(decision) = 'text' AND length(decision) > 0)
    ),
    reason TEXT NOT NULL CHECK(typeof(reason) = 'text'),
    metadata_json TEXT NOT NULL CHECK(typeof(metadata_json) = 'text'),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    UNIQUE(run_id, event_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE approvals (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    approval_id TEXT NOT NULL
        CHECK(typeof(approval_id) = 'text' AND length(approval_id) > 0),
    schema_version TEXT NOT NULL
        CHECK(
            typeof(schema_version) = 'text'
            AND schema_version = 'briefloop.approval.v2'
        ),
    mode TEXT NOT NULL CHECK(typeof(mode) = 'text' AND length(mode) > 0),
    role TEXT NOT NULL CHECK(typeof(role) = 'text' AND length(role) > 0),
    decision TEXT NOT NULL CHECK(typeof(decision) = 'text' AND length(decision) > 0),
    reason TEXT NOT NULL CHECK(typeof(reason) = 'text' AND length(reason) > 0),
    actor_id TEXT NOT NULL CHECK(typeof(actor_id) = 'text' AND length(actor_id) > 0),
    recorded_at TEXT NOT NULL
        CHECK(typeof(recorded_at) = 'text' AND length(recorded_at) > 0),
    boundary TEXT NOT NULL CHECK(typeof(boundary) = 'text' AND length(boundary) > 0),
    event_id TEXT NOT NULL
        CHECK(typeof(event_id) = 'text' AND length(event_id) > 0),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, approval_id),
    FOREIGN KEY(run_id, event_id) REFERENCES events(run_id, event_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE deliveries (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    delivery_id TEXT NOT NULL
        CHECK(typeof(delivery_id) = 'text' AND length(delivery_id) > 0),
    schema_version TEXT NOT NULL
        CHECK(
            typeof(schema_version) = 'text'
            AND schema_version = 'briefloop.delivery.v2'
        ),
    artifact_id TEXT NOT NULL
        CHECK(typeof(artifact_id) = 'text' AND length(artifact_id) > 0),
    artifact_revision INTEGER NOT NULL
        CHECK(typeof(artifact_revision) = 'integer' AND artifact_revision > 0),
    approval_id TEXT CHECK(
        approval_id IS NULL
        OR (typeof(approval_id) = 'text' AND length(approval_id) > 0)
    ),
    status TEXT NOT NULL CHECK(typeof(status) = 'text' AND length(status) > 0),
    target TEXT NOT NULL CHECK(typeof(target) = 'text' AND length(target) > 0),
    channel TEXT NOT NULL CHECK(typeof(channel) = 'text' AND length(channel) > 0),
    created_at TEXT NOT NULL
        CHECK(typeof(created_at) = 'text' AND length(created_at) > 0),
    completed_at TEXT CHECK(
        completed_at IS NULL
        OR (typeof(completed_at) = 'text' AND length(completed_at) > 0)
    ),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, delivery_id),
    FOREIGN KEY(run_id, artifact_id, artifact_revision)
        REFERENCES artifact_revisions(run_id, artifact_id, revision)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, approval_id) REFERENCES approvals(run_id, approval_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_events (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    transaction_id TEXT NOT NULL
        CHECK(typeof(transaction_id) = 'text' AND length(transaction_id) > 0),
    position INTEGER NOT NULL CHECK(typeof(position) = 'integer' AND position >= 0),
    event_id TEXT NOT NULL
        CHECK(typeof(event_id) = 'text' AND length(event_id) > 0),
    PRIMARY KEY(run_id, transaction_id, position),
    UNIQUE(run_id, transaction_id, event_id),
    FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, event_id) REFERENCES events(run_id, event_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_artifact_revisions (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    transaction_id TEXT NOT NULL
        CHECK(typeof(transaction_id) = 'text' AND length(transaction_id) > 0),
    position INTEGER NOT NULL CHECK(typeof(position) = 'integer' AND position >= 0),
    artifact_id TEXT NOT NULL
        CHECK(typeof(artifact_id) = 'text' AND length(artifact_id) > 0),
    revision INTEGER NOT NULL CHECK(typeof(revision) = 'integer' AND revision > 0),
    PRIMARY KEY(run_id, transaction_id, position),
    UNIQUE(run_id, transaction_id, artifact_id, revision),
    FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, artifact_id, revision)
        REFERENCES artifact_revisions(run_id, artifact_id, revision)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TRIGGER runs_no_update BEFORE UPDATE ON runs
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER runs_no_delete BEFORE DELETE ON runs
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transactions_no_update BEFORE UPDATE ON transactions
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transactions_no_delete BEFORE DELETE ON transactions
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_events_no_update BEFORE UPDATE ON transaction_events
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_events_no_delete BEFORE DELETE ON transaction_events
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_artifact_revisions_no_update
BEFORE UPDATE ON transaction_artifact_revisions
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_artifact_revisions_no_delete
BEFORE DELETE ON transaction_artifact_revisions
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER artifact_revisions_no_update BEFORE UPDATE ON artifact_revisions
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER artifact_revisions_no_delete BEFORE DELETE ON artifact_revisions
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER approvals_no_update BEFORE UPDATE ON approvals
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER approvals_no_delete BEFORE DELETE ON approvals
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER schema_migrations_no_update BEFORE UPDATE ON schema_migrations
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER schema_migrations_no_delete BEFORE DELETE ON schema_migrations
BEGIN SELECT RAISE(ABORT, 'append_only'); END;

PRAGMA user_version = 1;
COMMIT;
