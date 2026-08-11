BEGIN IMMEDIATE;

-- Fresh-only guard: schema installation is valid only before the first
-- workspace exists. Existing schema17 databases cannot run this script.
CREATE TEMP TABLE schema18_fresh_guard(
    workspace_count INTEGER NOT NULL CHECK(workspace_count=0)
);
INSERT INTO schema18_fresh_guard SELECT COUNT(*) FROM workspaces;
DROP TABLE schema18_fresh_guard;

-- Schema11 bundle tables remain immutable historical evidence. Schema18 has no
-- executable producer for them and writes only the multi-task authority below.
CREATE TABLE run_source_acquisition_attempt_authorizations_v2 (
    run_id TEXT NOT NULL,
    attempt_authorization_id TEXT NOT NULL,
    attempt_ordinal INTEGER NOT NULL CHECK(attempt_ordinal>=1),
    workspace_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.run_source_acquisition_attempt_authorization.v2'),
    discovery_authorization_id TEXT NOT NULL,
    run_contract_fingerprint TEXT NOT NULL CHECK(length(run_contract_fingerprint)=64 AND run_contract_fingerprint NOT GLOB '*[^0-9a-f]*'),
    run_direction_fingerprint TEXT NOT NULL CHECK(length(run_direction_fingerprint)=64 AND run_direction_fingerprint NOT GLOB '*[^0-9a-f]*'),
    runtime_source_plan_fingerprint TEXT NOT NULL CHECK(length(runtime_source_plan_fingerprint)=64 AND runtime_source_plan_fingerprint NOT GLOB '*[^0-9a-f]*'),
    source_route_fingerprint TEXT NOT NULL CHECK(length(source_route_fingerprint)=64 AND source_route_fingerprint NOT GLOB '*[^0-9a-f]*'),
    provider_request_fingerprint TEXT NOT NULL CHECK(length(provider_request_fingerprint)=64 AND provider_request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    provider_id TEXT NOT NULL CHECK(provider_id='tavily'),
    route_id TEXT NOT NULL CHECK(route_id='web-search'),
    max_provider_calls INTEGER NOT NULL CHECK(max_provider_calls BETWEEN 4 AND 80),
    provider_cost_status TEXT NOT NULL CHECK(provider_cost_status='not_reported_acknowledged'),
    previous_attempt_authorization_id TEXT,
    human_request_id TEXT NOT NULL,
    authorization_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,attempt_authorization_id),
    UNIQUE(run_id,attempt_ordinal),
    UNIQUE(run_id,human_request_id),
    FOREIGN KEY(run_id,discovery_authorization_id) REFERENCES run_source_discovery_authorizations(run_id,authorization_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,previous_attempt_authorization_id) REFERENCES run_source_acquisition_attempt_authorizations_v2(run_id,attempt_authorization_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,authorization_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id,authorization_event_id) REFERENCES transaction_events(run_id,transaction_id,event_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE transaction_run_source_acquisition_attempt_authorizations_v2 (
    run_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position>=0),
    attempt_authorization_id TEXT NOT NULL,
    PRIMARY KEY(run_id,transaction_id,position),
    UNIQUE(run_id,transaction_id,attempt_authorization_id),
    FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,attempt_authorization_id) REFERENCES run_source_acquisition_attempt_authorizations_v2(run_id,attempt_authorization_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TRIGGER run_source_acquisition_attempt_authorizations_v2_no_update BEFORE UPDATE ON run_source_acquisition_attempt_authorizations_v2 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER run_source_acquisition_attempt_authorizations_v2_no_delete BEFORE DELETE ON run_source_acquisition_attempt_authorizations_v2 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_source_acquisition_attempt_authorizations_v2_no_update BEFORE UPDATE ON transaction_run_source_acquisition_attempt_authorizations_v2 BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_run_source_acquisition_attempt_authorizations_v2_no_delete BEFORE DELETE ON transaction_run_source_acquisition_attempt_authorizations_v2 BEGIN SELECT RAISE(ABORT,'append_only'); END;

CREATE TABLE runtime_source_search_plans (
    run_id TEXT NOT NULL,
    search_plan_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.runtime_source_search_plan.v2'),
    plan_revision INTEGER NOT NULL CHECK(plan_revision>=1),
    report_type TEXT NOT NULL,
    task_count INTEGER NOT NULL CHECK(task_count BETWEEN 1 AND 20),
    acquisition_spec_fingerprint TEXT NOT NULL CHECK(length(acquisition_spec_fingerprint)=64 AND acquisition_spec_fingerprint NOT GLOB '*[^0-9a-f]*'),
    plan_fingerprint TEXT NOT NULL CHECK(length(plan_fingerprint)=64 AND plan_fingerprint NOT GLOB '*[^0-9a-f]*'),
    record_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,search_plan_id),
    UNIQUE(run_id,plan_revision),
    FOREIGN KEY(run_id,record_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id,record_event_id) REFERENCES transaction_events(run_id,transaction_id,event_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE tavily_acquisition_bundle_records (
    run_id TEXT NOT NULL,
    bundle_record_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.tavily_acquisition_bundle_record.v2'),
    attempt_authorization_id TEXT NOT NULL,
    provider_response_artifact_id TEXT NOT NULL,
    provider_response_sha256 TEXT NOT NULL CHECK(length(provider_response_sha256)=64 AND provider_response_sha256 NOT GLOB '*[^0-9a-f]*'),
    bundle_status TEXT NOT NULL CHECK(bundle_status IN ('complete','partial','failed')),
    search_count INTEGER NOT NULL CHECK(search_count BETWEEN 1 AND 40),
    extract_batch_count INTEGER NOT NULL CHECK(extract_batch_count BETWEEN 0 AND 40),
    unique_url_count INTEGER NOT NULL CHECK(unique_url_count BETWEEN 0 AND 800),
    durable_content_count INTEGER NOT NULL CHECK(durable_content_count BETWEEN 0 AND unique_url_count),
    record_fingerprint TEXT NOT NULL CHECK(length(record_fingerprint)=64 AND record_fingerprint NOT GLOB '*[^0-9a-f]*'),
    record_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,bundle_record_id),
    UNIQUE(run_id,attempt_authorization_id),
    FOREIGN KEY(run_id,attempt_authorization_id) REFERENCES run_source_acquisition_attempt_authorizations_v2(run_id,attempt_authorization_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,provider_response_artifact_id) REFERENCES artifacts(run_id,artifact_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,record_event_id) REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id,record_event_id) REFERENCES transaction_events(run_id,transaction_id,event_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE market_data_snapshots (
    run_id TEXT NOT NULL,
    market_data_snapshot_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(schema_version='briefloop.market_data_snapshot.v1'),
    as_of_date TEXT NOT NULL,
    security_count INTEGER NOT NULL CHECK(security_count BETWEEN 1 AND 11),
    provider_id TEXT NOT NULL CHECK(provider_id='yahoo_finance_chart'),
    snapshot_fingerprint TEXT NOT NULL CHECK(length(snapshot_fingerprint)=64 AND snapshot_fingerprint NOT GLOB '*[^0-9a-f]*'),
    accepted_transaction_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,market_data_snapshot_id),
    UNIQUE(run_id,as_of_date),
    FOREIGN KEY(run_id,accepted_transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TRIGGER runtime_source_search_plans_no_update BEFORE UPDATE ON runtime_source_search_plans BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER runtime_source_search_plans_no_delete BEFORE DELETE ON runtime_source_search_plans BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER tavily_acquisition_bundle_records_no_update BEFORE UPDATE ON tavily_acquisition_bundle_records BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER tavily_acquisition_bundle_records_no_delete BEFORE DELETE ON tavily_acquisition_bundle_records BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER market_data_snapshots_no_update BEFORE UPDATE ON market_data_snapshots BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER market_data_snapshots_no_delete BEFORE DELETE ON market_data_snapshots BEGIN SELECT RAISE(ABORT,'append_only'); END;

INSERT INTO schema_migrations(version,name) VALUES(18,'0018');
PRAGMA user_version=18;
COMMIT;
