BEGIN IMMEDIATE;

CREATE UNIQUE INDEX transactions_workspace_identity
ON transactions(workspace_id, run_id, transaction_id);

CREATE TABLE checkout_revisions (
  checkout_revision_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  parent_checkout_revision_id TEXT,
  schema_version TEXT NOT NULL CHECK(schema_version='briefloop.checkout_revision.v2'),
  manifest_sha256 TEXT NOT NULL,
  tree_sha256 TEXT NOT NULL,
  member_count INTEGER NOT NULL CHECK(member_count>=0),
  created_at TEXT NOT NULL,
  creator_transaction_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(workspace_id,run_id,checkout_revision_id),
  UNIQUE(workspace_id,tree_sha256),
  FOREIGN KEY(workspace_id,run_id,creator_transaction_id)
    REFERENCES transactions(workspace_id,run_id,transaction_id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(parent_checkout_revision_id)
    REFERENCES checkout_revisions(checkout_revision_id)
    DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE checkout_revision_members (
  checkout_revision_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  workspace_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  schema_version TEXT NOT NULL CHECK(schema_version='briefloop.checkout_revision_member.v2'),
  canonical_path TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  artifact_revision INTEGER NOT NULL CHECK(artifact_revision>0),
  blob_sha256 TEXT NOT NULL,
  byte_size INTEGER NOT NULL CHECK(byte_size>=0),
  payload_json TEXT NOT NULL,
  PRIMARY KEY(checkout_revision_id,ordinal),
  UNIQUE(checkout_revision_id,canonical_path),
  UNIQUE(checkout_revision_id,artifact_id,artifact_revision),
  FOREIGN KEY(workspace_id,run_id,checkout_revision_id)
    REFERENCES checkout_revisions(workspace_id,run_id,checkout_revision_id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(run_id,artifact_id,artifact_revision)
    REFERENCES artifact_revisions(run_id,artifact_id,revision)
    DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE receipt_checkout_bindings (
  workspace_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  transaction_id TEXT NOT NULL,
  schema_version TEXT NOT NULL CHECK(schema_version='briefloop.receipt_checkout_binding.v2'),
  pre_run_id TEXT NOT NULL,
  pre_checkout_revision_id TEXT,
  post_run_id TEXT NOT NULL,
  post_checkout_revision_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(workspace_id,run_id,transaction_id),
  UNIQUE(post_checkout_revision_id),
  FOREIGN KEY(workspace_id,run_id,transaction_id)
    REFERENCES transactions(workspace_id,run_id,transaction_id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(workspace_id,pre_run_id,pre_checkout_revision_id)
    REFERENCES checkout_revisions(workspace_id,run_id,checkout_revision_id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(workspace_id,post_run_id,post_checkout_revision_id)
    REFERENCES checkout_revisions(workspace_id,run_id,checkout_revision_id)
    DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE checkout_publication_intents (
  workspace_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  transaction_id TEXT NOT NULL,
  checkout_revision_id TEXT NOT NULL,
  schema_version TEXT NOT NULL CHECK(schema_version='briefloop.checkout_publication_intent.v2'),
  publication_identity_sha256 TEXT NOT NULL UNIQUE,
  pre_checkout_revision_id TEXT,
  post_checkout_revision_id TEXT NOT NULL,
  post_manifest_sha256 TEXT NOT NULL,
  post_tree_sha256 TEXT NOT NULL,
  changed_member_count INTEGER NOT NULL CHECK(changed_member_count>0),
  capability_profile_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(workspace_id,run_id,transaction_id,checkout_revision_id),
  FOREIGN KEY(workspace_id,run_id,transaction_id)
    REFERENCES receipt_checkout_bindings(workspace_id,run_id,transaction_id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(workspace_id,run_id,checkout_revision_id)
    REFERENCES checkout_revisions(workspace_id,run_id,checkout_revision_id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(pre_checkout_revision_id)
    REFERENCES checkout_revisions(checkout_revision_id)
    DEFERRABLE INITIALLY DEFERRED,
  CHECK(checkout_revision_id=post_checkout_revision_id)
);

CREATE TABLE checkout_publication_members (
  workspace_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  transaction_id TEXT NOT NULL,
  checkout_revision_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  schema_version TEXT NOT NULL CHECK(schema_version='briefloop.checkout_publication_member.v2'),
  canonical_path TEXT NOT NULL,
  temporary_basename TEXT NOT NULL,
  claim_basename TEXT NOT NULL,
  pre_kind TEXT NOT NULL CHECK(pre_kind IN ('absent','blob')),
  pre_sha256 TEXT,
  pre_size INTEGER,
  post_kind TEXT NOT NULL CHECK(post_kind IN ('absent','blob')),
  post_sha256 TEXT,
  post_size INTEGER,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(workspace_id,run_id,transaction_id,checkout_revision_id,ordinal),
  UNIQUE(workspace_id,run_id,transaction_id,checkout_revision_id,canonical_path),
  UNIQUE(workspace_id,run_id,transaction_id,checkout_revision_id,temporary_basename),
  UNIQUE(workspace_id,run_id,transaction_id,checkout_revision_id,claim_basename),
  FOREIGN KEY(workspace_id,run_id,transaction_id,checkout_revision_id)
    REFERENCES checkout_publication_intents(workspace_id,run_id,transaction_id,checkout_revision_id)
    DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE checkout_publication_acks (
  workspace_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  transaction_id TEXT NOT NULL,
  checkout_revision_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  schema_version TEXT NOT NULL CHECK(schema_version='briefloop.checkout_publication_ack.v2'),
  publication_identity_sha256 TEXT NOT NULL,
  capability_profile_sha256 TEXT NOT NULL,
  post_kind TEXT NOT NULL CHECK(post_kind IN ('absent','blob')),
  post_sha256 TEXT,
  post_size INTEGER,
  verification TEXT NOT NULL CHECK(verification='post_verified_durable'),
  cleanup_policy TEXT NOT NULL CHECK(cleanup_policy='retain_residue_v1'),
  appended_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(workspace_id,run_id,transaction_id,checkout_revision_id,ordinal),
  FOREIGN KEY(workspace_id,run_id,transaction_id,checkout_revision_id,ordinal)
    REFERENCES checkout_publication_members(workspace_id,run_id,transaction_id,checkout_revision_id,ordinal)
    DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE checkout_publication_cleanup_observations (
  cleanup_observation_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  transaction_id TEXT NOT NULL,
  checkout_revision_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  schema_version TEXT NOT NULL CHECK(schema_version='briefloop.checkout_publication_cleanup_observation.v2'),
  auxiliary_role TEXT NOT NULL CHECK(auxiliary_role IN ('temp','claim')),
  reason_code TEXT NOT NULL CHECK(reason_code IN ('checkout_projection_cleanup_retained','checkout_projection_cleanup_conflict','checkout_projection_cleanup_io_warning')),
  expected_kind TEXT NOT NULL,
  expected_sha256 TEXT,
  expected_size INTEGER,
  observed_kind TEXT NOT NULL CHECK(observed_kind IN ('absent','blob','unsafe','unreadable')),
  observed_sha256 TEXT,
  observed_size INTEGER,
  appended_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  FOREIGN KEY(workspace_id,run_id,transaction_id,checkout_revision_id,ordinal)
    REFERENCES checkout_publication_acks(workspace_id,run_id,transaction_id,checkout_revision_id,ordinal)
    DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE transaction_checkout_revisions (
  run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL,
  checkout_revision_id TEXT NOT NULL,
  PRIMARY KEY(run_id,transaction_id,position), UNIQUE(checkout_revision_id),
  FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(checkout_revision_id) REFERENCES checkout_revisions(checkout_revision_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE transaction_receipt_checkout_bindings (
  run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL,
  binding_transaction_id TEXT NOT NULL,
  PRIMARY KEY(run_id,transaction_id,position), UNIQUE(run_id,binding_transaction_id),
  FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE transaction_checkout_publication_intents (
  run_id TEXT NOT NULL, transaction_id TEXT NOT NULL, position INTEGER NOT NULL,
  checkout_revision_id TEXT NOT NULL,
  PRIMARY KEY(run_id,transaction_id,position), UNIQUE(run_id,transaction_id,checkout_revision_id),
  FOREIGN KEY(run_id,transaction_id) REFERENCES transactions(run_id,transaction_id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TRIGGER checkout_revisions_no_update BEFORE UPDATE ON checkout_revisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER checkout_revisions_no_delete BEFORE DELETE ON checkout_revisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER checkout_revision_members_no_update BEFORE UPDATE ON checkout_revision_members BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER checkout_revision_members_no_delete BEFORE DELETE ON checkout_revision_members BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER receipt_checkout_bindings_no_update BEFORE UPDATE ON receipt_checkout_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER receipt_checkout_bindings_no_delete BEFORE DELETE ON receipt_checkout_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER checkout_publication_intents_no_update BEFORE UPDATE ON checkout_publication_intents BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER checkout_publication_intents_no_delete BEFORE DELETE ON checkout_publication_intents BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER checkout_publication_members_no_update BEFORE UPDATE ON checkout_publication_members BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER checkout_publication_members_no_delete BEFORE DELETE ON checkout_publication_members BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER checkout_publication_acks_no_update BEFORE UPDATE ON checkout_publication_acks BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER checkout_publication_acks_no_delete BEFORE DELETE ON checkout_publication_acks BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER checkout_publication_cleanup_observations_no_update BEFORE UPDATE ON checkout_publication_cleanup_observations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER checkout_publication_cleanup_observations_no_delete BEFORE DELETE ON checkout_publication_cleanup_observations BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_checkout_revisions_no_update BEFORE UPDATE ON transaction_checkout_revisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_checkout_revisions_no_delete BEFORE DELETE ON transaction_checkout_revisions BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_receipt_checkout_bindings_no_update BEFORE UPDATE ON transaction_receipt_checkout_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_receipt_checkout_bindings_no_delete BEFORE DELETE ON transaction_receipt_checkout_bindings BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_checkout_publication_intents_no_update BEFORE UPDATE ON transaction_checkout_publication_intents BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER transaction_checkout_publication_intents_no_delete BEFORE DELETE ON transaction_checkout_publication_intents BEGIN SELECT RAISE(ABORT,'append_only'); END;

INSERT INTO schema_migrations(version,name) VALUES(5,'0005');
PRAGMA user_version=5;
COMMIT;
