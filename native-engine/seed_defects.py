"""Seeded-defect evaluation for the independent Reviewer.

Plants known problems into a COPY of the acceptance workspace so each review
run can be scored per problem type instead of on one or two incidental finds.
The edits change the frozen version in place (markdown, editor document and
its hash) and leave sources untouched, so every planted problem contradicts or
lacks a source the packet still carries. Never point this at a real workspace.

Two problems already present in the acceptance version, checked by hand, are
scored alongside the planted ones.
"""
import json
import re
import sqlite3
from pathlib import Path

VERSION = 'brief_a56c592a9aaf461d_r1'

# name -> (original text, replacement). Each original occurs exactly once in
# the report; the sources keep the true value.
SEEDS = {
    # Results PR: net income USD 45.8 million.
    'net_income_changed': ('净利润4,580万美元，去年同期为250万美元', '净利润5,480万美元，去年同期为250万美元'),
    # Proclamation 11052 was signed on 6 August.
    'proclamation_date_changed': ('8月6日签署的Proclamation 11052', '8月16日签署的Proclamation 11052'),
    # 123.4 / 58.9 - 1 = +109.51%; the planted figure matches no base.
    'growth_miscalculated': ('增长109.51%', '增长92.4%'),
    # The second 1 GW module line is planned for September, not running.
    'plan_stated_as_done': ('Humble第二条1GW组件线预计2026年9月投产，', 'Humble第二条1GW组件线已于2026年8月投产，'),
    # No source mentions a new cell plant; the sentence keeps a real citation.
    'fabricated_claim': ('（第一季度收入1.428亿美元）。', '（第一季度收入1.428亿美元）。公司同时宣布在得州新建5GW电池片工厂，预计2027年投产。'),
}

# A run detects a problem when one finding, assessment finding, unchecked item
# or response-check reason matches every pattern (the chart footnote is also
# the subject of an author response the Reviewer may judge unresolved).
# First-pass matching; read the texts before citing a count.
ISSUES = {
    'chart_footnote': [r'fig_dc483082d36b4165|放量', r'脚注|内嵌|PNG', r'1\.5|不一致|矛盾|冲突|相反'],
    'cash_58_9_vs_85_9': [r'85\.9|8,590', r'58\.9|5,890'],
    'net_income_changed': [r'5,480|5480|54\.8'],
    'proclamation_date_changed': [r'8月16日', r'Proclamation|11052|签署|232'],
    'growth_miscalculated': [r'92\.4'],
    'plan_stated_as_done': [r'已于2026年8月投产|已于8月投产|8月投产|已投产'],
    'fabricated_claim': [r'5\s?GW', r'电池片|工厂'],
}


def seed(workspace, version_id=VERSION):
    from briefloop.document_model import document_hash
    db = Path(workspace) / 'briefloop.db'
    connection = sqlite3.connect(db)
    markdown, document = connection.execute(
        'SELECT markdown,editor_document FROM briefs WHERE id=?', (version_id,)).fetchone()
    for name, (old, new) in SEEDS.items():
        for label, text in (('markdown', markdown), ('document', document)):
            if text.count(old) != 1:
                raise ValueError(f'{name}: expected one occurrence in {label}, found {text.count(old)}')
        markdown = markdown.replace(old, new)
        document = document.replace(old, new)
    digest = document_hash(json.loads(document))
    with connection:
        connection.execute('UPDATE briefs SET markdown=?,editor_document=?,hash=? WHERE id=?',
                           (markdown, document, digest, version_id))
    connection.close()
    return sorted(SEEDS)


def detections(result, issues=ISSUES):
    texts = [f.get('description', '') + ' ' + str(f.get('evidence', '')) + ' ' + str(f.get('report_quote', ''))
             for f in result.get('findings', []) + (result.get('assessment') or {}).get('findings', [])]
    texts += [u.get('description', '') for u in result.get('unchecked_items', [])]
    texts += [c.get('reason', '') for c in result.get('response_checks', []) if c.get('decision') == 'unresolved']
    return {name: any(all(re.search(p, text) for p in patterns) for text in texts)
            for name, patterns in issues.items()}
