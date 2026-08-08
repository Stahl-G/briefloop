BEGIN IMMEDIATE;

-- V15-B is a fresh current-schema cut.  Runtime opening never applies this
-- file to an existing v14 Store.  The old empty definitions are retained
-- under an explicit suffix so no data-destructive migration is possible.
PRAGMA legacy_alter_table=ON;
ALTER TABLE post_final_guidance_drafts RENAME TO post_final_guidance_drafts_v14;
ALTER TABLE post_final_guidance_statuses RENAME TO post_final_guidance_statuses_v14;
ALTER TABLE transaction_post_final_guidance_drafts RENAME TO transaction_post_final_guidance_drafts_v14;
ALTER TABLE transaction_post_final_guidance_statuses RENAME TO transaction_post_final_guidance_statuses_v14;
ALTER TABLE run_guidance_selection_decisions RENAME TO run_guidance_selection_decisions_v14;
ALTER TABLE run_guidance_snapshot_items RENAME TO run_guidance_snapshot_items_v14;
ALTER TABLE run_guidance_snapshot_decisions RENAME TO run_guidance_snapshot_decisions_v14;
ALTER TABLE run_guidance_snapshot_selected_items RENAME TO run_guidance_snapshot_selected_items_v14;
ALTER TABLE transaction_run_guidance_snapshots RENAME TO transaction_run_guidance_snapshots_v14;
ALTER TABLE transaction_run_guidance_selection_decisions RENAME TO transaction_run_guidance_selection_decisions_v14;
ALTER TABLE transaction_run_guidance_snapshot_items RENAME TO transaction_run_guidance_snapshot_items_v14;
PRAGMA legacy_alter_table=OFF;

CREATE TABLE post_final_human_observations (
    run_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.post_final_human_observation_record.v1'),
    origin TEXT NOT NULL CHECK(origin='human'),
    observation_revision INTEGER NOT NULL CHECK(observation_revision>0),
    finalized_lineage_fingerprint TEXT NOT NULL CHECK(length(finalized_lineage_fingerprint)=64 AND finalized_lineage_fingerprint NOT GLOB '*[^0-9a-f]*'),
    report_revision INTEGER NOT NULL CHECK(report_revision>0),
    report_artifact_id TEXT NOT NULL,
    report_sha256 TEXT NOT NULL CHECK(length(report_sha256)=64 AND report_sha256 NOT GLOB '*[^0-9a-f]*'),
    assessment_result_id TEXT,
    assessment_result_fingerprint TEXT CHECK(assessment_result_fingerprint IS NULL OR (length(assessment_result_fingerprint)=64 AND assessment_result_fingerprint NOT GLOB '*[^0-9a-f]*')),
    reader_view_sha256 TEXT CHECK(reader_view_sha256 IS NULL OR (length(reader_view_sha256)=64 AND reader_view_sha256 NOT GLOB '*[^0-9a-f]*')),
    observation_text TEXT NOT NULL,
    observation_sha256 TEXT NOT NULL CHECK(length(observation_sha256)=64 AND observation_sha256 NOT GLOB '*[^0-9a-f]*'),
    requirement_id TEXT,
    claim_id TEXT,
    report_span_json TEXT NOT NULL,
    scope_class TEXT CHECK(scope_class IS NULL OR scope_class IN ('O1','O2')),
    dimension_id TEXT,
    previous_observation_id TEXT,
    previous_observation_fingerprint TEXT CHECK(previous_observation_fingerprint IS NULL OR (length(previous_observation_fingerprint)=64 AND previous_observation_fingerprint NOT GLOB '*[^0-9a-f]*')),
    human_actor_id TEXT NOT NULL,
    human_request_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    observation_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    observation_fingerprint TEXT NOT NULL CHECK(length(observation_fingerprint)=64 AND observation_fingerprint NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,observation_id),
    UNIQUE(run_id,human_request_id),
    CHECK((assessment_result_id IS NULL AND assessment_result_fingerprint IS NULL AND reader_view_sha256 IS NULL) OR (assessment_result_id IS NOT NULL AND assessment_result_fingerprint IS NOT NULL AND reader_view_sha256 IS NOT NULL)),
    CHECK((scope_class IS NULL AND dimension_id IS NULL) OR (scope_class IS NOT NULL AND dimension_id IS NOT NULL)),
    CHECK((previous_observation_id IS NULL AND previous_observation_fingerprint IS NULL) OR (previous_observation_id IS NOT NULL AND previous_observation_fingerprint IS NOT NULL)),
    CHECK((observation_revision=1 AND previous_observation_id IS NULL) OR (observation_revision>1 AND previous_observation_id IS NOT NULL)),
    FOREIGN KEY(run_id,assessment_result_id) REFERENCES post_final_assessment_results(run_id,assessment_result_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,previous_observation_id) REFERENCES post_final_human_observations(run_id,observation_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,observation_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE transaction_post_final_human_observations (
    run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0), observation_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position), UNIQUE(run_id,observation_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,observation_id) REFERENCES post_final_human_observations(run_id,observation_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE post_final_guidance_drafts (
    run_id TEXT NOT NULL, guidance_id TEXT NOT NULL, draft_revision INTEGER NOT NULL CHECK(draft_revision>0),
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.post_final_guidance_draft_revision.v2'),
    finalized_lineage_fingerprint TEXT NOT NULL,
    provenance_kind TEXT NOT NULL CHECK(provenance_kind IN ('accepted_model_finding','human_observation')),
    assessment_result_id TEXT, assessment_result_fingerprint TEXT, finding_id TEXT, finding_fingerprint TEXT,
    disposition_id TEXT, disposition_fingerprint TEXT, observation_id TEXT, observation_fingerprint TEXT,
    previous_draft_revision INTEGER, guidance_scope TEXT NOT NULL CHECK(guidance_scope IN ('finding_only','observation_only')), guidance_text TEXT NOT NULL,
    guidance_sha256 TEXT NOT NULL, human_actor_id TEXT NOT NULL, human_request_id TEXT NOT NULL, recorded_at TEXT NOT NULL,
    draft_event_id TEXT NOT NULL, accepted_transaction_id TEXT NOT NULL, draft_fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,guidance_id,draft_revision), UNIQUE(run_id,human_request_id),
    CHECK((provenance_kind='accepted_model_finding' AND assessment_result_id IS NOT NULL AND assessment_result_fingerprint IS NOT NULL AND finding_id IS NOT NULL AND finding_fingerprint IS NOT NULL AND disposition_id IS NOT NULL AND disposition_fingerprint IS NOT NULL AND observation_id IS NULL AND observation_fingerprint IS NULL) OR (provenance_kind='human_observation' AND observation_id IS NOT NULL AND observation_fingerprint IS NOT NULL AND finding_id IS NULL AND finding_fingerprint IS NULL AND disposition_id IS NULL AND disposition_fingerprint IS NULL AND ((assessment_result_id IS NULL AND assessment_result_fingerprint IS NULL) OR (assessment_result_id IS NOT NULL AND assessment_result_fingerprint IS NOT NULL)))),
    FOREIGN KEY(run_id,disposition_id) REFERENCES post_final_finding_dispositions(run_id,disposition_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,observation_id) REFERENCES post_final_human_observations(run_id,observation_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,draft_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE post_final_guidance_statuses (
    run_id TEXT NOT NULL, status_revision_id TEXT NOT NULL, schema_version TEXT NOT NULL CHECK(schema_version='briefloop.post_final_guidance_status_revision.v2'),
    finalized_lineage_fingerprint TEXT NOT NULL, guidance_id TEXT NOT NULL, draft_revision INTEGER NOT NULL, guidance_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('approved','deactivated','reverted','superseded')), previous_status_revision_id TEXT,
    human_actor_id TEXT NOT NULL, human_request_id TEXT NOT NULL, recorded_at TEXT NOT NULL, status_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL, status_fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,status_revision_id), UNIQUE(run_id,human_request_id),
    FOREIGN KEY(run_id,guidance_id,draft_revision) REFERENCES post_final_guidance_drafts(run_id,guidance_id,draft_revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,previous_status_revision_id) REFERENCES post_final_guidance_statuses(run_id,status_revision_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,status_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE transaction_post_final_guidance_drafts (
    run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0), guidance_id TEXT NOT NULL, draft_revision INTEGER NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position), UNIQUE(run_id,guidance_id,draft_revision),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,guidance_id,draft_revision) REFERENCES post_final_guidance_drafts(run_id,guidance_id,draft_revision) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE transaction_post_final_guidance_statuses (
    run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0), status_revision_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position), UNIQUE(run_id,status_revision_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,status_revision_id) REFERENCES post_final_guidance_statuses(run_id,status_revision_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE run_guidance_selection_decisions (
    run_id TEXT NOT NULL, decision_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, source_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.run_guidance_selection_decision_record.v1'), guidance_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL CHECK(draft_revision>0), status_revision_id TEXT,
    provenance_kind TEXT NOT NULL CHECK(provenance_kind IN ('accepted_model_finding','human_observation')),
    assessment_result_id TEXT, finding_id TEXT, disposition_id TEXT, result_fingerprint TEXT, finding_fingerprint TEXT, disposition_fingerprint TEXT,
    observation_id TEXT, observation_fingerprint TEXT, draft_fingerprint TEXT NOT NULL, status_fingerprint TEXT,
    source_scope_fingerprint TEXT NOT NULL, successor_scope_fingerprint TEXT NOT NULL, selected INTEGER NOT NULL CHECK(selected IN (0,1)),
    reason_code TEXT NOT NULL CHECK(reason_code IN ('approved_scope_match','reuse_not_requested','guidance_unapproved','guidance_inactive','guidance_superseded','guidance_scope_mismatch')),
    decision_fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,decision_id), UNIQUE(run_id,snapshot_id,source_run_id,guidance_id,draft_revision),
    CHECK((status_revision_id IS NULL AND status_fingerprint IS NULL) OR (status_revision_id IS NOT NULL AND status_fingerprint IS NOT NULL)),
    CHECK((selected=1 AND reason_code='approved_scope_match') OR (selected=0 AND reason_code<>'approved_scope_match')),
    CHECK((provenance_kind='accepted_model_finding' AND assessment_result_id IS NOT NULL AND finding_id IS NOT NULL AND disposition_id IS NOT NULL AND result_fingerprint IS NOT NULL AND finding_fingerprint IS NOT NULL AND disposition_fingerprint IS NOT NULL AND observation_id IS NULL AND observation_fingerprint IS NULL) OR (provenance_kind='human_observation' AND observation_id IS NOT NULL AND observation_fingerprint IS NOT NULL AND finding_id IS NULL AND finding_fingerprint IS NULL AND disposition_id IS NULL AND disposition_fingerprint IS NULL)),
    FOREIGN KEY(run_id,snapshot_id) REFERENCES run_guidance_snapshots(run_id,snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,assessment_result_id) REFERENCES post_final_assessment_results(run_id,assessment_result_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,disposition_id) REFERENCES post_final_finding_dispositions(run_id,disposition_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,observation_id) REFERENCES post_final_human_observations(run_id,observation_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,guidance_id,draft_revision) REFERENCES post_final_guidance_drafts(run_id,guidance_id,draft_revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,status_revision_id) REFERENCES post_final_guidance_statuses(run_id,status_revision_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE run_guidance_snapshot_items (
    run_id TEXT NOT NULL, item_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0), source_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.run_guidance_snapshot_item_record.v1'), finalized_lineage_fingerprint TEXT NOT NULL,
    provenance_kind TEXT NOT NULL CHECK(provenance_kind IN ('accepted_model_finding','human_observation')),
    assessment_result_id TEXT, assessment_result_fingerprint TEXT, finding_id TEXT, finding_fingerprint TEXT, disposition_id TEXT, disposition_fingerprint TEXT,
    observation_id TEXT, observation_fingerprint TEXT, guidance_id TEXT NOT NULL, draft_revision INTEGER NOT NULL CHECK(draft_revision>0), draft_fingerprint TEXT NOT NULL,
    status_revision_id TEXT NOT NULL, status_fingerprint TEXT NOT NULL, guidance_text TEXT NOT NULL, guidance_sha256 TEXT NOT NULL,
    reuse_scope_fingerprint TEXT NOT NULL, item_fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,item_id), UNIQUE(run_id,snapshot_id,position), UNIQUE(run_id,snapshot_id,source_run_id,guidance_id,draft_revision),
    CHECK((provenance_kind='accepted_model_finding' AND assessment_result_id IS NOT NULL AND finding_id IS NOT NULL AND disposition_id IS NOT NULL AND assessment_result_fingerprint IS NOT NULL AND finding_fingerprint IS NOT NULL AND disposition_fingerprint IS NOT NULL AND observation_id IS NULL AND observation_fingerprint IS NULL) OR (provenance_kind='human_observation' AND observation_id IS NOT NULL AND observation_fingerprint IS NOT NULL AND finding_id IS NULL AND finding_fingerprint IS NULL AND disposition_id IS NULL AND disposition_fingerprint IS NULL)),
    FOREIGN KEY(run_id,snapshot_id) REFERENCES run_guidance_snapshots(run_id,snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,assessment_result_id) REFERENCES post_final_assessment_results(run_id,assessment_result_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,disposition_id) REFERENCES post_final_finding_dispositions(run_id,disposition_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,observation_id) REFERENCES post_final_human_observations(run_id,observation_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,guidance_id,draft_revision) REFERENCES post_final_guidance_drafts(run_id,guidance_id,draft_revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,status_revision_id) REFERENCES post_final_guidance_statuses(run_id,status_revision_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE run_guidance_snapshot_decisions (
    run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0), decision_id TEXT NOT NULL,
    PRIMARY KEY(run_id,snapshot_id,position), UNIQUE(run_id,snapshot_id,decision_id),
    FOREIGN KEY(run_id,snapshot_id) REFERENCES run_guidance_snapshots(run_id,snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,decision_id) REFERENCES run_guidance_selection_decisions(run_id,decision_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE run_guidance_snapshot_selected_items (
    run_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0), item_id TEXT NOT NULL,
    PRIMARY KEY(run_id,snapshot_id,position), UNIQUE(run_id,snapshot_id,item_id),
    FOREIGN KEY(run_id,snapshot_id) REFERENCES run_guidance_snapshots(run_id,snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,item_id) REFERENCES run_guidance_snapshot_items(run_id,item_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE transaction_run_guidance_snapshots (
    run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0), snapshot_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position), UNIQUE(run_id,snapshot_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,snapshot_id) REFERENCES run_guidance_snapshots(run_id,snapshot_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE transaction_run_guidance_selection_decisions (
    run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0), decision_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position), UNIQUE(run_id,decision_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,decision_id) REFERENCES run_guidance_selection_decisions(run_id,decision_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE transaction_run_guidance_snapshot_items (
    run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0), item_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position), UNIQUE(run_id,item_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,item_id) REFERENCES run_guidance_snapshot_items(run_id,item_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TRIGGER post_final_human_observations_no_update BEFORE UPDATE ON post_final_human_observations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_human_observations_no_delete BEFORE DELETE ON post_final_human_observations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_human_observations_no_update BEFORE UPDATE ON transaction_post_final_human_observations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_human_observations_no_delete BEFORE DELETE ON transaction_post_final_human_observations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_guidance_drafts_v15_no_update BEFORE UPDATE ON post_final_guidance_drafts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_guidance_drafts_v15_no_delete BEFORE DELETE ON post_final_guidance_drafts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_guidance_statuses_v15_no_update BEFORE UPDATE ON post_final_guidance_statuses BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER post_final_guidance_statuses_v15_no_delete BEFORE DELETE ON post_final_guidance_statuses BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_guidance_drafts_v15_no_update BEFORE UPDATE ON transaction_post_final_guidance_drafts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_guidance_drafts_v15_no_delete BEFORE DELETE ON transaction_post_final_guidance_drafts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_guidance_statuses_v15_no_update BEFORE UPDATE ON transaction_post_final_guidance_statuses BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_post_final_guidance_statuses_v15_no_delete BEFORE DELETE ON transaction_post_final_guidance_statuses BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_selection_decisions_v15_no_update BEFORE UPDATE ON run_guidance_selection_decisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_selection_decisions_v15_no_delete BEFORE DELETE ON run_guidance_selection_decisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_snapshot_items_v15_no_update BEFORE UPDATE ON run_guidance_snapshot_items BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_snapshot_items_v15_no_delete BEFORE DELETE ON run_guidance_snapshot_items BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_snapshot_decisions_v15_no_update BEFORE UPDATE ON run_guidance_snapshot_decisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_snapshot_decisions_v15_no_delete BEFORE DELETE ON run_guidance_snapshot_decisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_snapshot_selected_items_v15_no_update BEFORE UPDATE ON run_guidance_snapshot_selected_items BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_snapshot_selected_items_v15_no_delete BEFORE DELETE ON run_guidance_snapshot_selected_items BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_guidance_snapshots_v15_no_update BEFORE UPDATE ON transaction_run_guidance_snapshots BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_guidance_snapshots_v15_no_delete BEFORE DELETE ON transaction_run_guidance_snapshots BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_guidance_selection_decisions_v15_no_update BEFORE UPDATE ON transaction_run_guidance_selection_decisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_guidance_selection_decisions_v15_no_delete BEFORE DELETE ON transaction_run_guidance_selection_decisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_guidance_snapshot_items_v15_no_update BEFORE UPDATE ON transaction_run_guidance_snapshot_items BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_guidance_snapshot_items_v15_no_delete BEFORE DELETE ON transaction_run_guidance_snapshot_items BEGIN SELECT RAISE(ABORT,'append_only'); END;

INSERT INTO schema_migrations(version,name) VALUES(15,'0015');
PRAGMA user_version=15;
COMMIT;
