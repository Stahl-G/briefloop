"""Shared unresolved-item semantics for delivery checks and reader exports."""


def unresolved_findings(findings):
    return [finding for finding in findings
            if finding.get('status') not in ('resolved', 'dismissed_with_evidence')]


def unresolved_gap_records(detail):
    # Structured records supersede the legacy strings even when all are resolved.
    # Empty/missing records retain the legacy fallback used by formal delivery.
    records = detail.get('gap_records') or [
        {'impact': str(text), 'status': 'open'} for text in (detail.get('gaps') or [])]
    return [record for record in records if record.get('status') != 'resolved']
