BEGIN IMMEDIATE;

-- Fresh-only Solar market-data v2 cut. Runtime open never applies migrations
-- to an existing schema18 Store, and a fresh initializer has no workspace
-- rows to preserve. The v1 table created by immutable migration 0018 is
-- replaced before any run can write it; there is no dual-write authority.
CREATE TEMP TABLE schema19_fresh_guard(
    workspace_count INTEGER NOT NULL CHECK(workspace_count=0)
);
INSERT INTO schema19_fresh_guard SELECT COUNT(*) FROM workspaces;
DROP TABLE schema19_fresh_guard;

DROP TRIGGER market_data_snapshots_no_update;
DROP TRIGGER market_data_snapshots_no_delete;
DROP TABLE market_data_snapshots;

CREATE TABLE market_data_snapshots (
    run_id TEXT NOT NULL,
    market_data_snapshot_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK(
        schema_version='briefloop.market_data_snapshot.v2'
    ),
    report_window_start TEXT NOT NULL,
    report_window_end TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    universe_count INTEGER NOT NULL CHECK(universe_count BETWEEN 1 AND 20),
    security_count INTEGER NOT NULL CHECK(security_count BETWEEN 1 AND 20),
    provider_count INTEGER NOT NULL CHECK(provider_count BETWEEN 1 AND 8),
    workbook_sha256 TEXT CHECK(
        workbook_sha256 IS NULL OR (
            length(workbook_sha256)=64
            AND workbook_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    gap_count INTEGER NOT NULL CHECK(gap_count BETWEEN 0 AND 128),
    conflict_count INTEGER NOT NULL CHECK(conflict_count BETWEEN 0 AND 128),
    snapshot_fingerprint TEXT NOT NULL CHECK(
        length(snapshot_fingerprint)=64
        AND snapshot_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    record_event_id TEXT NOT NULL,
    accepted_transaction_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,market_data_snapshot_id),
    UNIQUE(run_id,as_of_date),
    FOREIGN KEY(run_id,record_event_id)
        REFERENCES events(run_id,event_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id)
        REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(run_id,accepted_transaction_id,record_event_id)
        REFERENCES transaction_events(run_id,transaction_id,event_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TRIGGER market_data_snapshots_no_update
BEFORE UPDATE ON market_data_snapshots
BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER market_data_snapshots_no_delete
BEFORE DELETE ON market_data_snapshots
BEGIN SELECT RAISE(ABORT,'append_only'); END;

INSERT INTO schema_migrations(version,name) VALUES(19,'0019');
PRAGMA user_version=19;
COMMIT;
