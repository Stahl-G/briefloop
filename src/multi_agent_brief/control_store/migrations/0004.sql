BEGIN IMMEDIATE;

INSERT INTO schema_migrations(version, name) VALUES (4, '0004');

PRAGMA legacy_alter_table=ON;

ALTER TABLE stage_transitions RENAME TO stage_transitions_v3;
CREATE TABLE stage_transitions (
  run_id TEXT NOT NULL, transition_id TEXT NOT NULL, schema_version TEXT NOT NULL CHECK(schema_version='briefloop.stage_transition_record.v2'),
  stage_id TEXT NOT NULL, transition_kind TEXT NOT NULL CHECK(transition_kind IN ('initialize','activate','complete','satisfied_by_topology','repair_reopen')),
  prior_status TEXT, prior_revision INTEGER, result_status TEXT NOT NULL, result_revision INTEGER NOT NULL CHECK(result_revision>=0),
  run_contract_fingerprint TEXT NOT NULL, transition_event_id TEXT NOT NULL, accepted_transaction_id TEXT NOT NULL,
  request_fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id,transition_id), UNIQUE(run_id,stage_id,result_revision),
  CHECK((transition_kind='initialize' AND prior_status IS NULL AND prior_revision IS NULL AND result_revision=0) OR (transition_kind!='initialize' AND prior_status IS NOT NULL AND prior_revision IS NOT NULL AND result_revision=prior_revision+1)),
  FOREIGN KEY(run_id,transition_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);
INSERT INTO stage_transitions SELECT * FROM stage_transitions_v3;
DROP TABLE stage_transitions_v3;

ALTER TABLE gate_evaluations RENAME TO gate_evaluations_v3;
CREATE TABLE gate_evaluations (
  run_id TEXT NOT NULL, evaluation_id TEXT NOT NULL, schema_version TEXT NOT NULL CHECK(schema_version='briefloop.gate_evaluation_record.v2'),
  gate_batch_id TEXT NOT NULL, stage_id TEXT NOT NULL CHECK(stage_id IN ('auditor','finalize')), gate_id TEXT NOT NULL,
  policy_version TEXT NOT NULL, run_contract_fingerprint TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pass','warning','fail','unavailable','invalid')),
  blocking INTEGER NOT NULL CHECK(blocking IN (0,1)), report_artifact_id TEXT NOT NULL, report_artifact_revision INTEGER NOT NULL,
  evaluation_event_id TEXT NOT NULL, accepted_transaction_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id,evaluation_id), UNIQUE(run_id,gate_batch_id,gate_id),
  FOREIGN KEY(run_id,report_artifact_id,report_artifact_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,evaluation_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);
INSERT INTO gate_evaluations SELECT * FROM gate_evaluations_v3;
DROP TABLE gate_evaluations_v3;

ALTER TABLE gate_artifact_bindings RENAME TO gate_artifact_bindings_v3;
CREATE TABLE gate_artifact_bindings (
  run_id TEXT NOT NULL,evaluation_id TEXT NOT NULL,position INTEGER NOT NULL CHECK(position>=0),schema_version TEXT NOT NULL CHECK(schema_version='briefloop.gate_artifact_binding.v2'),
  artifact_id TEXT NOT NULL,artifact_revision INTEGER NOT NULL,artifact_sha256 TEXT NOT NULL,
  usage TEXT NOT NULL CHECK(usage IN ('brief','ledger','analyst_snapshot','screened_candidates','reader_artifact','audit_report')),
  accepted_transaction_id TEXT NOT NULL,payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id,evaluation_id,position),UNIQUE(run_id,evaluation_id,artifact_id,artifact_revision),
  FOREIGN KEY(run_id,evaluation_id) REFERENCES gate_evaluations(run_id,evaluation_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,artifact_id,artifact_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);
INSERT INTO gate_artifact_bindings SELECT * FROM gate_artifact_bindings_v3;
DROP TABLE gate_artifact_bindings_v3;
PRAGMA legacy_alter_table=OFF;

CREATE TRIGGER stage_transitions_no_update BEFORE UPDATE ON stage_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER stage_transitions_no_delete BEFORE DELETE ON stage_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_evaluations_no_update BEFORE UPDATE ON gate_evaluations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_evaluations_no_delete BEFORE DELETE ON gate_evaluations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_artifact_bindings_no_update BEFORE UPDATE ON gate_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER gate_artifact_bindings_no_delete BEFORE DELETE ON gate_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;

CREATE TABLE artifact_identities (
  run_id TEXT NOT NULL CHECK(typeof(run_id)='text' AND length(run_id)>0),
  artifact_id TEXT NOT NULL CHECK(typeof(artifact_id)='text' AND length(artifact_id)>0),
  schema_version TEXT NOT NULL CHECK(schema_version='briefloop.artifact_identity_record.v2'),
  required INTEGER NOT NULL CHECK(typeof(required)='integer' AND required IN (0,1)),
  initial_path TEXT NOT NULL CHECK(typeof(initial_path)='text' AND length(initial_path)>0),
  format TEXT NOT NULL CHECK(typeof(format)='text' AND format IN ('json','yaml','markdown','html','docx','pdf','text','binary')),
  accepted_transaction_id TEXT NOT NULL CHECK(typeof(accepted_transaction_id)='text' AND length(accepted_transaction_id)>0),
  payload_json TEXT NOT NULL CHECK(typeof(payload_json)='text'),
  PRIMARY KEY(run_id,artifact_id),
  UNIQUE(run_id,artifact_id,accepted_transaction_id),
  FOREIGN KEY(run_id) REFERENCES runs(run_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,artifact_id) REFERENCES artifacts(run_id,artifact_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE transaction_artifact_identities (
  run_id TEXT NOT NULL CHECK(typeof(run_id)='text' AND length(run_id)>0),
  transaction_id TEXT NOT NULL CHECK(typeof(transaction_id)='text' AND length(transaction_id)>0),
  position INTEGER NOT NULL CHECK(typeof(position)='integer' AND position>=0),
  artifact_id TEXT NOT NULL CHECK(typeof(artifact_id)='text' AND length(artifact_id)>0),
  PRIMARY KEY(run_id,transaction_id,position),
  UNIQUE(run_id,artifact_id),
  FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,artifact_id,transaction_id) REFERENCES artifact_identities(run_id,artifact_id,accepted_transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
);
CREATE TRIGGER artifact_identities_no_update BEFORE UPDATE ON artifact_identities BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER artifact_identities_no_delete BEFORE DELETE ON artifact_identities BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_artifact_identities_no_update BEFORE UPDATE ON transaction_artifact_identities BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_artifact_identities_no_delete BEFORE DELETE ON transaction_artifact_identities BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER artifact_identities_match_artifact_before_insert
BEFORE INSERT ON artifact_identities
WHEN NOT EXISTS (
  SELECT 1 FROM artifacts
  WHERE run_id=NEW.run_id AND artifact_id=NEW.artifact_id
    AND required=NEW.required AND format=NEW.format AND path=NEW.initial_path
)
BEGIN SELECT RAISE(ABORT,'artifact_identity_mismatch'); END;
CREATE TRIGGER artifacts_identity_fields_match_before_update
BEFORE UPDATE OF required,format ON artifacts
WHEN EXISTS (
  SELECT 1 FROM artifact_identities
  WHERE run_id=NEW.run_id AND artifact_id=NEW.artifact_id
) AND NOT EXISTS (
  SELECT 1 FROM artifact_identities
  WHERE run_id=NEW.run_id AND artifact_id=NEW.artifact_id
    AND required=NEW.required AND format=NEW.format
)
BEGIN SELECT RAISE(ABORT,'artifact_identity_mismatch'); END;

CREATE TABLE repair_cycles (
  run_id TEXT NOT NULL,repair_id TEXT NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.repair_cycle_record.v2'),
  contamination_revision INTEGER NOT NULL,owner_stage_id TEXT NOT NULL,reason_code TEXT NOT NULL,started_at TEXT NOT NULL,
  start_event_id TEXT NOT NULL,accepted_transaction_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id,repair_id),FOREIGN KEY(run_id,contamination_revision) REFERENCES run_integrity_records(run_id,integrity_revision) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,start_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE artifact_supersessions (
  run_id TEXT NOT NULL,supersession_id TEXT NOT NULL,repair_id TEXT NOT NULL,mode TEXT NOT NULL CHECK(mode IN ('repair','supersede','revert')),
  schema_version TEXT NOT NULL CHECK(schema_version='briefloop.artifact_supersession_record.v2'),artifact_id TEXT NOT NULL,prior_revision INTEGER NOT NULL,successor_revision INTEGER NOT NULL,
  reason_code TEXT NOT NULL,created_at TEXT NOT NULL,accepted_event_id TEXT NOT NULL,accepted_transaction_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id,supersession_id),UNIQUE(run_id,artifact_id,successor_revision),CHECK(successor_revision=prior_revision+1),
  FOREIGN KEY(run_id,repair_id) REFERENCES repair_cycles(run_id,repair_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,artifact_id,prior_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,artifact_id,successor_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,accepted_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE repair_completions (
  run_id TEXT NOT NULL,repair_completion_id TEXT NOT NULL,repair_id TEXT NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.repair_completion_record.v2'),
  contamination_revision INTEGER NOT NULL,completed_at TEXT NOT NULL,completion_event_id TEXT NOT NULL,accepted_transaction_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id,repair_completion_id),UNIQUE(run_id,repair_id),FOREIGN KEY(run_id,repair_id) REFERENCES repair_cycles(run_id,repair_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,contamination_revision) REFERENCES run_integrity_records(run_id,integrity_revision) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,completion_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE repair_completion_supersessions (run_id TEXT NOT NULL,repair_completion_id TEXT NOT NULL,position INTEGER NOT NULL,supersession_id TEXT NOT NULL,PRIMARY KEY(run_id,repair_completion_id,position),UNIQUE(run_id,supersession_id),FOREIGN KEY(run_id,repair_completion_id) REFERENCES repair_completions(run_id,repair_completion_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,supersession_id) REFERENCES artifact_supersessions(run_id,supersession_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE repair_completion_transitions (run_id TEXT NOT NULL,repair_completion_id TEXT NOT NULL,position INTEGER NOT NULL,transition_id TEXT NOT NULL,PRIMARY KEY(run_id,repair_completion_id,position),UNIQUE(run_id,transition_id),FOREIGN KEY(run_id,repair_completion_id) REFERENCES repair_completions(run_id,repair_completion_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,transition_id) REFERENCES stage_transitions(run_id,transition_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE recovery_completions (
  run_id TEXT NOT NULL,recovery_id TEXT NOT NULL,repair_completion_id TEXT NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.recovery_completion_record.v2'),
  contamination_revision INTEGER NOT NULL,disposition TEXT NOT NULL CHECK(disposition='recovered_non_reference'),completed_at TEXT NOT NULL,completion_event_id TEXT NOT NULL,
  accepted_transaction_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(run_id,recovery_id),UNIQUE(run_id,contamination_revision),
  FOREIGN KEY(run_id,repair_completion_id) REFERENCES repair_completions(run_id,repair_completion_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,contamination_revision) REFERENCES run_integrity_records(run_id,integrity_revision) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,completion_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE recovery_supersessions (run_id TEXT NOT NULL,recovery_id TEXT NOT NULL,position INTEGER NOT NULL,supersession_id TEXT NOT NULL,PRIMARY KEY(run_id,recovery_id,position),FOREIGN KEY(run_id,recovery_id) REFERENCES recovery_completions(run_id,recovery_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,supersession_id) REFERENCES artifact_supersessions(run_id,supersession_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE recovery_stage_transitions (run_id TEXT NOT NULL,recovery_id TEXT NOT NULL,position INTEGER NOT NULL,transition_id TEXT NOT NULL,PRIMARY KEY(run_id,recovery_id,position),FOREIGN KEY(run_id,recovery_id) REFERENCES recovery_completions(run_id,recovery_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,transition_id) REFERENCES stage_transitions(run_id,transition_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE recovery_gate_evaluations (run_id TEXT NOT NULL,recovery_id TEXT NOT NULL,position INTEGER NOT NULL,evaluation_id TEXT NOT NULL,PRIMARY KEY(run_id,recovery_id,position),FOREIGN KEY(run_id,recovery_id) REFERENCES recovery_completions(run_id,recovery_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,evaluation_id) REFERENCES gate_evaluations(run_id,evaluation_id) DEFERRABLE INITIALLY DEFERRED);

CREATE TABLE run_head_transitions (
  workspace_id TEXT NOT NULL,head_transition_id TEXT NOT NULL,successor_run_id TEXT NOT NULL,predecessor_run_id TEXT NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.run_head_transition_record.v2'),
  prior_workspace_revision INTEGER NOT NULL,successor_workspace_revision INTEGER NOT NULL,reason_code TEXT NOT NULL CHECK(reason_code='run_reset'),successor_disposition TEXT NOT NULL CHECK(successor_disposition='non_reference'),
  created_at TEXT NOT NULL,transition_event_id TEXT NOT NULL,accepted_transaction_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL,
  PRIMARY KEY(workspace_id,head_transition_id),UNIQUE(workspace_id,successor_run_id),CHECK(predecessor_run_id<>successor_run_id),CHECK(successor_workspace_revision=prior_workspace_revision+1),
  FOREIGN KEY(workspace_id,successor_run_id) REFERENCES runs(workspace_id,run_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(workspace_id,predecessor_run_id) REFERENCES runs(workspace_id,run_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(successor_run_id,transition_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(successor_run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);

CREATE TABLE finalize_renders (
  run_id TEXT NOT NULL,render_id TEXT NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.finalize_render_record.v2'),audit_proposal_id TEXT NOT NULL,
  audited_brief_artifact_id TEXT NOT NULL,audited_brief_revision INTEGER NOT NULL,audit_report_artifact_id TEXT NOT NULL,audit_report_revision INTEGER NOT NULL,
  reader_clean_status TEXT NOT NULL CHECK(reader_clean_status='pass'),policy_result_fingerprint TEXT NOT NULL,run_contract_fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,
  render_event_id TEXT NOT NULL,accepted_transaction_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(run_id,render_id),
  FOREIGN KEY(run_id,audit_proposal_id) REFERENCES accepted_proposals(run_id,proposal_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,audited_brief_artifact_id,audited_brief_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,audit_report_artifact_id,audit_report_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,render_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE finalize_render_artifacts (run_id TEXT NOT NULL,render_id TEXT NOT NULL,position INTEGER NOT NULL,artifact_id TEXT NOT NULL,artifact_revision INTEGER NOT NULL,artifact_sha256 TEXT NOT NULL,PRIMARY KEY(run_id,render_id,position),UNIQUE(run_id,render_id,artifact_id,artifact_revision),FOREIGN KEY(run_id,render_id) REFERENCES finalize_renders(run_id,render_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,artifact_id,artifact_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE finalizations (
  run_id TEXT NOT NULL,finalization_id TEXT NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.finalization_record.v2'),render_id TEXT NOT NULL,
  finalize_transition_id TEXT NOT NULL,finalize_gate_batch_id TEXT NOT NULL,recovery_id TEXT,integrity_revision INTEGER NOT NULL,finalized_at TEXT NOT NULL,
  finalization_event_id TEXT NOT NULL,accepted_transaction_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id,finalization_id),UNIQUE(run_id),FOREIGN KEY(run_id,render_id) REFERENCES finalize_renders(run_id,render_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,finalize_transition_id) REFERENCES stage_transitions(run_id,transition_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,recovery_id) REFERENCES recovery_completions(run_id,recovery_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,integrity_revision) REFERENCES run_integrity_records(run_id,integrity_revision) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,finalization_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE finalization_gate_evaluations (run_id TEXT NOT NULL,finalization_id TEXT NOT NULL,position INTEGER NOT NULL,evaluation_id TEXT NOT NULL,PRIMARY KEY(run_id,finalization_id,position),UNIQUE(run_id,finalization_id,evaluation_id),FOREIGN KEY(run_id,finalization_id) REFERENCES finalizations(run_id,finalization_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,evaluation_id) REFERENCES gate_evaluations(run_id,evaluation_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE run_archives (
  run_id TEXT NOT NULL,archive_id TEXT NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.run_archive_record.v2'),finalization_id TEXT NOT NULL,
  archive_artifact_id TEXT NOT NULL,archive_artifact_revision INTEGER NOT NULL,manifest_sha256 TEXT NOT NULL,included_count INTEGER NOT NULL CHECK(included_count>0),
  created_at TEXT NOT NULL,archive_event_id TEXT NOT NULL,accepted_transaction_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id,archive_id),UNIQUE(run_id,finalization_id),FOREIGN KEY(run_id,finalization_id) REFERENCES finalizations(run_id,finalization_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,archive_artifact_id,archive_artifact_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,archive_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE run_archive_artifact_bindings (run_id TEXT NOT NULL,archive_id TEXT NOT NULL,position INTEGER NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.run_archive_artifact_binding.v2'),artifact_id TEXT NOT NULL,artifact_revision INTEGER NOT NULL,artifact_sha256 TEXT NOT NULL,usage TEXT NOT NULL CHECK(usage IN ('control','evidence','workflow','reader','gate')),accepted_transaction_id TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(run_id,archive_id,position),UNIQUE(run_id,archive_id,artifact_id,artifact_revision),FOREIGN KEY(run_id,archive_id) REFERENCES run_archives(run_id,archive_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,artifact_id,artifact_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE package_ready_records (
  run_id TEXT NOT NULL,package_id TEXT NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.package_ready_record.v2'),finalization_id TEXT NOT NULL,archive_id TEXT NOT NULL,
  package_manifest_artifact_id TEXT NOT NULL,package_manifest_revision INTEGER NOT NULL,package_manifest_sha256 TEXT NOT NULL,artifact_count INTEGER NOT NULL CHECK(artifact_count>0),
  created_at TEXT NOT NULL,package_event_id TEXT NOT NULL,accepted_transaction_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id,package_id),UNIQUE(run_id,finalization_id),UNIQUE(run_id,archive_id),FOREIGN KEY(run_id,finalization_id) REFERENCES finalizations(run_id,finalization_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,archive_id) REFERENCES run_archives(run_id,archive_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,package_manifest_artifact_id,package_manifest_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,package_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE package_artifact_bindings (run_id TEXT NOT NULL,package_id TEXT NOT NULL,position INTEGER NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.package_artifact_binding.v2'),artifact_id TEXT NOT NULL,artifact_revision INTEGER NOT NULL,artifact_sha256 TEXT NOT NULL,usage TEXT NOT NULL CHECK(usage IN ('reader','archive','manifest')),accepted_transaction_id TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(run_id,package_id,position),UNIQUE(run_id,package_id,artifact_id,artifact_revision),FOREIGN KEY(run_id,package_id) REFERENCES package_ready_records(run_id,package_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,artifact_id,artifact_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);

CREATE TABLE approval_package_bindings (run_id TEXT NOT NULL,approval_id TEXT NOT NULL,package_id TEXT NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.approval_package_binding.v2'),accepted_transaction_id TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(run_id,approval_id,package_id),UNIQUE(run_id,approval_id),FOREIGN KEY(run_id,approval_id) REFERENCES approvals(run_id,approval_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,package_id) REFERENCES package_ready_records(run_id,package_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE delivery_authorizations (run_id TEXT NOT NULL,authorization_id TEXT NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.delivery_authorization_record.v2'),package_id TEXT NOT NULL,prior_authorization_id TEXT,approval_mode TEXT NOT NULL CHECK(approval_mode IN ('internal_draft','internal_management_review','research_review','ir_draft','formal_release_candidate')),retry_of_attempt_id TEXT,purpose TEXT NOT NULL CHECK(purpose IN ('initial_attempt','retry_attempt','result_reconciliation')),decision TEXT NOT NULL CHECK(decision IN ('authorize','deny')),target TEXT NOT NULL CHECK(target IN ('local','feishu','gmail')),channel TEXT NOT NULL,recipient_fingerprint TEXT NOT NULL,actor_id TEXT NOT NULL,recorded_at TEXT NOT NULL,authorization_event_id TEXT NOT NULL,accepted_transaction_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(run_id,authorization_id),UNIQUE(run_id,prior_authorization_id),FOREIGN KEY(run_id,package_id) REFERENCES package_ready_records(run_id,package_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,prior_authorization_id) REFERENCES delivery_authorizations(run_id,authorization_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,retry_of_attempt_id) REFERENCES delivery_attempts(run_id,attempt_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,authorization_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE delivery_attempts (run_id TEXT NOT NULL,attempt_id TEXT NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.delivery_attempt_record.v2'),package_id TEXT NOT NULL,authorization_id TEXT NOT NULL,target TEXT NOT NULL,channel TEXT NOT NULL,recipient_fingerprint TEXT NOT NULL,connector_operation_id TEXT NOT NULL,connector_request_fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,attempt_event_id TEXT NOT NULL,accepted_transaction_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(run_id,attempt_id),UNIQUE(run_id,authorization_id),UNIQUE(run_id,connector_operation_id),FOREIGN KEY(run_id,package_id) REFERENCES package_ready_records(run_id,package_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,authorization_id) REFERENCES delivery_authorizations(run_id,authorization_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,attempt_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE delivery_results (run_id TEXT NOT NULL,result_id TEXT NOT NULL,schema_version TEXT NOT NULL CHECK(schema_version='briefloop.delivery_result_record.v2'),attempt_id TEXT NOT NULL,prior_result_id TEXT,reconciliation_authorization_id TEXT,status TEXT NOT NULL CHECK(status IN ('bundle_prepared','draft_created','succeeded','failed','outcome_unknown')),adapter_id TEXT NOT NULL,adapter_version TEXT NOT NULL,connector_operation_id TEXT NOT NULL,evidence_sha256 TEXT NOT NULL,evidence_artifact_id TEXT,evidence_artifact_revision INTEGER,recorded_at TEXT NOT NULL,result_event_id TEXT NOT NULL,accepted_transaction_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(run_id,result_id),UNIQUE(run_id,prior_result_id),UNIQUE(run_id,reconciliation_authorization_id),FOREIGN KEY(run_id,attempt_id) REFERENCES delivery_attempts(run_id,attempt_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,prior_result_id) REFERENCES delivery_results(run_id,result_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,reconciliation_authorization_id) REFERENCES delivery_authorizations(run_id,authorization_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,evidence_artifact_id,evidence_artifact_revision) REFERENCES artifact_revisions(run_id,artifact_id,revision) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,result_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);

CREATE TABLE transaction_repair_cycles (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,repair_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,repair_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,repair_id) REFERENCES repair_cycles(run_id,repair_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_artifact_supersessions (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,supersession_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,supersession_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,supersession_id) REFERENCES artifact_supersessions(run_id,supersession_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_repair_completions (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,repair_completion_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,repair_completion_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,repair_completion_id) REFERENCES repair_completions(run_id,repair_completion_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_recovery_completions (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,recovery_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,recovery_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,recovery_id) REFERENCES recovery_completions(run_id,recovery_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_run_head_transitions (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,head_transition_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,head_transition_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_finalize_renders (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,render_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,render_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,render_id) REFERENCES finalize_renders(run_id,render_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_finalizations (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,finalization_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,finalization_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,finalization_id) REFERENCES finalizations(run_id,finalization_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_run_archives (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,archive_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,archive_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,archive_id) REFERENCES run_archives(run_id,archive_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_run_archive_artifact_bindings (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,archive_id TEXT NOT NULL,binding_position INTEGER NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,archive_id,binding_position),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,archive_id,binding_position) REFERENCES run_archive_artifact_bindings(run_id,archive_id,position) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_package_ready_records (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,package_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,package_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,package_id) REFERENCES package_ready_records(run_id,package_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_package_artifact_bindings (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,package_id TEXT NOT NULL,binding_position INTEGER NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,package_id,binding_position),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,package_id,binding_position) REFERENCES package_artifact_bindings(run_id,package_id,position) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_approvals (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,approval_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,approval_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,approval_id) REFERENCES approvals(run_id,approval_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_approval_package_bindings (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,approval_id TEXT NOT NULL,package_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,approval_id,package_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,approval_id,package_id) REFERENCES approval_package_bindings(run_id,approval_id,package_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_delivery_authorizations (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,authorization_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,authorization_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,authorization_id) REFERENCES delivery_authorizations(run_id,authorization_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_delivery_attempts (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,attempt_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,attempt_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,attempt_id) REFERENCES delivery_attempts(run_id,attempt_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_delivery_results (run_id TEXT NOT NULL,transaction_id TEXT NOT NULL,position INTEGER NOT NULL,result_id TEXT NOT NULL,PRIMARY KEY(run_id,transaction_id,position),UNIQUE(run_id,result_id),FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(run_id,result_id) REFERENCES delivery_results(run_id,result_id) DEFERRABLE INITIALLY DEFERRED);

-- Every PR-4B record/relation is immutable.  Generate the trigger inventory explicitly.
CREATE TRIGGER repair_cycles_no_update BEFORE UPDATE ON repair_cycles BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER repair_cycles_no_delete BEFORE DELETE ON repair_cycles BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER artifact_supersessions_no_update BEFORE UPDATE ON artifact_supersessions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER artifact_supersessions_no_delete BEFORE DELETE ON artifact_supersessions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER repair_completions_no_update BEFORE UPDATE ON repair_completions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER repair_completions_no_delete BEFORE DELETE ON repair_completions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER recovery_completions_no_update BEFORE UPDATE ON recovery_completions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER recovery_completions_no_delete BEFORE DELETE ON recovery_completions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_head_transitions_no_update BEFORE UPDATE ON run_head_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_head_transitions_no_delete BEFORE DELETE ON run_head_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER finalize_renders_no_update BEFORE UPDATE ON finalize_renders BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER finalize_renders_no_delete BEFORE DELETE ON finalize_renders BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER finalizations_no_update BEFORE UPDATE ON finalizations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER finalizations_no_delete BEFORE DELETE ON finalizations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_archives_no_update BEFORE UPDATE ON run_archives BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_archives_no_delete BEFORE DELETE ON run_archives BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_archive_artifact_bindings_no_update BEFORE UPDATE ON run_archive_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_archive_artifact_bindings_no_delete BEFORE DELETE ON run_archive_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER package_ready_records_no_update BEFORE UPDATE ON package_ready_records BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER package_ready_records_no_delete BEFORE DELETE ON package_ready_records BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER package_artifact_bindings_no_update BEFORE UPDATE ON package_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER package_artifact_bindings_no_delete BEFORE DELETE ON package_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER approval_package_bindings_no_update BEFORE UPDATE ON approval_package_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER approval_package_bindings_no_delete BEFORE DELETE ON approval_package_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER delivery_authorizations_no_update BEFORE UPDATE ON delivery_authorizations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER delivery_authorizations_no_delete BEFORE DELETE ON delivery_authorizations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER delivery_attempts_no_update BEFORE UPDATE ON delivery_attempts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER delivery_attempts_no_delete BEFORE DELETE ON delivery_attempts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER delivery_results_no_update BEFORE UPDATE ON delivery_results BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER delivery_results_no_delete BEFORE DELETE ON delivery_results BEGIN SELECT RAISE(ABORT,'append_only'); END;

CREATE TRIGGER repair_completion_supersessions_no_update BEFORE UPDATE ON repair_completion_supersessions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER repair_completion_supersessions_no_delete BEFORE DELETE ON repair_completion_supersessions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER repair_completion_transitions_no_update BEFORE UPDATE ON repair_completion_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER repair_completion_transitions_no_delete BEFORE DELETE ON repair_completion_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER recovery_supersessions_no_update BEFORE UPDATE ON recovery_supersessions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER recovery_supersessions_no_delete BEFORE DELETE ON recovery_supersessions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER recovery_stage_transitions_no_update BEFORE UPDATE ON recovery_stage_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER recovery_stage_transitions_no_delete BEFORE DELETE ON recovery_stage_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER recovery_gate_evaluations_no_update BEFORE UPDATE ON recovery_gate_evaluations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER recovery_gate_evaluations_no_delete BEFORE DELETE ON recovery_gate_evaluations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER finalize_render_artifacts_no_update BEFORE UPDATE ON finalize_render_artifacts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER finalize_render_artifacts_no_delete BEFORE DELETE ON finalize_render_artifacts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER finalization_gate_evaluations_no_update BEFORE UPDATE ON finalization_gate_evaluations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER finalization_gate_evaluations_no_delete BEFORE DELETE ON finalization_gate_evaluations BEGIN SELECT RAISE(ABORT,'append_only'); END;

CREATE TRIGGER transaction_repair_cycles_no_update BEFORE UPDATE ON transaction_repair_cycles BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_repair_cycles_no_delete BEFORE DELETE ON transaction_repair_cycles BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_artifact_supersessions_no_update BEFORE UPDATE ON transaction_artifact_supersessions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_artifact_supersessions_no_delete BEFORE DELETE ON transaction_artifact_supersessions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_repair_completions_no_update BEFORE UPDATE ON transaction_repair_completions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_repair_completions_no_delete BEFORE DELETE ON transaction_repair_completions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_recovery_completions_no_update BEFORE UPDATE ON transaction_recovery_completions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_recovery_completions_no_delete BEFORE DELETE ON transaction_recovery_completions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_head_transitions_no_update BEFORE UPDATE ON transaction_run_head_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_head_transitions_no_delete BEFORE DELETE ON transaction_run_head_transitions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_finalize_renders_no_update BEFORE UPDATE ON transaction_finalize_renders BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_finalize_renders_no_delete BEFORE DELETE ON transaction_finalize_renders BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_finalizations_no_update BEFORE UPDATE ON transaction_finalizations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_finalizations_no_delete BEFORE DELETE ON transaction_finalizations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_archives_no_update BEFORE UPDATE ON transaction_run_archives BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_archives_no_delete BEFORE DELETE ON transaction_run_archives BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_archive_artifact_bindings_no_update BEFORE UPDATE ON transaction_run_archive_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_archive_artifact_bindings_no_delete BEFORE DELETE ON transaction_run_archive_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_package_ready_records_no_update BEFORE UPDATE ON transaction_package_ready_records BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_package_ready_records_no_delete BEFORE DELETE ON transaction_package_ready_records BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_package_artifact_bindings_no_update BEFORE UPDATE ON transaction_package_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_package_artifact_bindings_no_delete BEFORE DELETE ON transaction_package_artifact_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_approvals_no_update BEFORE UPDATE ON transaction_approvals BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_approvals_no_delete BEFORE DELETE ON transaction_approvals BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_approval_package_bindings_no_update BEFORE UPDATE ON transaction_approval_package_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_approval_package_bindings_no_delete BEFORE DELETE ON transaction_approval_package_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_delivery_authorizations_no_update BEFORE UPDATE ON transaction_delivery_authorizations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_delivery_authorizations_no_delete BEFORE DELETE ON transaction_delivery_authorizations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_delivery_attempts_no_update BEFORE UPDATE ON transaction_delivery_attempts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_delivery_attempts_no_delete BEFORE DELETE ON transaction_delivery_attempts BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_delivery_results_no_update BEFORE UPDATE ON transaction_delivery_results BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_delivery_results_no_delete BEFORE DELETE ON transaction_delivery_results BEGIN SELECT RAISE(ABORT,'append_only'); END;

PRAGMA user_version=4;
COMMIT;
