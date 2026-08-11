BEGIN IMMEDIATE;

-- Fresh-schema v0.15 Reader Review bindings. Runtime opening remains
-- fail-closed: older Stores are never migrated implicitly by the application.
PRAGMA legacy_alter_table=ON;

DROP TRIGGER post_final_assessment_policy_revisions_no_update;
DROP TRIGGER post_final_assessment_policy_revisions_no_delete;
DROP TRIGGER post_final_assessment_requests_no_update;
DROP TRIGGER post_final_assessment_requests_no_delete;
DROP TRIGGER post_final_assessment_results_no_update;
DROP TRIGGER post_final_assessment_results_no_delete;

ALTER TABLE post_final_assessment_policy_revisions
RENAME TO post_final_assessment_policy_revisions_v13;
ALTER TABLE post_final_assessment_requests
RENAME TO post_final_assessment_requests_v13;
ALTER TABLE post_final_assessment_results
RENAME TO post_final_assessment_results_v13;

CREATE TABLE post_final_assessment_policy_revisions (
    run_id TEXT NOT NULL,
    policy_revision_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version IN (
        'briefloop.post_final_assessment_policy_revision.v2',
        'briefloop.post_final_assessment_policy_revision.v3'
    )),
    previous_policy_revision_id TEXT,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    auto_run INTEGER NOT NULL CHECK(auto_run IN (0,1)),
    auto_open INTEGER NOT NULL CHECK(auto_open IN (0,1)),
    adapter_id TEXT NOT NULL CHECK(adapter_id='anthropic_messages_v1'),
    messages_endpoint_sha256 TEXT NOT NULL CHECK(length(messages_endpoint_sha256)=64 AND messages_endpoint_sha256 NOT GLOB '*[^0-9a-f]*'),
    requested_model_id TEXT NOT NULL,
    profile_id TEXT NOT NULL CHECK(profile_id IN (
        'research_design_report_zh_v1',
        'management_brief_en_v1'
    )),
    human_request_id TEXT NOT NULL,
    policy_fingerprint TEXT NOT NULL CHECK(length(policy_fingerprint)=64 AND policy_fingerprint NOT GLOB '*[^0-9a-f]*'),
    recorded_at TEXT NOT NULL,
    policy_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id, policy_revision_id),
    UNIQUE(run_id, human_request_id),
    CHECK(
        (schema_version='briefloop.post_final_assessment_policy_revision.v2'
         AND profile_id='research_design_report_zh_v1')
        OR
        (schema_version='briefloop.post_final_assessment_policy_revision.v3'
         AND profile_id='management_brief_en_v1'
         AND enabled=1 AND auto_run=0 AND auto_open=0)
    ),
    FOREIGN KEY(run_id, policy_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE post_final_assessment_requests (
    run_id TEXT NOT NULL,
    assessment_request_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version IN (
        'briefloop.post_final_assessment_request_record.v2',
        'briefloop.post_final_assessment_request_record.v3',
        'briefloop.post_final_assessment_request_record.v4'
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
        (schema_version IN (
             'briefloop.post_final_assessment_request_record.v3',
             'briefloop.post_final_assessment_request_record.v4'
         )
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

CREATE TABLE post_final_assessment_results (
    run_id TEXT NOT NULL,
    assessment_result_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version IN (
        'briefloop.post_final_assessment_result_record.v2',
        'briefloop.post_final_assessment_result_record.v3'
    )),
    assessment_request_id TEXT NOT NULL,
    policy_revision_id TEXT NOT NULL,
    finalized_facts_fingerprint TEXT NOT NULL CHECK(length(finalized_facts_fingerprint)=64 AND finalized_facts_fingerprint NOT GLOB '*[^0-9a-f]*'),
    finalized_lineage_fingerprint TEXT NOT NULL CHECK(length(finalized_lineage_fingerprint)=64 AND finalized_lineage_fingerprint NOT GLOB '*[^0-9a-f]*'),
    terminal_evidence_class TEXT NOT NULL CHECK(terminal_evidence_class IN ('available','abstained','provider_failed','refused','incomplete','unavailable')),
    result_fingerprint TEXT NOT NULL CHECK(length(result_fingerprint)=64 AND result_fingerprint NOT GLOB '*[^0-9a-f]*'),
    recorded_at TEXT NOT NULL,
    result_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id, assessment_result_id),
    UNIQUE(run_id, assessment_request_id),
    FOREIGN KEY(run_id, assessment_request_id) REFERENCES post_final_assessment_requests(run_id,assessment_request_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, policy_revision_id) REFERENCES post_final_assessment_policy_revisions(run_id,policy_revision_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, result_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);

INSERT INTO post_final_assessment_policy_revisions
SELECT * FROM post_final_assessment_policy_revisions_v13;
INSERT INTO post_final_assessment_requests
SELECT * FROM post_final_assessment_requests_v13;
INSERT INTO post_final_assessment_results
SELECT * FROM post_final_assessment_results_v13;

DROP TABLE post_final_assessment_results_v13;
DROP TABLE post_final_assessment_requests_v13;
DROP TABLE post_final_assessment_policy_revisions_v13;

CREATE TRIGGER post_final_assessment_policy_revisions_no_update BEFORE UPDATE ON post_final_assessment_policy_revisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_policy_revisions_no_delete BEFORE DELETE ON post_final_assessment_policy_revisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_requests_no_update BEFORE UPDATE ON post_final_assessment_requests BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_requests_no_delete BEFORE DELETE ON post_final_assessment_requests BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_results_no_update BEFORE UPDATE ON post_final_assessment_results BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_results_no_delete BEFORE DELETE ON post_final_assessment_results BEGIN SELECT RAISE(ABORT,'append_only'); END;

PRAGMA legacy_alter_table=OFF;

INSERT INTO schema_migrations(version,name) VALUES(14,'0014');
PRAGMA user_version=14;
COMMIT;
