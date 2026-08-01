BEGIN IMMEDIATE;

CREATE TABLE source_acquisition_attempt_compatibility_boundaries (
    workspace_id TEXT PRIMARY KEY,
    boundary_id TEXT NOT NULL
        CHECK(boundary_id='briefloop.source_acquisition_attempt_compatibility.v1'),
    legacy_receipt_max_committed_revision INTEGER NOT NULL
        CHECK(
            typeof(legacy_receipt_max_committed_revision)='integer'
            AND legacy_receipt_max_committed_revision>=0
        ),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

INSERT INTO source_acquisition_attempt_compatibility_boundaries(
    workspace_id,
    boundary_id,
    legacy_receipt_max_committed_revision
)
SELECT
    workspaces.workspace_id,
    'briefloop.source_acquisition_attempt_compatibility.v1',
    COALESCE(MAX(transactions.committed_revision),0)
FROM workspaces
LEFT JOIN transactions
    ON transactions.workspace_id=workspaces.workspace_id
GROUP BY workspaces.workspace_id;

CREATE TRIGGER source_acquisition_attempt_compatibility_boundaries_no_update
BEFORE UPDATE ON source_acquisition_attempt_compatibility_boundaries
BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER source_acquisition_attempt_compatibility_boundaries_no_delete
BEFORE DELETE ON source_acquisition_attempt_compatibility_boundaries
BEGIN SELECT RAISE(ABORT,'append_only'); END;

CREATE TABLE run_source_acquisition_attempt_authorizations (
    run_id TEXT NOT NULL,
    attempt_authorization_id TEXT NOT NULL,
    attempt_ordinal INTEGER NOT NULL CHECK(attempt_ordinal>=1),
    workspace_id TEXT NOT NULL,
    schema_version TEXT NOT NULL
        CHECK(schema_version='briefloop.run_source_acquisition_attempt_authorization.v1'),
    discovery_authorization_id TEXT NOT NULL,
    run_contract_fingerprint TEXT NOT NULL
        CHECK(length(run_contract_fingerprint)=64 AND run_contract_fingerprint NOT GLOB '*[^0-9a-f]*'),
    run_direction_fingerprint TEXT NOT NULL
        CHECK(length(run_direction_fingerprint)=64 AND run_direction_fingerprint NOT GLOB '*[^0-9a-f]*'),
    runtime_source_plan_fingerprint TEXT NOT NULL
        CHECK(length(runtime_source_plan_fingerprint)=64 AND runtime_source_plan_fingerprint NOT GLOB '*[^0-9a-f]*'),
    source_route_fingerprint TEXT NOT NULL
        CHECK(length(source_route_fingerprint)=64 AND source_route_fingerprint NOT GLOB '*[^0-9a-f]*'),
    provider_request_fingerprint TEXT NOT NULL
        CHECK(length(provider_request_fingerprint)=64 AND provider_request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    provider_id TEXT NOT NULL CHECK(provider_id='tavily'),
    route_id TEXT NOT NULL CHECK(route_id='web-search'),
    max_provider_calls INTEGER NOT NULL CHECK(max_provider_calls=2),
    provider_cost_status TEXT NOT NULL
        CHECK(provider_cost_status='not_reported_acknowledged'),
    previous_attempt_authorization_id TEXT,
    human_request_id TEXT NOT NULL,
    authorization_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL
        CHECK(length(request_fingerprint)=64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,attempt_authorization_id),
    UNIQUE(run_id,attempt_ordinal),
    UNIQUE(run_id,human_request_id),
    FOREIGN KEY(run_id,discovery_authorization_id)
        REFERENCES run_source_discovery_authorizations(run_id,authorization_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,previous_attempt_authorization_id)
        REFERENCES run_source_acquisition_attempt_authorizations(
            run_id,attempt_authorization_id
        ) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,authorization_event_id)
        REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id)
        REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id,authorization_event_id)
        REFERENCES transaction_events(run_id,transaction_id,event_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_run_source_acquisition_attempt_authorizations (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    attempt_authorization_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,transaction_id,attempt_authorization_id),
    FOREIGN KEY(run_id,transaction_id)
        REFERENCES transactions(run_id,transaction_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,attempt_authorization_id)
        REFERENCES run_source_acquisition_attempt_authorizations(
            run_id,attempt_authorization_id
        ) DEFERRABLE INITIALLY DEFERRED
);

CREATE TRIGGER run_source_acquisition_attempt_authorizations_no_update
BEFORE UPDATE ON run_source_acquisition_attempt_authorizations
BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_source_acquisition_attempt_authorizations_no_delete
BEFORE DELETE ON run_source_acquisition_attempt_authorizations
BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_source_acquisition_attempt_authorizations_no_update
BEFORE UPDATE ON transaction_run_source_acquisition_attempt_authorizations
BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_source_acquisition_attempt_authorizations_no_delete
BEFORE DELETE ON transaction_run_source_acquisition_attempt_authorizations
BEGIN SELECT RAISE(ABORT,'append_only'); END;

INSERT INTO schema_migrations(version,name) VALUES(11,'0011');
PRAGMA user_version=11;
COMMIT;
