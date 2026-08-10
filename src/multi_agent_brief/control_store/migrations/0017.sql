BEGIN IMMEDIATE;

-- Fresh-only schema17 execution evidence.  The full identity remains in
-- payload_json; this table is the single durable execution witness.
CREATE TABLE post_final_assessment_executions (
    run_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(
        schema_version='briefloop.post_final_assessment_execution.v1'
    ),
    assessment_request_id TEXT NOT NULL,
    trial_id TEXT NOT NULL,
    execution_archive_manifest_sha256 TEXT NOT NULL CHECK(
        length(execution_archive_manifest_sha256)=64
        AND execution_archive_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    execution_receipt_id TEXT NOT NULL,
    execution_status TEXT NOT NULL CHECK(
        execution_status IN ('complete','provider_failed','local_derivation_failed')
    ),
    run_status TEXT,
    validation_status TEXT,
    reason_codes_json TEXT NOT NULL,
    execution_fingerprint TEXT NOT NULL CHECK(
        length(execution_fingerprint)=64
        AND execution_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    recorded_at TEXT NOT NULL,
    execution_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id, execution_id),
    UNIQUE(run_id, assessment_request_id),
    UNIQUE(run_id, execution_receipt_id),
    FOREIGN KEY(run_id, assessment_request_id)
        REFERENCES post_final_assessment_requests(run_id,assessment_request_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, execution_event_id)
        REFERENCES events(run_id,event_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id)
        REFERENCES transactions(run_id,transaction_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TRIGGER post_final_assessment_executions_no_update
BEFORE UPDATE ON post_final_assessment_executions
BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_executions_no_delete
BEFORE DELETE ON post_final_assessment_executions
BEGIN SELECT RAISE(ABORT,'append_only'); END;

INSERT INTO schema_migrations(version,name) VALUES(17,'0017');
PRAGMA user_version=17;
COMMIT;
