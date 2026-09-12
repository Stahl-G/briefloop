"""Revision necessity is distinct from impact severity and aggregate scores.

Agents classify the actual findings/checks. This module only applies the bounded
repair rule to those structured decisions; it never guesses from prose keywords.
"""
from .models import must_fix

OPEN_STATUSES = {'open', 'addressed_pending_review'}
REQUIRED_KINDS = {'contradiction', 'insufficient_evidence', 'missing_requirement', 'missing_binding'}


def _required(finding):
    return (finding.get('severity') == 'major'
            or finding.get('kind') in REQUIRED_KINDS
            or (finding.get('kind') in ('expression', 'execution_gap') and bool(finding.get('requirement_ids'))))


def _optional(finding):
    return (finding.get('severity') == 'minor' and not _required(finding)
            and (finding.get('kind') == 'expression'
                 or (not finding.get('kind') and finding.get('dimension') in ('expression', 'analysis'))))


def revision_reasons(assessment, findings, review=None):
    """Return concrete triggers from applicable, unresolved inputs only.

    Review kinds express a confirmed defect, while major/minor expresses impact.
    Linked expression/execution findings identify an explicit requirement breach;
    unlinked minor polish alone does not spend the automatic revision.
    """
    assessment = assessment or {}
    active = [f for f in findings if f.get('status') in OPEN_STATUSES
              and not f.get('stale') and f.get('applicable', True)]
    reasons = [{'type': 'review_finding', 'finding_id': f['id'], 'kind': f['data'].get('kind')}
               for f in active if _required(f['data'])]
    review = review or {}
    for field, identifier, kind in [('requirement_checks', 'requirement_id', 'requirement_check'),
                                    ('clause_checks', 'clause_id', 'clause_check')]:
        reasons.extend({'type': kind, identifier: item[identifier], 'status': item['status'],
                        'reason': item.get('reason', '')}
                       for item in review.get(field, []) if item.get('status') in ('partial', 'missing'))
    if assessment.get('status') == 'complete':
        scored = assessment.get('findings', [])
        reasons.extend({'type': 'assessment_finding', 'index': index,
                        'kind': finding.get('kind'), 'dimension': finding.get('dimension')}
                       for index, finding in enumerate(scored) if _required(finding)
                       or (finding.get('dimension') in ('evidence', 'coverage')
                           and bool(finding.get('evidence'))
                           and bool(finding.get('report_quote') or finding.get('requirement'))))
        if must_fix(assessment):
            reasons.append({'type': 'expression_score', 'score': assessment['expression']})
        # Preserve older unstructured assessments, but an explicitly optional
        # finding list must not become compulsory merely because of its headline.
        all_findings = [f['data'] for f in active] + scored
        optional_only = bool(all_findings) and all(_optional(f) for f in all_findings)
        if assessment.get('overall') in ('建议修改', '存在重大问题') and not optional_only:
            reasons.append({'type': 'overall', 'overall': assessment['overall']})
    return reasons


def applicable_inputs(store, review_state, assessment_row=None):
    """Exclude outdated packets and their derived scores without changing history."""
    from .review import validate_applicable_review
    import json
    applicable = {}
    def valid(identity):
        if identity not in applicable:
            try:
                validate_applicable_review(store, identity)
                applicable[identity] = True
            except (ValueError, OSError, KeyError):
                applicable[identity] = False
        return applicable[identity]
    findings = [f for f in review_state['findings'] if f.get('status') in OPEN_STATUSES and valid(f['review_id'])]
    review = next((row['result'] for row in review_state['reviews']
                   if row['result'] and row['status'] == 'complete' and valid(row['id'])), None)
    assessment = json.loads(assessment_row['data']) if assessment_row else {}
    if assessment_row and assessment_row['id'].startswith('assessment_review_'):
        if not valid(assessment_row['id'][len('assessment_'):]):
            assessment = {}
    return assessment, findings, review
