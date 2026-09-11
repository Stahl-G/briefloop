"""Schema-drift regressions from the W8 run.

The reviewer mixed ReviewFinding and Assessment.Finding fields, added a stray
top-level `overall`, and once omitted `assessment` entirely. None of that may fail
the whole generation task, and the useful parts must survive.
"""
from briefloop.review import ReviewOutput


def _drifted_output():
    return {
        'fingerprint': 'fp', 'version_id': 'brief_1', 'status': 'complete', 'summary': 's',
        'coverage_scan_complete': True,
        'overall': '建议修改',  # stray top-level key that belongs to the assessment
        'findings': [{'kind': 'expression', 'severity': 'major', 'description': '重复免责声明',
                      'evidence': 'e', 'report_quote': 'q', 'dimension': 'expression',
                      'locator': 'block_1', 'requirement': 'clause_0911ca8109dfb7ae',
                      'source_id': 'src_07ff0942584f48d2'}],
        'assessment': {'brief_hash': 'h', 'status': 'complete', 'summary': 's', 'overall': '建议修改',
                       'evidence': 3, 'coverage': 3, 'analysis': 3, 'expression': 2,
                       'findings': [{'dimension': 'expression', 'severity': 'minor', 'description': 'd',
                                     'kind': 'expression', 'block_ids': ['block_87a6ad6b6a236608678e']}]},
    }


def test_review_output_tolerates_known_field_drift():
    out = ReviewOutput.model_validate(_drifted_output())
    assert out.findings[0].requirement == 'clause_0911ca8109dfb7ae'
    assert out.findings[0].source_id == 'src_07ff0942584f48d2'
    assert out.assessment is not None and out.assessment.expression == 2
    assert out.assessment.findings[0].kind == 'expression'
    assert out.assessment.findings[0].block_ids == ['block_87a6ad6b6a236608678e']
    assert not hasattr(out, 'overall')


def test_review_can_arrive_without_a_score():
    value = _drifted_output()
    del value['assessment']
    out = ReviewOutput.model_validate(value)
    assert out.assessment is None
    assert out.findings[0].kind == 'expression'
