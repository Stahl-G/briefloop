BEGIN IMMEDIATE;

CREATE TABLE transaction_receipt_compatibility_boundaries (
    workspace_id TEXT PRIMARY KEY,
    boundary_id TEXT NOT NULL
        CHECK(boundary_id='briefloop.transaction_receipt_relation_compatibility.v1'),
    legacy_receipt_max_committed_revision INTEGER NOT NULL
        CHECK(
            typeof(legacy_receipt_max_committed_revision)='integer'
            AND legacy_receipt_max_committed_revision>=0
        ),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

INSERT INTO transaction_receipt_compatibility_boundaries(
    workspace_id,
    boundary_id,
    legacy_receipt_max_committed_revision
)
SELECT
    workspaces.workspace_id,
    'briefloop.transaction_receipt_relation_compatibility.v1',
    COALESCE(MAX(transactions.committed_revision),0)
FROM workspaces
LEFT JOIN transactions
    ON transactions.workspace_id=workspaces.workspace_id
GROUP BY workspaces.workspace_id;

CREATE TRIGGER transaction_receipt_compatibility_boundaries_no_update
BEFORE UPDATE ON transaction_receipt_compatibility_boundaries
BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_receipt_compatibility_boundaries_no_delete
BEFORE DELETE ON transaction_receipt_compatibility_boundaries
BEGIN SELECT RAISE(ABORT,'append_only'); END;

CREATE TABLE post_final_assessment_policy_revisions (
    run_id TEXT NOT NULL,
    policy_revision_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.post_final_assessment_policy_revision.v2'),
    previous_policy_revision_id TEXT,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    auto_run INTEGER NOT NULL CHECK(auto_run IN (0,1)),
    auto_open INTEGER NOT NULL CHECK(auto_open IN (0,1)),
    adapter_id TEXT NOT NULL CHECK(adapter_id='anthropic_messages_v1'),
    messages_endpoint_sha256 TEXT NOT NULL CHECK(length(messages_endpoint_sha256)=64 AND messages_endpoint_sha256 NOT GLOB '*[^0-9a-f]*'),
    requested_model_id TEXT NOT NULL,
    profile_id TEXT NOT NULL CHECK(profile_id='research_design_report_zh_v1'),
    human_request_id TEXT NOT NULL,
    policy_fingerprint TEXT NOT NULL CHECK(length(policy_fingerprint)=64 AND policy_fingerprint NOT GLOB '*[^0-9a-f]*'),
    recorded_at TEXT NOT NULL,
    policy_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id, policy_revision_id),
    UNIQUE(run_id, human_request_id),
    FOREIGN KEY(run_id, policy_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE post_final_assessment_requests (
    run_id TEXT NOT NULL,
    assessment_request_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.post_final_assessment_request_record.v2'),
    finalized_facts_fingerprint TEXT NOT NULL CHECK(length(finalized_facts_fingerprint)=64 AND finalized_facts_fingerprint NOT GLOB '*[^0-9a-f]*'),
    finalized_lineage_fingerprint TEXT NOT NULL CHECK(length(finalized_lineage_fingerprint)=64 AND finalized_lineage_fingerprint NOT GLOB '*[^0-9a-f]*'),
    policy_revision_id TEXT NOT NULL,
    trial_id TEXT NOT NULL,
    archive_identity_sha256 TEXT NOT NULL CHECK(length(archive_identity_sha256)=64 AND archive_identity_sha256 NOT GLOB '*[^0-9a-f]*'),
    request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    claimed_at TEXT NOT NULL,
    request_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id, assessment_request_id),
    UNIQUE(run_id, finalized_facts_fingerprint),
    UNIQUE(run_id, finalized_lineage_fingerprint),
    UNIQUE(run_id, trial_id),
    FOREIGN KEY(run_id, policy_revision_id) REFERENCES post_final_assessment_policy_revisions(run_id,policy_revision_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, request_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE post_final_assessment_results (
    run_id TEXT NOT NULL,
    assessment_result_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.post_final_assessment_result_record.v2'),
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

CREATE TABLE transaction_post_final_assessment_policy_revisions (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    policy_revision_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,policy_revision_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,policy_revision_id) REFERENCES post_final_assessment_policy_revisions(run_id,policy_revision_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_post_final_assessment_requests (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    assessment_request_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,assessment_request_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,assessment_request_id) REFERENCES post_final_assessment_requests(run_id,assessment_request_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_post_final_assessment_results (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    assessment_result_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,assessment_result_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,assessment_result_id) REFERENCES post_final_assessment_results(run_id,assessment_result_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE post_final_finding_dispositions (
    run_id TEXT NOT NULL,
    disposition_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.post_final_finding_disposition_record.v2'),
    finalized_lineage_fingerprint TEXT NOT NULL,
    assessment_result_id TEXT NOT NULL,
    assessment_result_fingerprint TEXT NOT NULL,
    reader_view_sha256 TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    finding_fingerprint TEXT NOT NULL,
    previous_disposition_id TEXT,
    decision TEXT NOT NULL CHECK(decision IN ('accept','reject','defer')),
    human_note TEXT,
    human_actor_id TEXT NOT NULL,
    human_request_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    disposition_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    disposition_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,disposition_id),
    UNIQUE(run_id,human_request_id),
    FOREIGN KEY(run_id,assessment_result_id) REFERENCES post_final_assessment_results(run_id,assessment_result_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,previous_disposition_id) REFERENCES post_final_finding_dispositions(run_id,disposition_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,disposition_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE post_final_guidance_drafts (
    run_id TEXT NOT NULL,
    guidance_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL CHECK(draft_revision>0),
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.post_final_guidance_draft_revision.v2'),
    finalized_lineage_fingerprint TEXT NOT NULL,
    assessment_result_id TEXT NOT NULL,
    assessment_result_fingerprint TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    finding_fingerprint TEXT NOT NULL,
    disposition_id TEXT NOT NULL,
    disposition_fingerprint TEXT NOT NULL,
    previous_draft_revision INTEGER,
    guidance_scope TEXT NOT NULL CHECK(guidance_scope='finding_only'),
    guidance_text TEXT NOT NULL,
    guidance_sha256 TEXT NOT NULL,
    human_actor_id TEXT NOT NULL,
    human_request_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    draft_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    draft_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,guidance_id,draft_revision),
    UNIQUE(run_id,human_request_id),
    FOREIGN KEY(run_id,disposition_id) REFERENCES post_final_finding_dispositions(run_id,disposition_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,draft_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE post_final_guidance_statuses (
    run_id TEXT NOT NULL,
    status_revision_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.post_final_guidance_status_revision.v2'),
    finalized_lineage_fingerprint TEXT NOT NULL,
    guidance_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL,
    guidance_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('approved','deactivated','reverted','superseded')),
    previous_status_revision_id TEXT,
    human_actor_id TEXT NOT NULL,
    human_request_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    status_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    status_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,status_revision_id),
    UNIQUE(run_id,human_request_id),
    FOREIGN KEY(run_id,guidance_id,draft_revision) REFERENCES post_final_guidance_drafts(run_id,guidance_id,draft_revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,previous_status_revision_id) REFERENCES post_final_guidance_statuses(run_id,status_revision_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,status_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_post_final_finding_dispositions (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    disposition_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,disposition_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,disposition_id) REFERENCES post_final_finding_dispositions(run_id,disposition_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_post_final_guidance_drafts (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    guidance_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,guidance_id,draft_revision),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,guidance_id,draft_revision) REFERENCES post_final_guidance_drafts(run_id,guidance_id,draft_revision) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_post_final_guidance_statuses (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    status_revision_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,status_revision_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,status_revision_id) REFERENCES post_final_guidance_statuses(run_id,status_revision_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TRIGGER post_final_assessment_policy_revisions_no_update BEFORE UPDATE ON post_final_assessment_policy_revisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_policy_revisions_no_delete BEFORE DELETE ON post_final_assessment_policy_revisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_requests_no_update BEFORE UPDATE ON post_final_assessment_requests BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_requests_no_delete BEFORE DELETE ON post_final_assessment_requests BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_results_no_update BEFORE UPDATE ON post_final_assessment_results BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_assessment_results_no_delete BEFORE DELETE ON post_final_assessment_results BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_assessment_policy_revisions_no_update BEFORE UPDATE ON transaction_post_final_assessment_policy_revisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_assessment_policy_revisions_no_delete BEFORE DELETE ON transaction_post_final_assessment_policy_revisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_assessment_requests_no_update BEFORE UPDATE ON transaction_post_final_assessment_requests BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_assessment_requests_no_delete BEFORE DELETE ON transaction_post_final_assessment_requests BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_assessment_results_no_update BEFORE UPDATE ON transaction_post_final_assessment_results BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_assessment_results_no_delete BEFORE DELETE ON transaction_post_final_assessment_results BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_finding_dispositions_no_update BEFORE UPDATE ON post_final_finding_dispositions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_finding_dispositions_no_delete BEFORE DELETE ON post_final_finding_dispositions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_guidance_drafts_no_update BEFORE UPDATE ON post_final_guidance_drafts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_guidance_drafts_no_delete BEFORE DELETE ON post_final_guidance_drafts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_guidance_statuses_no_update BEFORE UPDATE ON post_final_guidance_statuses BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_guidance_statuses_no_delete BEFORE DELETE ON post_final_guidance_statuses BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_finding_dispositions_no_update BEFORE UPDATE ON transaction_post_final_finding_dispositions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_finding_dispositions_no_delete BEFORE DELETE ON transaction_post_final_finding_dispositions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_guidance_drafts_no_update BEFORE UPDATE ON transaction_post_final_guidance_drafts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_guidance_drafts_no_delete BEFORE DELETE ON transaction_post_final_guidance_drafts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_guidance_statuses_no_update BEFORE UPDATE ON transaction_post_final_guidance_statuses BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_guidance_statuses_no_delete BEFORE DELETE ON transaction_post_final_guidance_statuses BEGIN SELECT RAISE(ABORT,'append_only'); END;

INSERT INTO schema_migrations(version,name) VALUES(10,'0010');
PRAGMA user_version=10;
COMMIT;
