BEGIN IMMEDIATE;

-- Schema 13 is installed only while creating a fresh Store.  Rebuild the
-- transition table so recovery reset and normal Human-started successors have
-- distinct, exact authority meanings.
PRAGMA legacy_alter_table=ON;

DROP TRIGGER run_head_transitions_no_update;
DROP TRIGGER run_head_transitions_no_delete;

ALTER TABLE run_head_transitions RENAME TO run_head_transitions_v12;

CREATE TABLE run_head_transitions (
    workspace_id TEXT NOT NULL,
    head_transition_id TEXT NOT NULL,
    successor_run_id TEXT NOT NULL,
    predecessor_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL
        CHECK(schema_version='briefloop.run_head_transition_record.v2'),
    prior_workspace_revision INTEGER NOT NULL
        CHECK(typeof(prior_workspace_revision)='integer' AND prior_workspace_revision>=0),
    successor_workspace_revision INTEGER NOT NULL
        CHECK(typeof(successor_workspace_revision)='integer' AND successor_workspace_revision>0),
    reason_code TEXT NOT NULL
        CHECK(reason_code IN ('run_reset','human_started_successor')),
    successor_disposition TEXT NOT NULL
        CHECK(successor_disposition IN ('non_reference','reference')),
    created_at TEXT NOT NULL,
    transition_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL
        CHECK(length(request_fingerprint)=64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id,head_transition_id),
    UNIQUE(workspace_id,successor_run_id),
    CHECK(predecessor_run_id<>successor_run_id),
    CHECK(successor_workspace_revision=prior_workspace_revision+1),
    CHECK(
        (reason_code='run_reset' AND successor_disposition='non_reference')
        OR
        (reason_code='human_started_successor' AND successor_disposition='reference')
    ),
    FOREIGN KEY(workspace_id,successor_run_id)
        REFERENCES runs(workspace_id,run_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(workspace_id,predecessor_run_id)
        REFERENCES runs(workspace_id,run_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(successor_run_id,transition_event_id)
        REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(successor_run_id,accepted_transaction_id)
        REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);

INSERT INTO run_head_transitions SELECT * FROM run_head_transitions_v12;
DROP TABLE run_head_transitions_v12;

PRAGMA legacy_alter_table=OFF;

CREATE TRIGGER run_head_transitions_no_update
BEFORE UPDATE ON run_head_transitions
BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_head_transitions_no_delete
BEFORE DELETE ON run_head_transitions
BEGIN SELECT RAISE(ABORT,'append_only'); END;

CREATE TABLE run_guidance_snapshots (
    run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    predecessor_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL
        CHECK(schema_version='briefloop.run_guidance_snapshot_record.v1'),
    reuse_requested INTEGER NOT NULL CHECK(reuse_requested IN (0,1)),
    successor_direction_fingerprint TEXT NOT NULL
        CHECK(length(successor_direction_fingerprint)=64 AND successor_direction_fingerprint NOT GLOB '*[^0-9a-f]*'),
    successor_run_contract_fingerprint TEXT NOT NULL
        CHECK(length(successor_run_contract_fingerprint)=64 AND successor_run_contract_fingerprint NOT GLOB '*[^0-9a-f]*'),
    candidate_set_fingerprint TEXT NOT NULL
        CHECK(length(candidate_set_fingerprint)=64 AND candidate_set_fingerprint NOT GLOB '*[^0-9a-f]*'),
    selected_count INTEGER NOT NULL CHECK(selected_count>=0),
    omitted_count INTEGER NOT NULL CHECK(omitted_count>=0),
    snapshot_fingerprint TEXT NOT NULL
        CHECK(length(snapshot_fingerprint)=64 AND snapshot_fingerprint NOT GLOB '*[^0-9a-f]*'),
    snapshot_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL
        CHECK(length(request_fingerprint)=64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,snapshot_id),
    UNIQUE(run_id),
    CHECK(predecessor_run_id<>run_id),
    CHECK(reuse_requested=1 OR selected_count=0),
    FOREIGN KEY(workspace_id,run_id)
        REFERENCES runs(workspace_id,run_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(workspace_id,predecessor_run_id)
        REFERENCES runs(workspace_id,run_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,snapshot_event_id)
        REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id)
        REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id,snapshot_event_id)
        REFERENCES transaction_events(run_id,transaction_id,event_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE run_guidance_selection_decisions (
    run_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL
        CHECK(schema_version='briefloop.run_guidance_selection_decision_record.v1'),
    guidance_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL CHECK(draft_revision>0),
    status_revision_id TEXT,
    assessment_result_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    disposition_id TEXT NOT NULL,
    result_fingerprint TEXT NOT NULL
        CHECK(length(result_fingerprint)=64 AND result_fingerprint NOT GLOB '*[^0-9a-f]*'),
    finding_fingerprint TEXT NOT NULL
        CHECK(length(finding_fingerprint)=64 AND finding_fingerprint NOT GLOB '*[^0-9a-f]*'),
    disposition_fingerprint TEXT NOT NULL
        CHECK(length(disposition_fingerprint)=64 AND disposition_fingerprint NOT GLOB '*[^0-9a-f]*'),
    draft_fingerprint TEXT NOT NULL
        CHECK(length(draft_fingerprint)=64 AND draft_fingerprint NOT GLOB '*[^0-9a-f]*'),
    status_fingerprint TEXT,
    source_scope_fingerprint TEXT NOT NULL
        CHECK(length(source_scope_fingerprint)=64 AND source_scope_fingerprint NOT GLOB '*[^0-9a-f]*'),
    successor_scope_fingerprint TEXT NOT NULL
        CHECK(length(successor_scope_fingerprint)=64 AND successor_scope_fingerprint NOT GLOB '*[^0-9a-f]*'),
    selected INTEGER NOT NULL CHECK(selected IN (0,1)),
    reason_code TEXT NOT NULL CHECK(reason_code IN (
        'approved_scope_match',
        'reuse_not_requested',
        'guidance_unapproved',
        'guidance_inactive',
        'guidance_superseded',
        'guidance_scope_mismatch'
    )),
    decision_fingerprint TEXT NOT NULL
        CHECK(length(decision_fingerprint)=64 AND decision_fingerprint NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,decision_id),
    UNIQUE(run_id,snapshot_id,source_run_id,guidance_id,draft_revision),
    CHECK(
        (status_revision_id IS NULL AND status_fingerprint IS NULL)
        OR
        (status_revision_id IS NOT NULL AND status_fingerprint IS NOT NULL)
    ),
    CHECK(
        (selected=1 AND reason_code='approved_scope_match')
        OR
        (selected=0 AND reason_code<>'approved_scope_match')
    ),
    FOREIGN KEY(run_id,snapshot_id)
        REFERENCES run_guidance_snapshots(run_id,snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,assessment_result_id)
        REFERENCES post_final_assessment_results(run_id,assessment_result_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,disposition_id)
        REFERENCES post_final_finding_dispositions(run_id,disposition_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,guidance_id,draft_revision)
        REFERENCES post_final_guidance_drafts(run_id,guidance_id,draft_revision)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,status_revision_id)
        REFERENCES post_final_guidance_statuses(run_id,status_revision_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE run_guidance_snapshot_items (
    run_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    source_run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL
        CHECK(schema_version='briefloop.run_guidance_snapshot_item_record.v1'),
    finalized_lineage_fingerprint TEXT NOT NULL
        CHECK(length(finalized_lineage_fingerprint)=64 AND finalized_lineage_fingerprint NOT GLOB '*[^0-9a-f]*'),
    assessment_result_id TEXT NOT NULL,
    assessment_result_fingerprint TEXT NOT NULL
        CHECK(length(assessment_result_fingerprint)=64 AND assessment_result_fingerprint NOT GLOB '*[^0-9a-f]*'),
    finding_id TEXT NOT NULL,
    finding_fingerprint TEXT NOT NULL
        CHECK(length(finding_fingerprint)=64 AND finding_fingerprint NOT GLOB '*[^0-9a-f]*'),
    disposition_id TEXT NOT NULL,
    disposition_fingerprint TEXT NOT NULL
        CHECK(length(disposition_fingerprint)=64 AND disposition_fingerprint NOT GLOB '*[^0-9a-f]*'),
    guidance_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL CHECK(draft_revision>0),
    draft_fingerprint TEXT NOT NULL
        CHECK(length(draft_fingerprint)=64 AND draft_fingerprint NOT GLOB '*[^0-9a-f]*'),
    status_revision_id TEXT NOT NULL,
    status_fingerprint TEXT NOT NULL
        CHECK(length(status_fingerprint)=64 AND status_fingerprint NOT GLOB '*[^0-9a-f]*'),
    guidance_text TEXT NOT NULL,
    guidance_sha256 TEXT NOT NULL
        CHECK(length(guidance_sha256)=64 AND guidance_sha256 NOT GLOB '*[^0-9a-f]*'),
    reuse_scope_fingerprint TEXT NOT NULL
        CHECK(length(reuse_scope_fingerprint)=64 AND reuse_scope_fingerprint NOT GLOB '*[^0-9a-f]*'),
    item_fingerprint TEXT NOT NULL
        CHECK(length(item_fingerprint)=64 AND item_fingerprint NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,item_id),
    UNIQUE(run_id,snapshot_id,position),
    UNIQUE(run_id,snapshot_id,source_run_id,guidance_id,draft_revision),
    FOREIGN KEY(run_id,snapshot_id)
        REFERENCES run_guidance_snapshots(run_id,snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,assessment_result_id)
        REFERENCES post_final_assessment_results(run_id,assessment_result_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,disposition_id)
        REFERENCES post_final_finding_dispositions(run_id,disposition_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,guidance_id,draft_revision)
        REFERENCES post_final_guidance_drafts(run_id,guidance_id,draft_revision)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(source_run_id,status_revision_id)
        REFERENCES post_final_guidance_statuses(run_id,status_revision_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE run_guidance_snapshot_decisions (
    run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    decision_id TEXT NOT NULL,
    PRIMARY KEY(run_id,snapshot_id,position),
    UNIQUE(run_id,snapshot_id,decision_id),
    FOREIGN KEY(run_id,snapshot_id)
        REFERENCES run_guidance_snapshots(run_id,snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,decision_id)
        REFERENCES run_guidance_selection_decisions(run_id,decision_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE run_guidance_snapshot_selected_items (
    run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    item_id TEXT NOT NULL,
    PRIMARY KEY(run_id,snapshot_id,position),
    UNIQUE(run_id,snapshot_id,item_id),
    FOREIGN KEY(run_id,snapshot_id)
        REFERENCES run_guidance_snapshots(run_id,snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,item_id)
        REFERENCES run_guidance_snapshot_items(run_id,item_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_run_guidance_snapshots (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    snapshot_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,snapshot_id),
    FOREIGN KEY(run_id,transaction_id)
        REFERENCES transactions(run_id,transaction_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,snapshot_id)
        REFERENCES run_guidance_snapshots(run_id,snapshot_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_run_guidance_selection_decisions (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    decision_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,decision_id),
    FOREIGN KEY(run_id,transaction_id)
        REFERENCES transactions(run_id,transaction_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,decision_id)
        REFERENCES run_guidance_selection_decisions(run_id,decision_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_run_guidance_snapshot_items (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    item_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,item_id),
    FOREIGN KEY(run_id,transaction_id)
        REFERENCES transactions(run_id,transaction_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,item_id)
        REFERENCES run_guidance_snapshot_items(run_id,item_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TRIGGER run_guidance_snapshots_no_update BEFORE UPDATE ON run_guidance_snapshots BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_snapshots_no_delete BEFORE DELETE ON run_guidance_snapshots BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_selection_decisions_no_update BEFORE UPDATE ON run_guidance_selection_decisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_selection_decisions_no_delete BEFORE DELETE ON run_guidance_selection_decisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_snapshot_items_no_update BEFORE UPDATE ON run_guidance_snapshot_items BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_snapshot_items_no_delete BEFORE DELETE ON run_guidance_snapshot_items BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_snapshot_decisions_no_update BEFORE UPDATE ON run_guidance_snapshot_decisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_snapshot_decisions_no_delete BEFORE DELETE ON run_guidance_snapshot_decisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_snapshot_selected_items_no_update BEFORE UPDATE ON run_guidance_snapshot_selected_items BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_guidance_snapshot_selected_items_no_delete BEFORE DELETE ON run_guidance_snapshot_selected_items BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_guidance_snapshots_no_update BEFORE UPDATE ON transaction_run_guidance_snapshots BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_guidance_snapshots_no_delete BEFORE DELETE ON transaction_run_guidance_snapshots BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_guidance_selection_decisions_no_update BEFORE UPDATE ON transaction_run_guidance_selection_decisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_guidance_selection_decisions_no_delete BEFORE DELETE ON transaction_run_guidance_selection_decisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_guidance_snapshot_items_no_update BEFORE UPDATE ON transaction_run_guidance_snapshot_items BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_guidance_snapshot_items_no_delete BEFORE DELETE ON transaction_run_guidance_snapshot_items BEGIN SELECT RAISE(ABORT,'append_only'); END;

INSERT INTO schema_migrations(version,name) VALUES(13,'0013');
PRAGMA user_version=13;
COMMIT;
