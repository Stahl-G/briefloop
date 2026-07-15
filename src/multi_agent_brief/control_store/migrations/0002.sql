BEGIN IMMEDIATE;

INSERT INTO schema_migrations(version, name) VALUES (2, '0002');

ALTER TABLE agent_invocations ADD COLUMN failure_reason TEXT CHECK(
    failure_reason IS NULL
    OR (typeof(failure_reason) = 'text' AND length(failure_reason) > 0)
);

CREATE TABLE workspace_run_heads (
    workspace_id TEXT PRIMARY KEY
        CHECK(typeof(workspace_id) = 'text' AND length(workspace_id) > 0),
    schema_version TEXT NOT NULL
        CHECK(
            typeof(schema_version) = 'text'
            AND schema_version = 'briefloop.workspace_run_head.v2'
        ),
    current_run_id TEXT NOT NULL
        CHECK(typeof(current_run_id) = 'text' AND length(current_run_id) > 0),
    updated_at TEXT NOT NULL
        CHECK(typeof(updated_at) = 'text' AND length(updated_at) > 0),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    UNIQUE(workspace_id, current_run_id),
    FOREIGN KEY(workspace_id, current_run_id)
        REFERENCES runs(workspace_id, run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE sources (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    source_id TEXT NOT NULL
        CHECK(typeof(source_id) = 'text' AND length(source_id) > 0),
    schema_version TEXT NOT NULL
        CHECK(
            typeof(schema_version) = 'text'
            AND schema_version = 'briefloop.accepted_source_record.v2'
        ),
    origin_type TEXT NOT NULL
        CHECK(typeof(origin_type) = 'text' AND length(origin_type) > 0),
    acquisition_method TEXT NOT NULL
        CHECK(typeof(acquisition_method) = 'text' AND length(acquisition_method) > 0),
    material_kind TEXT NOT NULL
        CHECK(typeof(material_kind) = 'text' AND length(material_kind) > 0),
    provider TEXT CHECK(
        provider IS NULL
        OR (typeof(provider) = 'text' AND length(provider) > 0)
    ),
    locator_json TEXT NOT NULL CHECK(typeof(locator_json) = 'text'),
    title TEXT NOT NULL CHECK(typeof(title) = 'text' AND length(title) > 0),
    publisher TEXT CHECK(
        publisher IS NULL
        OR (typeof(publisher) = 'text' AND length(publisher) > 0)
    ),
    published_at TEXT CHECK(
        published_at IS NULL
        OR (typeof(published_at) = 'text' AND length(published_at) > 0)
    ),
    retrieved_at TEXT NOT NULL
        CHECK(typeof(retrieved_at) = 'text' AND length(retrieved_at) > 0),
    source_category TEXT NOT NULL
        CHECK(typeof(source_category) = 'text' AND length(source_category) > 0),
    retrieval_source_type TEXT NOT NULL
        CHECK(typeof(retrieval_source_type) = 'text' AND length(retrieval_source_type) > 0),
    underlying_evidence_type TEXT NOT NULL
        CHECK(typeof(underlying_evidence_type) = 'text' AND length(underlying_evidence_type) > 0),
    raw_underlying_evidence_type TEXT CHECK(
        raw_underlying_evidence_type IS NULL
        OR (
            typeof(raw_underlying_evidence_type) = 'text'
            AND length(raw_underlying_evidence_type) > 0
        )
    ),
    content_sha256 TEXT NOT NULL CHECK(
        typeof(content_sha256) = 'text'
        AND length(content_sha256) = 64
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    content_size_bytes INTEGER NOT NULL
        CHECK(typeof(content_size_bytes) = 'integer' AND content_size_bytes >= 0),
    content_media_type TEXT NOT NULL
        CHECK(typeof(content_media_type) = 'text' AND length(content_media_type) > 0),
    content_blob_path TEXT NOT NULL
        CHECK(typeof(content_blob_path) = 'text' AND length(content_blob_path) > 0),
    content_artifact_id TEXT NOT NULL
        CHECK(typeof(content_artifact_id) = 'text' AND length(content_artifact_id) > 0),
    content_artifact_revision INTEGER NOT NULL
        CHECK(
            typeof(content_artifact_revision) = 'integer'
            AND content_artifact_revision = 1
        ),
    raw_payload_sha256 TEXT CHECK(
        raw_payload_sha256 IS NULL
        OR (
            typeof(raw_payload_sha256) = 'text'
            AND length(raw_payload_sha256) = 64
            AND raw_payload_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    raw_payload_size_bytes INTEGER CHECK(
        raw_payload_size_bytes IS NULL
        OR (typeof(raw_payload_size_bytes) = 'integer' AND raw_payload_size_bytes >= 0)
    ),
    raw_payload_media_type TEXT CHECK(
        raw_payload_media_type IS NULL
        OR (typeof(raw_payload_media_type) = 'text' AND length(raw_payload_media_type) > 0)
    ),
    raw_payload_blob_path TEXT CHECK(
        raw_payload_blob_path IS NULL
        OR (typeof(raw_payload_blob_path) = 'text' AND length(raw_payload_blob_path) > 0)
    ),
    raw_payload_artifact_id TEXT CHECK(
        raw_payload_artifact_id IS NULL
        OR (typeof(raw_payload_artifact_id) = 'text' AND length(raw_payload_artifact_id) > 0)
    ),
    raw_payload_artifact_revision INTEGER CHECK(
        raw_payload_artifact_revision IS NULL
        OR (
            typeof(raw_payload_artifact_revision) = 'integer'
            AND raw_payload_artifact_revision = 1
        )
    ),
    claims_eligible INTEGER NOT NULL
        CHECK(typeof(claims_eligible) = 'integer' AND claims_eligible IN (0, 1)),
    eligibility_reason TEXT NOT NULL
        CHECK(typeof(eligibility_reason) = 'text' AND length(eligibility_reason) > 0),
    invocation_id TEXT NOT NULL
        CHECK(typeof(invocation_id) = 'text' AND length(invocation_id) > 0),
    acquisition_event_id TEXT NOT NULL
        CHECK(typeof(acquisition_event_id) = 'text' AND length(acquisition_event_id) > 0),
    accepted_transaction_id TEXT NOT NULL
        CHECK(typeof(accepted_transaction_id) = 'text' AND length(accepted_transaction_id) > 0),
    request_fingerprint TEXT NOT NULL CHECK(
        typeof(request_fingerprint) = 'text'
        AND length(request_fingerprint) = 64
        AND request_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL
        CHECK(typeof(created_at) = 'text' AND length(created_at) > 0),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, source_id),
    UNIQUE(run_id, invocation_id),
    UNIQUE(run_id, acquisition_event_id),
    UNIQUE(run_id, accepted_transaction_id),
    UNIQUE(run_id, content_artifact_id, content_artifact_revision),
    UNIQUE(run_id, raw_payload_artifact_id, raw_payload_artifact_revision),
    CHECK(
        (
            raw_payload_sha256 IS NULL
            AND raw_payload_size_bytes IS NULL
            AND raw_payload_media_type IS NULL
            AND raw_payload_blob_path IS NULL
            AND raw_payload_artifact_id IS NULL
            AND raw_payload_artifact_revision IS NULL
        )
        OR
        (
            raw_payload_sha256 IS NOT NULL
            AND raw_payload_size_bytes IS NOT NULL
            AND raw_payload_media_type IS NOT NULL
            AND raw_payload_blob_path IS NOT NULL
            AND raw_payload_artifact_id IS NOT NULL
            AND raw_payload_artifact_revision = 1
            AND raw_payload_artifact_id != content_artifact_id
        )
    ),
    FOREIGN KEY(run_id, invocation_id)
        REFERENCES agent_invocations(run_id, invocation_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, acquisition_event_id)
        REFERENCES events(run_id, event_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id)
        REFERENCES transactions(run_id, transaction_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id, acquisition_event_id)
        REFERENCES transaction_events(run_id, transaction_id, event_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, content_artifact_id, content_artifact_revision)
        REFERENCES artifact_revisions(run_id, artifact_id, revision)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(
        run_id,
        accepted_transaction_id,
        content_artifact_id,
        content_artifact_revision
    ) REFERENCES transaction_artifact_revisions(
        run_id,
        transaction_id,
        artifact_id,
        revision
    ) ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, raw_payload_artifact_id, raw_payload_artifact_revision)
        REFERENCES artifact_revisions(run_id, artifact_id, revision)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(
        run_id,
        accepted_transaction_id,
        raw_payload_artifact_id,
        raw_payload_artifact_revision
    ) REFERENCES transaction_artifact_revisions(
        run_id,
        transaction_id,
        artifact_id,
        revision
    ) ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE accepted_proposals (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    proposal_id TEXT NOT NULL
        CHECK(typeof(proposal_id) = 'text' AND length(proposal_id) > 0),
    schema_version TEXT NOT NULL
        CHECK(
            typeof(schema_version) = 'text'
            AND schema_version = 'briefloop.accepted_proposal_record.v2'
        ),
    proposal_kind TEXT NOT NULL
        CHECK(proposal_kind IN ('candidate', 'screened', 'claim_drafts', 'audit')),
    artifact_id TEXT NOT NULL
        CHECK(typeof(artifact_id) = 'text' AND length(artifact_id) > 0),
    artifact_revision INTEGER NOT NULL
        CHECK(typeof(artifact_revision) = 'integer' AND artifact_revision > 0),
    proposal_sha256 TEXT NOT NULL CHECK(
        typeof(proposal_sha256) = 'text'
        AND length(proposal_sha256) = 64
        AND proposal_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    invocation_id TEXT NOT NULL
        CHECK(typeof(invocation_id) = 'text' AND length(invocation_id) > 0),
    owner_stage_id TEXT NOT NULL
        CHECK(typeof(owner_stage_id) = 'text' AND length(owner_stage_id) > 0),
    owner_role_id TEXT NOT NULL
        CHECK(typeof(owner_role_id) = 'text' AND length(owner_role_id) > 0),
    parent_proposal_id TEXT CHECK(
        parent_proposal_id IS NULL
        OR (typeof(parent_proposal_id) = 'text' AND length(parent_proposal_id) > 0)
    ),
    target_artifact_id TEXT CHECK(
        target_artifact_id IS NULL
        OR (typeof(target_artifact_id) = 'text' AND length(target_artifact_id) > 0)
    ),
    target_artifact_revision INTEGER CHECK(
        target_artifact_revision IS NULL
        OR (typeof(target_artifact_revision) = 'integer' AND target_artifact_revision > 0)
    ),
    source_ids_json TEXT NOT NULL CHECK(typeof(source_ids_json) = 'text'),
    accepted_event_id TEXT NOT NULL
        CHECK(typeof(accepted_event_id) = 'text' AND length(accepted_event_id) > 0),
    accepted_transaction_id TEXT NOT NULL
        CHECK(typeof(accepted_transaction_id) = 'text' AND length(accepted_transaction_id) > 0),
    request_fingerprint TEXT NOT NULL CHECK(
        typeof(request_fingerprint) = 'text'
        AND length(request_fingerprint) = 64
        AND request_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    created_at TEXT NOT NULL
        CHECK(typeof(created_at) = 'text' AND length(created_at) > 0),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, proposal_id),
    UNIQUE(run_id, invocation_id),
    UNIQUE(run_id, accepted_event_id),
    UNIQUE(run_id, accepted_transaction_id),
    UNIQUE(run_id, artifact_id, artifact_revision),
    CHECK(
        (proposal_kind = 'candidate'
            AND parent_proposal_id IS NULL
            AND target_artifact_id IS NULL
            AND target_artifact_revision IS NULL)
        OR
        (proposal_kind IN ('screened', 'claim_drafts')
            AND parent_proposal_id IS NOT NULL
            AND target_artifact_id IS NULL
            AND target_artifact_revision IS NULL)
        OR
        (proposal_kind = 'audit'
            AND parent_proposal_id IS NULL
            AND target_artifact_id IS NOT NULL
            AND target_artifact_revision IS NOT NULL)
    ),
    FOREIGN KEY(run_id, invocation_id)
        REFERENCES agent_invocations(run_id, invocation_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, artifact_id, artifact_revision)
        REFERENCES artifact_revisions(run_id, artifact_id, revision)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_event_id)
        REFERENCES events(run_id, event_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id)
        REFERENCES transactions(run_id, transaction_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id, accepted_event_id)
        REFERENCES transaction_events(run_id, transaction_id, event_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, accepted_transaction_id, artifact_id, artifact_revision)
        REFERENCES transaction_artifact_revisions(
            run_id,
            transaction_id,
            artifact_id,
            revision
        ) ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, parent_proposal_id)
        REFERENCES accepted_proposals(run_id, proposal_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, target_artifact_id, target_artifact_revision)
        REFERENCES artifact_revisions(run_id, artifact_id, revision)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE proposal_source_bindings (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    proposal_id TEXT NOT NULL
        CHECK(typeof(proposal_id) = 'text' AND length(proposal_id) > 0),
    source_id TEXT NOT NULL
        CHECK(typeof(source_id) = 'text' AND length(source_id) > 0),
    schema_version TEXT NOT NULL
        CHECK(
            typeof(schema_version) = 'text'
            AND schema_version = 'briefloop.proposal_source_binding.v2'
        ),
    payload_json TEXT NOT NULL CHECK(typeof(payload_json) = 'text'),
    PRIMARY KEY(run_id, proposal_id, source_id),
    FOREIGN KEY(run_id, proposal_id)
        REFERENCES accepted_proposals(run_id, proposal_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, source_id)
        REFERENCES sources(run_id, source_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_sources (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    transaction_id TEXT NOT NULL
        CHECK(typeof(transaction_id) = 'text' AND length(transaction_id) > 0),
    position INTEGER NOT NULL
        CHECK(typeof(position) = 'integer' AND position >= 0),
    source_id TEXT NOT NULL
        CHECK(typeof(source_id) = 'text' AND length(source_id) > 0),
    PRIMARY KEY(run_id, transaction_id, position),
    UNIQUE(run_id, source_id),
    FOREIGN KEY(run_id, transaction_id)
        REFERENCES transactions(run_id, transaction_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, source_id)
        REFERENCES sources(run_id, source_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_proposals (
    run_id TEXT NOT NULL
        CHECK(typeof(run_id) = 'text' AND length(run_id) > 0),
    transaction_id TEXT NOT NULL
        CHECK(typeof(transaction_id) = 'text' AND length(transaction_id) > 0),
    position INTEGER NOT NULL
        CHECK(typeof(position) = 'integer' AND position >= 0),
    proposal_id TEXT NOT NULL
        CHECK(typeof(proposal_id) = 'text' AND length(proposal_id) > 0),
    PRIMARY KEY(run_id, transaction_id, position),
    UNIQUE(run_id, proposal_id),
    FOREIGN KEY(run_id, transaction_id)
        REFERENCES transactions(run_id, transaction_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id, proposal_id)
        REFERENCES accepted_proposals(run_id, proposal_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TRIGGER workspace_run_heads_no_delete BEFORE DELETE ON workspace_run_heads
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER sources_no_update BEFORE UPDATE ON sources
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER sources_no_delete BEFORE DELETE ON sources
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER accepted_proposals_no_update BEFORE UPDATE ON accepted_proposals
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER accepted_proposals_no_delete BEFORE DELETE ON accepted_proposals
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER proposal_source_bindings_no_update
BEFORE UPDATE ON proposal_source_bindings
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER proposal_source_bindings_no_delete
BEFORE DELETE ON proposal_source_bindings
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_sources_no_update BEFORE UPDATE ON transaction_sources
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_sources_no_delete BEFORE DELETE ON transaction_sources
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_proposals_no_update BEFORE UPDATE ON transaction_proposals
BEGIN SELECT RAISE(ABORT, 'append_only'); END;
CREATE TRIGGER transaction_proposals_no_delete BEFORE DELETE ON transaction_proposals
BEGIN SELECT RAISE(ABORT, 'append_only'); END;

PRAGMA user_version = 2;
COMMIT;
