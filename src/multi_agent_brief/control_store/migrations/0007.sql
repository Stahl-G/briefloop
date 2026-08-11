BEGIN IMMEDIATE;

CREATE TABLE run_execution_authorizations (
    run_id TEXT NOT NULL CHECK(typeof(run_id)='text' AND length(run_id)>0),
    authorization_id TEXT NOT NULL CHECK(typeof(authorization_id)='text' AND length(authorization_id)>0),
    workspace_id TEXT NOT NULL CHECK(typeof(workspace_id)='text' AND length(workspace_id)>0),
    schema_version TEXT NOT NULL CHECK(typeof(schema_version)='text' AND schema_version='briefloop.run_execution_authorization.v2'),
    run_contract_fingerprint TEXT NOT NULL CHECK(typeof(run_contract_fingerprint)='text' AND length(run_contract_fingerprint)=64 AND run_contract_fingerprint NOT GLOB '*[^0-9a-f]*'),
    run_direction_fingerprint TEXT NOT NULL CHECK(typeof(run_direction_fingerprint)='text' AND length(run_direction_fingerprint)=64 AND run_direction_fingerprint NOT GLOB '*[^0-9a-f]*'),
    completion_target TEXT NOT NULL CHECK(typeof(completion_target)='text' AND completion_target='finalized_local'),
    source_manifest_artifact_id TEXT NOT NULL CHECK(typeof(source_manifest_artifact_id)='text' AND length(source_manifest_artifact_id)>0),
    source_manifest_revision INTEGER NOT NULL CHECK(typeof(source_manifest_revision)='integer' AND source_manifest_revision=1),
    source_manifest_sha256 TEXT NOT NULL CHECK(typeof(source_manifest_sha256)='text' AND length(source_manifest_sha256)=64 AND source_manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    source_manifest_member_count INTEGER NOT NULL CHECK(typeof(source_manifest_member_count)='integer' AND source_manifest_member_count BETWEEN 1 AND 256),
    repair_budget INTEGER NOT NULL CHECK(typeof(repair_budget)='integer' AND repair_budget=1),
    authorization_event_id TEXT NOT NULL CHECK(typeof(authorization_event_id)='text' AND length(authorization_event_id)>0),
    accepted_transaction_id TEXT NOT NULL CHECK(typeof(accepted_transaction_id)='text' AND length(accepted_transaction_id)>0),
    request_fingerprint TEXT NOT NULL CHECK(typeof(request_fingerprint)='text' AND length(request_fingerprint)=64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL CHECK(typeof(created_at)='text' AND length(created_at)>0),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json)='text'),
    PRIMARY KEY(run_id, authorization_id),
    UNIQUE(run_id),
    UNIQUE(run_id, source_manifest_artifact_id, source_manifest_revision),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, source_manifest_artifact_id, source_manifest_revision) REFERENCES artifact_revisions(run_id, artifact_id, revision) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, authorization_event_id) REFERENCES events(run_id, event_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id, transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id, authorization_event_id) REFERENCES transaction_events(run_id, transaction_id, event_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id, source_manifest_artifact_id, source_manifest_revision) REFERENCES transaction_artifact_revisions(run_id, transaction_id, artifact_id, revision) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

CREATE TRIGGER run_execution_authorizations_no_update BEFORE UPDATE ON run_execution_authorizations
BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_execution_authorizations_no_delete BEFORE DELETE ON run_execution_authorizations
BEGIN SELECT RAISE(ABORT,'append_only'); END;

CREATE TABLE transaction_run_execution_authorizations (
    run_id TEXT NOT NULL CHECK(typeof(run_id)='text' AND length(run_id)>0),
    transaction_id TEXT NOT NULL CHECK(typeof(transaction_id)='text' AND length(transaction_id)>0),
    position INTEGER NOT NULL CHECK(typeof(position)='integer' AND position>=0),
    authorization_id TEXT NOT NULL CHECK(typeof(authorization_id)='text' AND length(authorization_id)>0),
    PRIMARY KEY(run_id, transaction_id, position),
    UNIQUE(run_id, transaction_id, authorization_id),
    FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, authorization_id) REFERENCES run_execution_authorizations(run_id, authorization_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

INSERT INTO schema_migrations(version,name) VALUES(7,'0007');
PRAGMA user_version=7;
COMMIT;
