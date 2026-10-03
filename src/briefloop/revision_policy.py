"""Revision necessity is distinct from impact severity and aggregate scores.

Agents classify the actual findings/checks. This module only applies the bounded
repair rule to those structured decisions; it never guesses from prose keywords.
"""
from .models import must_fix

OPEN_STATUSES = {'open', 'addressed_pending_review'}
REQUIRED_KINDS = {'contradiction', 'insufficient_evidence', 'missing_requirement', 'missing_binding'}


def _required(finding, *, candidate=False):
    # Candidate labels have no repair authority without source-bound chapter checks.
    if candidate and finding.get('kind') == 'no_implication':
        return False
    return (finding.get('severity') == 'major'
            or finding.get('kind') in REQUIRED_KINDS
            or (finding.get('kind') in ('expression', 'execution_gap') and bool(finding.get('requirement_ids'))))


def _optional(finding, *, candidate=False):
    if candidate and finding.get('kind') == 'no_implication':
        return True
    return (finding.get('severity') == 'minor' and not _required(finding, candidate=candidate)
            and (finding.get('kind') == 'expression'
                 or (candidate and finding.get('kind') in ('filler', 'restatement', 'off_topic'))
                 or (not finding.get('kind') and finding.get('dimension') in ('expression', 'analysis'))))


def revision_reasons(assessment, findings, review=None, *, analysis_context=None):
    """Return concrete triggers from applicable, unresolved inputs only.

    Review kinds express a confirmed defect, while major/minor expresses impact.
    Linked expression/execution findings identify an explicit requirement breach;
    unlinked minor polish alone does not spend the automatic revision.
    """
    assessment = assessment or {}
    candidate = analysis_context is not None
    active = [f for f in findings if f.get('status') in OPEN_STATUSES
              and not f.get('stale') and f.get('applicable', True)]
    reasons = [{'type': 'review_finding', 'finding_id': f['id'], 'kind': f['data'].get('kind')}
               for f in active if _required(f['data'], candidate=candidate)]
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
                       for index, finding in enumerate(scored) if _required(finding, candidate=candidate)
                       or (finding.get('dimension') in ('evidence', 'coverage')
                           and bool(finding.get('evidence'))
                           and bool(finding.get('report_quote') or finding.get('requirement'))))
        if must_fix(assessment):
            reasons.append({'type': 'expression_score', 'score': assessment['expression']})
        # Preserve older unstructured assessments, but an explicitly optional
        # finding list must not become compulsory merely because of its headline.
        all_findings = [f['data'] for f in active] + scored + assessment.get('analysis_check_unverified_findings', [])
        optional_only = bool(all_findings) and all(_optional(f, candidate=candidate) for f in all_findings)
        if assessment.get('overall') in ('建议修改', '存在重大问题') and not optional_only:
            reasons.append({'type': 'overall', 'overall': assessment['overall']})
        if analysis_context is not None:
            from .deliverable_spec import validate_analysis_checks
            checked = validate_analysis_checks(assessment, analysis_context['spec'], analysis_context['markdown'])
            reasons.extend({'type': 'analysis_check', 'check_id': check['id'],
                            'chapter_quote': check['chapter_quote'], 'requirement_quote': check['requirement_quote'],
                            'reason': check['rationale']}
                           for check in checked if check['status'] == 'missing')
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
