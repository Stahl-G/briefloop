BEGIN IMMEDIATE;

CREATE TABLE post_final_assessment_abandonment_compatibility_boundaries (
    workspace_id TEXT PRIMARY KEY,
    boundary_id TEXT NOT NULL
        CHECK(boundary_id='briefloop.post_final_assessment_abandonment_compatibility.v1'),
    legacy_receipt_max_committed_revision INTEGER NOT NULL
        CHECK(
            typeof(legacy_receipt_max_committed_revision)='integer'
            AND legacy_receipt_max_committed_revision>=0
        ),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

INSERT INTO post_final_assessment_abandonment_compatibility_boundaries(
    workspace_id,
    boundary_id,
    legacy_receipt_max_committed_revision
)
SELECT
    workspaces.workspace_id,
    'briefloop.post_final_assessment_abandonment_compatibility.v1',
    COALESCE(MAX(transactions.committed_revision),0)
FROM workspaces
LEFT JOIN transactions
    ON transactions.workspace_id=workspaces.workspace_id
GROUP BY workspaces.workspace_id;

CREATE TRIGGER post_final_assessment_abandonment_compatibility_boundaries_no_update
BEFORE UPDATE ON post_final_assessment_abandonment_compatibility_boundaries
BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_abandonment_compatibility_boundaries_no_delete
BEFORE DELETE ON post_final_assessment_abandonment_compatibility_boundaries
BEGIN SELECT RAISE(ABORT,'append_only'); END;

PRAGMA legacy_alter_table=ON;

ALTER TABLE post_final_assessment_requests
RENAME TO post_final_assessment_requests_v11;

CREATE TABLE post_final_assessment_requests (
    run_id TEXT NOT NULL,
    assessment_request_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version IN (
        'briefloop.post_final_assessment_request_record.v2',
        'briefloop.post_final_assessment_request_record.v3'
    )),
    finalized_facts_fingerprint TEXT NOT NULL CHECK(length(finalized_facts_fingerprint)=64 AND finalized_facts_fingerprint NOT GLOB '*[^0-9a-f]*'),
    finalized_lineage_fingerprint TEXT NOT NULL CHECK(length(finalized_lineage_fingerprint)=64 AND finalized_lineage_fingerprint NOT GLOB '*[^0-9a-f]*'),
    policy_revision_id TEXT NOT NULL,
    trial_id TEXT NOT NULL,
    archive_identity_sha256 TEXT NOT NULL CHECK(length(archive_identity_sha256)=64 AND archive_identity_sha256 NOT GLOB '*[^0-9a-f]*'),
    request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    claimed_at TEXT NOT NULL,
    request_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    assessment_generation INTEGER NOT NULL CHECK(assessment_generation>0),
    predecessor_assessment_request_id TEXT,
    predecessor_assessment_request_fingerprint TEXT,
    predecessor_assessment_result_id TEXT,
    predecessor_result_fingerprint TEXT,
    predecessor_abandonment_id TEXT,
    predecessor_abandonment_fingerprint TEXT,
    assessment_purpose TEXT NOT NULL CHECK(assessment_purpose IN ('post_final_review','model_evaluation')),
    human_actor_id TEXT,
    human_request_id TEXT,
    authorization_fingerprint TEXT,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id, assessment_request_id),
    UNIQUE(run_id, finalized_lineage_fingerprint, assessment_generation),
    UNIQUE(run_id, human_request_id),
    UNIQUE(run_id, trial_id),
    CHECK(
        (schema_version='briefloop.post_final_assessment_request_record.v2'
         AND assessment_generation=1
         AND predecessor_assessment_request_id IS NULL
         AND predecessor_assessment_request_fingerprint IS NULL
         AND predecessor_assessment_result_id IS NULL
         AND predecessor_result_fingerprint IS NULL
         AND predecessor_abandonment_id IS NULL
         AND predecessor_abandonment_fingerprint IS NULL
         AND assessment_purpose='post_final_review'
         AND human_actor_id IS NULL
         AND human_request_id IS NULL
         AND authorization_fingerprint IS NULL)
        OR
        (schema_version='briefloop.post_final_assessment_request_record.v3'
         AND human_actor_id IS NOT NULL
         AND human_request_id IS NOT NULL
         AND authorization_fingerprint IS NOT NULL
         AND (
             (assessment_generation=1
              AND predecessor_assessment_request_id IS NULL
              AND predecessor_assessment_request_fingerprint IS NULL
              AND predecessor_assessment_result_id IS NULL
              AND predecessor_result_fingerprint IS NULL
              AND predecessor_abandonment_id IS NULL
              AND predecessor_abandonment_fingerprint IS NULL)
             OR
             (assessment_generation>1
              AND predecessor_assessment_request_id IS NOT NULL
              AND predecessor_assessment_request_fingerprint IS NOT NULL
              AND (
                  (predecessor_assessment_result_id IS NOT NULL
                   AND predecessor_result_fingerprint IS NOT NULL
                   AND predecessor_abandonment_id IS NULL
                   AND predecessor_abandonment_fingerprint IS NULL)
                  OR
                  (predecessor_assessment_result_id IS NULL
                   AND predecessor_result_fingerprint IS NULL
                   AND predecessor_abandonment_id IS NOT NULL
                   AND predecessor_abandonment_fingerprint IS NOT NULL)
              ))
         ))
    ),
    FOREIGN KEY(run_id, policy_revision_id) REFERENCES post_final_assessment_policy_revisions(run_id,policy_revision_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, request_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, predecessor_assessment_request_id) REFERENCES post_final_assessment_requests(run_id,assessment_request_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, predecessor_assessment_result_id) REFERENCES post_final_assessment_results(run_id,assessment_result_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, predecessor_abandonment_id) REFERENCES post_final_assessment_abandonments(run_id,abandonment_id) DEFERRABLE INITIALLY DEFERRED
);

INSERT INTO post_final_assessment_requests(
    run_id,
    assessment_request_id,
    schema_version,
    finalized_facts_fingerprint,
    finalized_lineage_fingerprint,
    policy_revision_id,
    trial_id,
    archive_identity_sha256,
    request_fingerprint,
    claimed_at,
    request_event_id,
    accepted_transaction_id,
    assessment_generation,
    predecessor_assessment_request_id,
    predecessor_assessment_request_fingerprint,
    predecessor_assessment_result_id,
    predecessor_result_fingerprint,
    predecessor_abandonment_id,
    predecessor_abandonment_fingerprint,
    assessment_purpose,
    human_actor_id,
    human_request_id,
    authorization_fingerprint,
    payload_json
)
SELECT
    run_id,
    assessment_request_id,
    schema_version,
    finalized_facts_fingerprint,
    finalized_lineage_fingerprint,
    policy_revision_id,
    trial_id,
    archive_identity_sha256,
    request_fingerprint,
    claimed_at,
    request_event_id,
    accepted_transaction_id,
    1,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    NULL,
    'post_final_review',
    NULL,
    NULL,
    NULL,
    payload_json
FROM post_final_assessment_requests_v11;

DROP TABLE post_final_assessment_requests_v11;

PRAGMA legacy_alter_table=OFF;

CREATE TABLE post_final_assessment_abandonments (
    run_id TEXT NOT NULL,
    abandonment_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.post_final_assessment_abandonment_record.v1'),
    assessment_request_id TEXT NOT NULL,
    assessment_request_fingerprint TEXT NOT NULL CHECK(length(assessment_request_fingerprint)=64 AND assessment_request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    finalized_lineage_fingerprint TEXT NOT NULL CHECK(length(finalized_lineage_fingerprint)=64 AND finalized_lineage_fingerprint NOT GLOB '*[^0-9a-f]*'),
    assessment_generation INTEGER NOT NULL CHECK(assessment_generation>0),
    reason TEXT NOT NULL CHECK(reason='outcome_unknown'),
    human_actor_id TEXT NOT NULL,
    human_request_id TEXT NOT NULL,
    expected_store_revision INTEGER NOT NULL CHECK(expected_store_revision>=0),
    abandonment_fingerprint TEXT NOT NULL CHECK(length(abandonment_fingerprint)=64 AND abandonment_fingerprint NOT GLOB '*[^0-9a-f]*'),
    recorded_at TEXT NOT NULL,
    abandonment_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id, abandonment_id),
    UNIQUE(run_id, assessment_request_id),
    UNIQUE(run_id, human_request_id),
    FOREIGN KEY(run_id, assessment_request_id) REFERENCES post_final_assessment_requests(run_id,assessment_request_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, abandonment_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_post_final_assessment_abandonments (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    abandonment_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,abandonment_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,abandonment_id) REFERENCES post_final_assessment_abandonments(run_id,abandonment_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TRIGGER post_final_assessment_requests_no_update BEFORE UPDATE ON post_final_assessment_requests BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_requests_no_delete BEFORE DELETE ON post_final_assessment_requests BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_abandonments_no_update BEFORE UPDATE ON post_final_assessment_abandonments BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_abandonments_no_delete BEFORE DELETE ON post_final_assessment_abandonments BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_assessment_abandonments_no_update BEFORE UPDATE ON transaction_post_final_assessment_abandonments BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_assessment_abandonments_no_delete BEFORE DELETE ON transaction_post_final_assessment_abandonments BEGIN SELECT RAISE(ABORT,'append_only'); END;

INSERT INTO schema_migrations(version,name) VALUES(12,'0012');
PRAGMA user_version=12;
COMMIT;
