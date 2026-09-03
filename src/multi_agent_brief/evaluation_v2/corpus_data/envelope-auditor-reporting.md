# Auditor finding reporting contract (harness-owned)

Every finding in output/intermediate/audit_report.json MUST:
- use a finding_type that is exactly one of: number_without_source,
  stale_source, claim_support_matrix_blocking_support,
  target_priority_claim_missing_from_summary;
- carry an anchor: related_claim_id for claim-scoped defects, or
  line_number for the offending line in output/intermediate/audited_brief.md.

Findings outside this vocabulary or without an anchor are recorded but
cannot be matched. This contract is identical for every evaluated variant
and is versioned with the corpus; its content hash is recorded alongside
corpus_sha and roles_sha in every reward ledger record.
