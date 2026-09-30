import json
import pytest
from briefloop.document_model import markdown_document
from briefloop.finding_anchors import validate_findings
from briefloop.evidence import blocks
from briefloop.store import Store
from briefloop.review import build_packet,check_review,accept_review
from review_checks import for_version


def finding(quote,**extra):
    return dict(report_quote=quote,**extra)


def test_rendered_quotes_and_scope_without_joining_passages():
    doc=markdown_document('Revenue **12** million [USD](https://example.com).\n\nGross margin flat.\n\n- First item.\n- Second item.\n\n| A | B |\n|---|---|\n| Left cell | Right cell |')
    validate_findings(doc,[finding('Revenue 12 million USD.'),finding('Revenue **12** million'),finding('First item.'),finding('Left cell')])
    wrong=next(key for key,node in blocks(doc).items() if node.get('content',[{}])[0].get('text')=='Gross margin flat.')
    for bad in [finding('Revenue 12 … USD.'),finding('USD. Gross margin'),finding('Left cellRight cell'),finding('Revenue 12 million USD.',block_ids=[wrong])]:
        with pytest.raises(ValueError,match='report_quote'):validate_findings(doc,[bad])
    validate_findings(doc,[finding('',kind='missing_requirement'),finding('Old removed quote',response_to='response_old')])


def test_citation_views_are_specific_not_arbitrary_number_stripping():
    doc=markdown_document('Revenue 12. [@src_abc] Margin 4. Literal [2].')
    for quote in ['Revenue 12. [@src_abc]','Revenue 12. [src_abc]','Revenue 12. [1]','Literal [2].']:
        validate_findings(doc,[finding(quote)])
    with pytest.raises(ValueError):validate_findings(doc,[finding('Revenue 12. [2]')])
    linked=markdown_document('Revenue 12. [1](#source-src_abc) Margin 4.')
    for quote in ['Revenue 12. 1 Margin','Revenue 12. [1] Margin','Revenue 12. [@src_abc] Margin']:
        validate_findings(linked,[finding(quote)])
    validate_findings(doc,[finding('Published source title')],reader_preview='## Sources\n\n1. Published source title')
    bid=next(iter(blocks(doc)))
    with pytest.raises(ValueError):validate_findings(doc,[finding('Published source title',block_ids=[bid])],reader_preview='Published source title')


def test_new_review_and_assessment_reject_bad_quote_without_partial_save(tmp_path):
    store=Store(tmp_path);source=store.add_source('Source','Revenue 12 million USD.')
    run=store.create_run({'title':'Report','objective':'Explain'},[source['id']])
    brief=store.publish(run['id'],{'title':'Report','markdown':'Revenue **12** million USD.'})
    fp,files=build_packet(store,brief['id'],store.root/'review')
    with store.tx() as c:
        c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',('review_test',brief['id'],None,fp,'running',json.dumps({'packet_path':'review/packet','files':files}),None,'2026','2026'))
    item={'kind':'expression','severity':'minor','description':'Check','evidence':'Check original','report_quote':'Revenue 12 … USD.'}
    value={'fingerprint':fp,'version_id':brief['id'],'status':'complete','summary':'Checked','coverage_scan_complete':True,'requirement_checks':for_version(store,brief['id']),'findings':[item]}
    with pytest.raises(ValueError,match='report_quote'):check_review(store,'review_test',value)
    assert store.rows("SELECT result FROM reviews WHERE id='review_test'")[0]['result'] is None
    assert not store.rows('SELECT * FROM review_findings')
    score={'brief_hash':brief['hash'],'status':'complete','summary':'Checked','overall':'建议修改','evidence':3,'coverage':3,'analysis':3,'expression':3,'findings':[{**item,'dimension':'expression'}]}
    with pytest.raises(ValueError,match='report_quote'):store.validate_assessment(brief['id'],score)
    with pytest.raises(ValueError,match='report_quote'):store.assess(brief['id'],score)
    from briefloop.interactive_runtime import _usable_output
    cache=store.root/'cache';cache.mkdir()
    (cache/'assessment.json').write_text(json.dumps(score))
    assert not _usable_output({'kind':'assess','payload':json.dumps({'version_id':brief['id']})},cache,store)
    assert not store.rows('SELECT * FROM assessments')
    item['report_quote']='Revenue 12 million USD.'
    accept_review(store,'review_test',value)
    assert check_review(store,'review_test',value).status=='complete'
    # An already accepted historical result is immutable and replayable, not
    # retroactively relabeled by newly introduced location admission checks.
    old={**value,'findings':[{**item,'report_quote':'Legacy stitched … quote'}]}
    with store.tx() as c:c.execute('UPDATE reviews SET result=? WHERE id=?',(json.dumps(old),'review_test'))
    assert check_review(store,'review_test',old).findings[0].report_quote=='Legacy stitched … quote'


def test_image_packet_quote_preserves_its_actual_wrapper():
    doc=markdown_document('![Quarterly revenue chart](briefloop-figure:fig_abc)')
    validate_findings(doc,[finding('[图：Quarterly revenue chart]'),finding('Quarterly revenue chart')])
    with pytest.raises(ValueError):validate_findings(doc,[finding('[图：Invented chart]')])
