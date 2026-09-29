"""Value degradations for measuring whether the Evaluator notices low-value text (#757).

seed_generic.py plants factual defects. These plant text that is accurate or
harmless but makes a brief worth less: filler, a passage that only restates a
source, accurate background that does not serve the reader's question, and
conclusions removed so only facts remain. Each kind is planted alone into a
COPY of a frozen version; the truth is returned to the caller, never written
into the workspace. Never run this on a real workspace.

The editor document is rebuilt from the edited Markdown. The unchanged control
goes through the same rebuild, so a difference between them comes from the
planted text, not from the conversion.
"""
import json
import re
import sqlite3
from pathlib import Path

KINDS = ('filler', 'restated_source', 'off_topic', 'conclusions_removed')

FILLER = ('这一变化值得关注。从整体来看，相关情况仍需持续观察，各方面因素相互交织，未来走势存在一定不确定性。'
          '企业需要保持战略定力，积极应对挑战、把握机遇，在变局中寻求新的突破。')
# Accurate and on the industry, but not about anything a management report on
# this period is asked. No source in the slices covers it, so it is uncited.
OFF_TOPIC = ('光伏效应由法国物理学家埃德蒙·贝克勒尔于1839年首次发现；1954年，贝尔实验室制成第一块实用的硅太阳能电池，'
             '转换效率约为6%。此后数十年，晶体硅路线逐步成为光伏行业的主流技术。')
NEEDLES = {'filler': ['战略定力', '仍需持续观察', '值得关注。从整体来看'],
           'off_topic': ['贝克勒尔', '贝尔实验室', '1839'],
           'restated_source': ['来源材料列示']}
NOISE = re.compile(r'(?i)search|cookie|subscribe|log ?in|sign ?up|menu|javascript|privacy|©|copyright|click|browser|download|http')
CITATION = re.compile(r'\[@(src_[A-Za-z0-9]+)\]')
# A paragraph's leading judgment, or a sentence that draws an implication.
JUDGMENT = re.compile(r'(意味着|表明|显示出|因此|由此|建议|应当|需要关注|关键在于|经营含义|启示|风险在于)')


def _body_paragraphs(markdown):
    """Indexes of prose paragraphs under a chapter heading (no headings, figures, tables, lists)."""
    blocks = markdown.split('\n\n')
    seen_heading = False
    out = []
    for i, block in enumerate(blocks):
        text = block.strip()
        if text.startswith('## '):
            seen_heading = True
            continue
        if (seen_heading and text and not text.startswith(('#', '!', '|', '- ', '* ', '>', '1.'))
                and CITATION.search(text) and len(text) > 80):
            out.append(i)
    return blocks, out


def _sentences(paragraph):
    # A sentence keeps the citation markers that follow its full stop.
    return re.findall(r'[^。！？]*[。！？](?:\s*\[@src_[A-Za-z0-9]+\])*', paragraph)


def degrade(markdown, kind, source_text):
    """Return (new markdown, truth) for one kind; truth=None if the report does not allow it."""
    blocks, body = _body_paragraphs(markdown)
    if not body:
        return markdown, None
    if kind == 'filler':
        at = body[min(1, len(body) - 1)]
        blocks.insert(at + 1, FILLER)
        return '\n\n'.join(blocks), {'kind': kind, 'inserted': FILLER, 'needles': NEEDLES[kind]}
    if kind == 'off_topic':
        at = body[len(body) // 2]
        blocks.insert(at + 1, OFF_TOPIC)
        return '\n\n'.join(blocks), {'kind': kind, 'inserted': OFF_TOPIC, 'needles': NEEDLES[kind]}
    if kind == 'restated_source':
        # The paragraph's own facts listed again as a separate passage, with the
        # citation and without any judgment: accurate, sourced, and adds nothing.
        for index in body:
            facts = [re.sub(r'\[@[^\]]*\]', '', x).replace('**', '').strip() for x in _sentences(blocks[index])[1:]]
            facts = [x for x in facts if re.search(r'\d', x) and not JUDGMENT.search(x)][:2]
            cited = CITATION.findall(blocks[index])
            if len(facts) < 2 or not cited:
                continue
            paragraph = '来源材料列示的数据包括：' + ''.join(facts) + f'[@{cited[0]}]'
            blocks.insert(index + 1, paragraph)
            return '\n\n'.join(blocks), {'kind': kind, 'inserted': paragraph, 'source_id': cited[0],
                                         'needles': NEEDLES[kind]}
        return markdown, None
    if kind == 'conclusions_removed':
        removed = []
        for index in body:
            sentences = _sentences(blocks[index])
            if len(sentences) < 2:
                continue
            keep = []
            for n, sentence in enumerate(sentences):
                # The leading sentence of these reports is the paragraph's judgment.
                if n == 0 or JUDGMENT.search(re.sub(r'\[@[^\]]*\]', '', sentence)):
                    removed.append(sentence.strip())
                else:
                    keep.append(sentence)
            if keep:
                text = ''.join(keep).strip()
                # A removed bold lead sentence leaves its closing marker behind.
                if text.count('**') % 2:
                    text = text.replace('**', '', 1).strip()
                blocks[index] = text
        if not removed:
            return markdown, None
        return '\n\n'.join(blocks), {'kind': kind, 'removed': removed, 'needles': []}
    raise ValueError(kind)


def apply(workspace, version_id, kind):
    """Plant `kind` (or nothing for 'control') into a copied workspace; return the truth."""
    from briefloop.document_model import document_hash, markdown_document
    from briefloop.store import Store
    workspace = Path(workspace)
    store = Store(workspace)
    brief = store.one('briefs', version_id)

    def source_text(source_id):
        try:
            return store.source_text(source_id)
        except (ValueError, OSError):
            return ''
    markdown, truth = (brief['markdown'], {'kind': 'control'}) if kind == 'control' else degrade(brief['markdown'], kind, source_text)
    if truth is None:
        return None
    document = markdown_document(markdown)
    connection = sqlite3.connect(workspace / 'briefloop.db')
    with connection:
        connection.execute('UPDATE briefs SET markdown=?,editor_document=?,hash=? WHERE id=?',
                           (markdown, json.dumps(document, ensure_ascii=False), document_hash(document), version_id))
    connection.close()
    return {'version_id': version_id, **truth}


def mentioned(assessment, truth):
    """Whether any finding quotes or names the planted text."""
    texts = [' '.join(str(f.get(k, '')) for k in ('description', 'evidence', 'report_quote', 'suggestion', 'requirement'))
             for f in assessment.get('findings', [])]
    texts.append(str(assessment.get('summary', '')))
    normalized = [re.sub(r'\s', '', t) for t in texts]
    return any(re.sub(r'\s', '', n) in t for n in truth.get('needles', []) for t in normalized)
