BEGIN IMMEDIATE;

PRAGMA legacy_alter_table=ON;

ALTER TABLE stage_transitions RENAME TO stage_transitions_v7;
CREATE TABLE stage_transitions (
    run_id TEXT NOT NULL,
    transition_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.stage_transition_record.v2'),
    stage_id TEXT NOT NULL,
    transition_kind TEXT NOT NULL CHECK(transition_kind IN ('initialize','activate','complete','satisfied_by_topology','repair_reopen','gate_repair_reopen','gate_repair_reset')),
    prior_status TEXT,
    prior_revision INTEGER,
    result_status TEXT NOT NULL,
    result_revision INTEGER NOT NULL CHECK(result_revision>=0),
    run_contract_fingerprint TEXT NOT NULL,
    transition_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,transition_id),
    UNIQUE(run_id,stage_id,result_revision),
    CHECK((transition_kind='initialize' AND prior_status IS NULL AND prior_revision IS NULL AND result_revision=0) OR (transition_kind!='initialize' AND prior_status IS NOT NULL AND prior_revision IS NOT NULL AND result_revision=prior_revision+1)),
    FOREIGN KEY(run_id,transition_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);
INSERT INTO stage_transitions SELECT * FROM stage_transitions_v7;
DROP TABLE stage_transitions_v7;

PRAGMA legacy_alter_table=OFF;

CREATE TABLE gate_repair_cycles (
    run_id TEXT NOT NULL,
    gate_repair_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.gate_repair_cycle_record.v2'),
    authorization_id TEXT NOT NULL,
    repair_ordinal INTEGER NOT NULL CHECK(repair_ordinal=1),
    source_gate_batch_id TEXT NOT NULL,
    source_stage_id TEXT NOT NULL CHECK(source_stage_id IN ('auditor','finalize')),
    repair_owner TEXT NOT NULL CHECK(repair_owner='editor'),
    target_artifact_id TEXT NOT NULL CHECK(target_artifact_id='audited_brief'),
    target_artifact_revision INTEGER NOT NULL CHECK(target_artifact_revision>0),
    started_at TEXT NOT NULL,
    start_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,gate_repair_id),
    UNIQUE(run_id),
    FOREIGN KEY(run_id,authorization_id) REFERENCES run_execution_authorizations(run_id,authorization_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,target_artifact_id,target_artifact_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,start_event_id) REFERENCES events(run_id,event_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id,start_event_id) REFERENCES transaction_events(run_id,transaction_id,event_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE gate_repair_cycle_evaluations (
    run_id TEXT NOT NULL,
    gate_repair_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    evaluation_id TEXT NOT NULL,
    PRIMARY KEY(run_id,gate_repair_id,position),
    UNIQUE(run_id,gate_repair_id,evaluation_id),
    FOREIGN KEY(run_id,gate_repair_id) REFERENCES gate_repair_cycles(run_id,gate_repair_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,evaluation_id) REFERENCES gate_evaluations(run_id,evaluation_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE gate_repair_cycle_findings (
    run_id TEXT NOT NULL,
    gate_repair_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    evaluation_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    PRIMARY KEY(run_id,gate_repair_id,position),
    UNIQUE(run_id,gate_repair_id,evaluation_id,finding_id),
    FOREIGN KEY(run_id,gate_repair_id) REFERENCES gate_repair_cycles(run_id,gate_repair_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,evaluation_id,finding_id) REFERENCES gate_findings(run_id,evaluation_id,finding_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE gate_repair_cycle_transitions (
    run_id TEXT NOT NULL,
    gate_repair_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    transition_id TEXT NOT NULL,
    PRIMARY KEY(run_id,gate_repair_id,position),
    UNIQUE(run_id,gate_repair_id,transition_id),
    FOREIGN KEY(run_id,gate_repair_id) REFERENCES gate_repair_cycles(run_id,gate_repair_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,transition_id) REFERENCES stage_transitions(run_id,transition_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE gate_repair_artifact_bindings (
    run_id TEXT NOT NULL,
    gate_repair_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.gate_repair_artifact_binding.v2'),
    prior_artifact_id TEXT NOT NULL CHECK(prior_artifact_id='audited_brief'),
    prior_artifact_revision INTEGER NOT NULL CHECK(prior_artifact_revision>0),
    successor_artifact_id TEXT NOT NULL CHECK(successor_artifact_id='audited_brief'),
    successor_artifact_revision INTEGER NOT NULL CHECK(successor_artifact_revision=prior_artifact_revision+1),
    owned_artifact_submission_id TEXT NOT NULL,
    accepted_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,gate_repair_id),
    UNIQUE(run_id,successor_artifact_id,successor_artifact_revision),
    UNIQUE(run_id,owned_artifact_submission_id),
    FOREIGN KEY(run_id,gate_repair_id) REFERENCES gate_repair_cycles(run_id,gate_repair_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,prior_artifact_id,prior_artifact_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,successor_artifact_id,successor_artifact_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,owned_artifact_submission_id) REFERENCES owned_artifact_submissions(run_id,submission_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_event_id) REFERENCES events(run_id,event_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id,accepted_event_id) REFERENCES transaction_events(run_id,transaction_id,event_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id,successor_artifact_id,successor_artifact_revision) REFERENCES transaction_artifact_revisions(run_id,transaction_id,artifact_id,revision) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE gate_repair_outcomes (
    run_id TEXT NOT NULL,
    outcome_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.gate_repair_outcome_record.v2'),
    gate_repair_id TEXT NOT NULL,
    replacement_gate_batch_id TEXT NOT NULL,
    replacement_stage_id TEXT NOT NULL CHECK(replacement_stage_id IN ('auditor','finalize')),
    disposition TEXT NOT NULL CHECK(disposition IN ('passed','blocked')),
    completed_at TEXT NOT NULL,
    completion_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,outcome_id),
    UNIQUE(run_id,gate_repair_id),
    FOREIGN KEY(run_id,gate_repair_id) REFERENCES gate_repair_cycles(run_id,gate_repair_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,completion_event_id) REFERENCES events(run_id,event_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id,completion_event_id) REFERENCES transaction_events(run_id,transaction_id,event_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE gate_repair_outcome_evaluations (
    run_id TEXT NOT NULL,
    outcome_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    evaluation_id TEXT NOT NULL,
    PRIMARY KEY(run_id,outcome_id,position),
    UNIQUE(run_id,outcome_id,evaluation_id),
    FOREIGN KEY(run_id,outcome_id) REFERENCES gate_repair_outcomes(run_id,outcome_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,evaluation_id) REFERENCES gate_evaluations(run_id,evaluation_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_gate_repair_cycles (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    gate_repair_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,gate_repair_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,gate_repair_id) REFERENCES gate_repair_cycles(run_id,gate_repair_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_gate_repair_artifact_bindings (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    gate_repair_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,gate_repair_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,gate_repair_id) REFERENCES gate_repair_artifact_bindings(run_id,gate_repair_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_gate_repair_outcomes (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    outcome_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,outcome_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,outcome_id) REFERENCES gate_repair_outcomes(run_id,outcome_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);

CREATE TRIGGER gate_repair_cycles_no_update BEFORE UPDATE ON gate_repair_cycles BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_repair_cycles_no_delete BEFORE DELETE ON gate_repair_cycles BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_repair_cycle_evaluations_no_update BEFORE UPDATE ON gate_repair_cycle_evaluations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_repair_cycle_evaluations_no_delete BEFORE DELETE ON gate_repair_cycle_evaluations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_repair_cycle_findings_no_update BEFORE UPDATE ON gate_repair_cycle_findings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_repair_cycle_findings_no_delete BEFORE DELETE ON gate_repair_cycle_findings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_repair_cycle_transitions_no_update BEFORE UPDATE ON gate_repair_cycle_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_repair_cycle_transitions_no_delete BEFORE DELETE ON gate_repair_cycle_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_repair_artifact_bindings_no_update BEFORE UPDATE ON gate_repair_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_repair_artifact_bindings_no_delete BEFORE DELETE ON gate_repair_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_repair_outcomes_no_update BEFORE UPDATE ON gate_repair_outcomes BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_repair_outcomes_no_delete BEFORE DELETE ON gate_repair_outcomes BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_repair_outcome_evaluations_no_update BEFORE UPDATE ON gate_repair_outcome_evaluations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_repair_outcome_evaluations_no_delete BEFORE DELETE ON gate_repair_outcome_evaluations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_gate_repair_cycles_no_update BEFORE UPDATE ON transaction_gate_repair_cycles BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_gate_repair_cycles_no_delete BEFORE DELETE ON transaction_gate_repair_cycles BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_gate_repair_artifact_bindings_no_update BEFORE UPDATE ON transaction_gate_repair_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_gate_repair_artifact_bindings_no_delete BEFORE DELETE ON transaction_gate_repair_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_gate_repair_outcomes_no_update BEFORE UPDATE ON transaction_gate_repair_outcomes BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_gate_repair_outcomes_no_delete BEFORE DELETE ON transaction_gate_repair_outcomes BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER stage_transitions_no_update BEFORE UPDATE ON stage_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER stage_transitions_no_delete BEFORE DELETE ON stage_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;

INSERT INTO schema_migrations(version,name) VALUES(8,'0008');
PRAGMA user_version=8;
COMMIT;
