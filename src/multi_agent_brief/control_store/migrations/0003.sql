BEGIN IMMEDIATE;

INSERT INTO schema_migrations(version, name) VALUES (3, '0003');

CREATE TABLE run_contract_bindings (
    run_id TEXT PRIMARY KEY CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    workspace_id TEXT NOT NULL CHECK(typeof(workspace_id) = 'text' AND length(workspace_id) > 0),
    schema_version TEXT NOT NULL CHECK(schema_version = 'briefloop.run_contract_binding.v2'),
    runtime TEXT NOT NULL CHECK(typeof(runtime) = 'text' AND length(runtime) > 0),
    stage_specs_artifact_id TEXT NOT NULL,
    stage_specs_revision INTEGER NOT NULL CHECK(typeof(stage_specs_revision) = 'integer' AND stage_specs_revision > 0),
    stage_specs_sha256 TEXT NOT NULL CHECK(length(stage_specs_sha256) = 64 AND stage_specs_sha256 NOT GLOB '*[^0-9a-f]*'),
    artifact_contracts_artifact_id TEXT NOT NULL,
    artifact_contracts_revision INTEGER NOT NULL CHECK(typeof(artifact_contracts_revision) = 'integer' AND artifact_contracts_revision > 0),
    artifact_contracts_sha256 TEXT NOT NULL CHECK(length(artifact_contracts_sha256) = 64 AND artifact_contracts_sha256 NOT GLOB '*[^0-9a-f]*'),
    policy_pack_artifact_id TEXT NOT NULL,
    policy_pack_revision INTEGER NOT NULL CHECK(typeof(policy_pack_revision) = 'integer' AND policy_pack_revision > 0),
    policy_pack_sha256 TEXT NOT NULL CHECK(length(policy_pack_sha256) = 64 AND policy_pack_sha256 NOT GLOB '*[^0-9a-f]*'),
    contract_fingerprint TEXT NOT NULL CHECK(length(contract_fingerprint) = 64 AND contract_fingerprint NOT GLOB '*[^0-9a-f]*'),
    initialization_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint) = 64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    FOREIGN KEY(workspace_id, run_id) REFERENCES runs(workspace_id, run_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, stage_specs_artifact_id, stage_specs_revision) REFERENCES artifact_revisions(run_id, artifact_id, revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, artifact_contracts_artifact_id, artifact_contracts_revision) REFERENCES artifact_revisions(run_id, artifact_id, revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, policy_pack_artifact_id, policy_pack_revision) REFERENCES artifact_revisions(run_id, artifact_id, revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, initialization_event_id) REFERENCES events(run_id, event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE owned_artifact_submissions (
    run_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version = 'briefloop.owned_artifact_submission_record.v2'),
    artifact_id TEXT NOT NULL,
    artifact_revision INTEGER NOT NULL CHECK(typeof(artifact_revision) = 'integer' AND artifact_revision > 0),
    artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256) = 64 AND artifact_sha256 NOT GLOB '*[^0-9a-f]*'),
    owner_stage_id TEXT NOT NULL,
    owner_role_id TEXT NOT NULL,
    run_contract_fingerprint TEXT NOT NULL CHECK(length(run_contract_fingerprint) = 64 AND run_contract_fingerprint NOT GLOB '*[^0-9a-f]*'),
    invocation_id TEXT,
    producer_tool_id TEXT,
    parent_artifact_id TEXT,
    parent_artifact_revision INTEGER,
    source_proposal_id TEXT,
    canonical_workspace_path TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint) = 64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    accepted_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, submission_id),
    UNIQUE(run_id, artifact_id, artifact_revision),
    CHECK(invocation_id IS NOT NULL OR producer_tool_id IS NOT NULL),
    CHECK((parent_artifact_id IS NULL) = (parent_artifact_revision IS NULL)),
    FOREIGN KEY(run_id, artifact_id, artifact_revision) REFERENCES artifact_revisions(run_id, artifact_id, revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, invocation_id) REFERENCES agent_invocations(run_id, invocation_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, parent_artifact_id, parent_artifact_revision) REFERENCES artifact_revisions(run_id, artifact_id, revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, source_proposal_id) REFERENCES accepted_proposals(run_id, proposal_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_event_id) REFERENCES events(run_id, event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE stage_transitions (
    run_id TEXT NOT NULL,
    transition_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version = 'briefloop.stage_transition_record.v2'),
    stage_id TEXT NOT NULL,
    transition_kind TEXT NOT NULL CHECK(transition_kind IN ('initialize','activate','complete','satisfied_by_topology')),
    prior_status TEXT,
    prior_revision INTEGER,
    result_status TEXT NOT NULL,
    result_revision INTEGER NOT NULL CHECK(typeof(result_revision) = 'integer' AND result_revision >= 0),
    run_contract_fingerprint TEXT NOT NULL CHECK(length(run_contract_fingerprint) = 64 AND run_contract_fingerprint NOT GLOB '*[^0-9a-f]*'),
    transition_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint) = 64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, transition_id),
    UNIQUE(run_id, stage_id, result_revision),
    CHECK((transition_kind = 'initialize' AND prior_status IS NULL AND prior_revision IS NULL AND result_revision = 0) OR (transition_kind != 'initialize' AND prior_status IS NOT NULL AND prior_revision IS NOT NULL AND result_revision = prior_revision + 1)),
    FOREIGN KEY(run_id, transition_event_id) REFERENCES events(run_id, event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE stage_artifact_bindings (
    run_id TEXT NOT NULL,
    transition_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(typeof(position) = 'integer' AND position >= 0),
    schema_version TEXT NOT NULL CHECK(schema_version = 'briefloop.stage_artifact_binding.v2'),
    artifact_id TEXT NOT NULL,
    artifact_revision INTEGER NOT NULL CHECK(typeof(artifact_revision) = 'integer' AND artifact_revision > 0),
    artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256) = 64 AND artifact_sha256 NOT GLOB '*[^0-9a-f]*'),
    usage TEXT NOT NULL CHECK(usage IN ('produced','consumed','topology_required')),
    accepted_transaction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, transition_id, position),
    UNIQUE(run_id, transition_id, artifact_id, artifact_revision, usage),
    FOREIGN KEY(run_id, transition_id) REFERENCES stage_transitions(run_id, transition_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, artifact_id, artifact_revision) REFERENCES artifact_revisions(run_id, artifact_id, revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE claims (
    run_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version = 'briefloop.claim_record.v2'),
    freeze_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(typeof(ordinal) = 'integer' AND ordinal > 0),
    claim_drafts_proposal_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    primary_source_id TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, claim_id),
    UNIQUE(run_id, freeze_id, ordinal),
    FOREIGN KEY(run_id, claim_drafts_proposal_id) REFERENCES accepted_proposals(run_id, proposal_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, primary_source_id) REFERENCES sources(run_id, source_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE claim_source_bindings (
    run_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version = 'briefloop.claim_source_binding.v2'),
    position INTEGER NOT NULL CHECK(typeof(position) = 'integer' AND position >= 0),
    citation_role TEXT NOT NULL CHECK(citation_role IN ('primary','additional')),
    claim_drafts_proposal_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, claim_id, source_id),
    UNIQUE(run_id, claim_id, position),
    CHECK((position = 0 AND citation_role = 'primary') OR (position > 0 AND citation_role = 'additional')),
    FOREIGN KEY(run_id, claim_id) REFERENCES claims(run_id, claim_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, source_id) REFERENCES sources(run_id, source_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, claim_drafts_proposal_id) REFERENCES accepted_proposals(run_id, proposal_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE claim_freezes (
    run_id TEXT NOT NULL,
    freeze_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version = 'briefloop.claim_freeze_record.v2'),
    claim_drafts_proposal_id TEXT NOT NULL,
    screened_proposal_id TEXT NOT NULL,
    candidate_proposal_id TEXT NOT NULL,
    claim_drafts_artifact_id TEXT NOT NULL,
    claim_drafts_artifact_revision INTEGER NOT NULL,
    claim_drafts_sha256 TEXT NOT NULL,
    ledger_artifact_id TEXT NOT NULL,
    ledger_artifact_revision INTEGER NOT NULL,
    ledger_sha256 TEXT NOT NULL,
    run_contract_fingerprint TEXT NOT NULL,
    claim_count INTEGER NOT NULL CHECK(typeof(claim_count) = 'integer' AND claim_count > 0),
    freeze_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, freeze_id),
    UNIQUE(run_id),
    FOREIGN KEY(run_id, claim_drafts_proposal_id) REFERENCES accepted_proposals(run_id, proposal_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, screened_proposal_id) REFERENCES accepted_proposals(run_id, proposal_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, candidate_proposal_id) REFERENCES accepted_proposals(run_id, proposal_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, claim_drafts_artifact_id, claim_drafts_artifact_revision) REFERENCES artifact_revisions(run_id, artifact_id, revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, ledger_artifact_id, ledger_artifact_revision) REFERENCES artifact_revisions(run_id, artifact_id, revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, freeze_event_id) REFERENCES events(run_id, event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE gate_evaluations (
    run_id TEXT NOT NULL,
    evaluation_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version = 'briefloop.gate_evaluation_record.v2'),
    gate_batch_id TEXT NOT NULL,
    stage_id TEXT NOT NULL CHECK(stage_id = 'auditor'),
    gate_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    run_contract_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pass','warning','fail','unavailable','invalid')),
    blocking INTEGER NOT NULL CHECK(blocking IN (0,1)),
    report_artifact_id TEXT NOT NULL,
    report_artifact_revision INTEGER NOT NULL,
    evaluation_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, evaluation_id),
    UNIQUE(run_id, gate_batch_id, gate_id),
    FOREIGN KEY(run_id, report_artifact_id, report_artifact_revision) REFERENCES artifact_revisions(run_id, artifact_id, revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, evaluation_event_id) REFERENCES events(run_id, event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE gate_findings (
    run_id TEXT NOT NULL,
    evaluation_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version = 'briefloop.gate_finding_record.v2'),
    gate_id TEXT NOT NULL,
    blocking_level TEXT NOT NULL CHECK(blocking_level IN ('none','warning','blocking')),
    artifact_id TEXT,
    claim_id TEXT,
    source_id TEXT,
    accepted_transaction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, evaluation_id, finding_id),
    FOREIGN KEY(run_id, evaluation_id) REFERENCES gate_evaluations(run_id, evaluation_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, artifact_id) REFERENCES artifacts(run_id, artifact_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, claim_id) REFERENCES claims(run_id, claim_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, source_id) REFERENCES sources(run_id, source_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE gate_artifact_bindings (
    run_id TEXT NOT NULL,
    evaluation_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(typeof(position) = 'integer' AND position >= 0),
    schema_version TEXT NOT NULL CHECK(schema_version = 'briefloop.gate_artifact_binding.v2'),
    artifact_id TEXT NOT NULL,
    artifact_revision INTEGER NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    usage TEXT NOT NULL CHECK(usage IN ('brief','ledger','analyst_snapshot','screened_candidates')),
    accepted_transaction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, evaluation_id, position),
    UNIQUE(run_id, evaluation_id, artifact_id, artifact_revision),
    FOREIGN KEY(run_id, evaluation_id) REFERENCES gate_evaluations(run_id, evaluation_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, artifact_id, artifact_revision) REFERENCES artifact_revisions(run_id, artifact_id, revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE stage_gate_bindings (
    run_id TEXT NOT NULL,
    transition_id TEXT NOT NULL,
    gate_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version = 'briefloop.stage_gate_binding.v2'),
    evaluation_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, transition_id, gate_id),
    FOREIGN KEY(run_id, transition_id) REFERENCES stage_transitions(run_id, transition_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, evaluation_id) REFERENCES gate_evaluations(run_id, evaluation_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE run_integrity_records (
    run_id TEXT NOT NULL,
    integrity_revision INTEGER NOT NULL CHECK(typeof(integrity_revision) = 'integer' AND integrity_revision > 0),
    schema_version TEXT NOT NULL CHECK(schema_version = 'briefloop.run_integrity_record.v2'),
    status TEXT NOT NULL CHECK(status IN ('clean','contaminated')),
    prior_integrity_revision INTEGER,
    affected_artifact_id TEXT,
    affected_artifact_revision INTEGER,
    expected_workspace_path TEXT,
    expected_sha256 TEXT,
    observed_entry_kind TEXT,
    observed_sha256 TEXT,
    reason_code TEXT,
    first_detected_event_id TEXT,
    accepted_transaction_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, integrity_revision),
    CHECK((status = 'clean' AND integrity_revision = 1 AND prior_integrity_revision IS NULL AND affected_artifact_id IS NULL) OR (status = 'contaminated' AND prior_integrity_revision IS NOT NULL AND integrity_revision = prior_integrity_revision + 1 AND affected_artifact_id IS NOT NULL)),
    FOREIGN KEY(run_id, affected_artifact_id, affected_artifact_revision) REFERENCES artifact_revisions(run_id, artifact_id, revision) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, first_detected_event_id) REFERENCES events(run_id, event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_run_contract_bindings (run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL, binding_run_id TEXT NOT NULL, PRIMARY KEY(run_id, transaction_id, position), UNIQUE(binding_run_id), FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED, FOREIGN KEY(binding_run_id) REFERENCES run_contract_bindings(run_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_owned_artifact_submissions (run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL, submission_id TEXT NOT NULL, PRIMARY KEY(run_id, transaction_id, position), UNIQUE(run_id, submission_id), FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED, FOREIGN KEY(run_id, submission_id) REFERENCES owned_artifact_submissions(run_id, submission_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_stage_transitions (run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL, transition_id TEXT NOT NULL, PRIMARY KEY(run_id, transaction_id, position), UNIQUE(run_id, transition_id), FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED, FOREIGN KEY(run_id, transition_id) REFERENCES stage_transitions(run_id, transition_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_stage_artifact_bindings (run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL, transition_id TEXT NOT NULL, binding_position INTEGER NOT NULL, PRIMARY KEY(run_id, transaction_id, position), UNIQUE(run_id, transition_id, binding_position), FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED, FOREIGN KEY(run_id, transition_id, binding_position) REFERENCES stage_artifact_bindings(run_id, transition_id, position) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_stage_gate_bindings (run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL, transition_id TEXT NOT NULL, gate_id TEXT NOT NULL, PRIMARY KEY(run_id, transaction_id, position), UNIQUE(run_id, transition_id, gate_id), FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED, FOREIGN KEY(run_id, transition_id, gate_id) REFERENCES stage_gate_bindings(run_id, transition_id, gate_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_claims (run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL, claim_id TEXT NOT NULL, PRIMARY KEY(run_id, transaction_id, position), UNIQUE(run_id, claim_id), FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED, FOREIGN KEY(run_id, claim_id) REFERENCES claims(run_id, claim_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_claim_source_bindings (run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL, claim_id TEXT NOT NULL, source_id TEXT NOT NULL, PRIMARY KEY(run_id, transaction_id, position), UNIQUE(run_id, claim_id, source_id), FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED, FOREIGN KEY(run_id, claim_id, source_id) REFERENCES claim_source_bindings(run_id, claim_id, source_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_claim_freezes (run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL, freeze_id TEXT NOT NULL, PRIMARY KEY(run_id, transaction_id, position), UNIQUE(run_id, freeze_id), FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED, FOREIGN KEY(run_id, freeze_id) REFERENCES claim_freezes(run_id, freeze_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_gate_evaluations (run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL, evaluation_id TEXT NOT NULL, PRIMARY KEY(run_id, transaction_id, position), UNIQUE(run_id, evaluation_id), FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED, FOREIGN KEY(run_id, evaluation_id) REFERENCES gate_evaluations(run_id, evaluation_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_gate_findings (run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL, evaluation_id TEXT NOT NULL, finding_id TEXT NOT NULL, PRIMARY KEY(run_id, transaction_id, position), UNIQUE(run_id, evaluation_id, finding_id), FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED, FOREIGN KEY(run_id, evaluation_id, finding_id) REFERENCES gate_findings(run_id, evaluation_id, finding_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_gate_artifact_bindings (run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL, evaluation_id TEXT NOT NULL, binding_position INTEGER NOT NULL, PRIMARY KEY(run_id, transaction_id, position), UNIQUE(run_id, evaluation_id, binding_position), FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED, FOREIGN KEY(run_id, evaluation_id, binding_position) REFERENCES gate_artifact_bindings(run_id, evaluation_id, position) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE transaction_run_integrity_records (run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL, integrity_revision INTEGER NOT NULL, PRIMARY KEY(run_id, transaction_id, position), UNIQUE(run_id, integrity_revision), FOREIGN KEY(run_id, transaction_id) REFERENCES transactions(run_id, transaction_id) DEFERRABLE INITIALLY DEFERRED, FOREIGN KEY(run_id, integrity_revision) REFERENCES run_integrity_records(run_id, integrity_revision) DEFERRABLE INITIALLY DEFERRED);

CREATE TRIGGER run_contract_bindings_no_update BEFORE UPDATE ON run_contract_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER run_contract_bindings_no_delete BEFORE DELETE ON run_contract_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER owned_artifact_submissions_no_update BEFORE UPDATE ON owned_artifact_submissions BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER owned_artifact_submissions_no_delete BEFORE DELETE ON owned_artifact_submissions BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER stage_transitions_no_update BEFORE UPDATE ON stage_transitions BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER stage_transitions_no_delete BEFORE DELETE ON stage_transitions BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER stage_artifact_bindings_no_update BEFORE UPDATE ON stage_artifact_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER stage_artifact_bindings_no_delete BEFORE DELETE ON stage_artifact_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER stage_gate_bindings_no_update BEFORE UPDATE ON stage_gate_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER stage_gate_bindings_no_delete BEFORE DELETE ON stage_gate_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER claims_no_update BEFORE UPDATE ON claims BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER claims_no_delete BEFORE DELETE ON claims BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER claim_source_bindings_no_update BEFORE UPDATE ON claim_source_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER claim_source_bindings_no_delete BEFORE DELETE ON claim_source_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER claim_freezes_no_update BEFORE UPDATE ON claim_freezes BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER claim_freezes_no_delete BEFORE DELETE ON claim_freezes BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER gate_evaluations_no_update BEFORE UPDATE ON gate_evaluations BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER gate_evaluations_no_delete BEFORE DELETE ON gate_evaluations BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER gate_findings_no_update BEFORE UPDATE ON gate_findings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER gate_findings_no_delete BEFORE DELETE ON gate_findings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER gate_artifact_bindings_no_update BEFORE UPDATE ON gate_artifact_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER gate_artifact_bindings_no_delete BEFORE DELETE ON gate_artifact_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER run_integrity_records_no_update BEFORE UPDATE ON run_integrity_records BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER run_integrity_records_no_delete BEFORE DELETE ON run_integrity_records BEGIN SELECT RAISE(ABORT, 'append_only'); END;

CREATE TRIGGER transaction_run_contract_bindings_no_update BEFORE UPDATE ON transaction_run_contract_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_run_contract_bindings_no_delete BEFORE DELETE ON transaction_run_contract_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_owned_artifact_submissions_no_update BEFORE UPDATE ON transaction_owned_artifact_submissions BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_owned_artifact_submissions_no_delete BEFORE DELETE ON transaction_owned_artifact_submissions BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_stage_transitions_no_update BEFORE UPDATE ON transaction_stage_transitions BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_stage_transitions_no_delete BEFORE DELETE ON transaction_stage_transitions BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_stage_artifact_bindings_no_update BEFORE UPDATE ON transaction_stage_artifact_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_stage_artifact_bindings_no_delete BEFORE DELETE ON transaction_stage_artifact_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_stage_gate_bindings_no_update BEFORE UPDATE ON transaction_stage_gate_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_stage_gate_bindings_no_delete BEFORE DELETE ON transaction_stage_gate_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_claims_no_update BEFORE UPDATE ON transaction_claims BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_claims_no_delete BEFORE DELETE ON transaction_claims BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_claim_source_bindings_no_update BEFORE UPDATE ON transaction_claim_source_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_claim_source_bindings_no_delete BEFORE DELETE ON transaction_claim_source_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_claim_freezes_no_update BEFORE UPDATE ON transaction_claim_freezes BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_claim_freezes_no_delete BEFORE DELETE ON transaction_claim_freezes BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_gate_evaluations_no_update BEFORE UPDATE ON transaction_gate_evaluations BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_gate_evaluations_no_delete BEFORE DELETE ON transaction_gate_evaluations BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_gate_findings_no_update BEFORE UPDATE ON transaction_gate_findings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_gate_findings_no_delete BEFORE DELETE ON transaction_gate_findings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_gate_artifact_bindings_no_update BEFORE UPDATE ON transaction_gate_artifact_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_gate_artifact_bindings_no_delete BEFORE DELETE ON transaction_gate_artifact_bindings BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_run_integrity_records_no_update BEFORE UPDATE ON transaction_run_integrity_records BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_run_integrity_records_no_delete BEFORE DELETE ON transaction_run_integrity_records BEGIN SELECT RAISE(ABORT, 'append_only'); END;

PRAGMA user_version = 3;
COMMIT;
