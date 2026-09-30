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
# Benchmark v2 (#757 design): graded removal, and two changes a good evaluator must
# NOT penalise for implication existence. Reordering can still affect expression/overall.
# They act on sentences labelled implication (label_sentences.py) and need those labels.
V2_KINDS = ('implications_removed', 'implications_half_removed', 'implications_paraphrased', 'paragraphs_reordered')
SCORE_INVARIANT = ('control', 'implications_paraphrased', 'paragraphs_reordered')

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
        # Any heading opens the body; a report's title block before it has no citations.
        if re.match(r'#{1,6} ', text):
            seen_heading = True
            continue
        if (seen_heading and text and not text.startswith(('#', '!', '|', '- ', '* ', '>'))
                and CITATION.search(text) and len(text) > 80):
            out.append(i)
    return blocks, out


def _sentences(paragraph):
    # A sentence keeps the citation markers that follow its full stop.
    return re.findall(r'[^。！？]*[。！？](?:\s*\[@src_[A-Za-z0-9]+\])*', paragraph)


def _plain(sentence):
    return re.sub(r'\[@[^\]]*\]', '', sentence).replace('**', '').strip()


def judgments(markdown, labels=None):
    """Judgment sentences as (block, sentence index, text).

    With labels (label_sentences.py, checked by a person) these are the sentences
    labelled implication. Without, the legacy heuristic of conclusions_removed.
    """
    blocks, body = _body_paragraphs(markdown)
    found = []
    if labels is not None:
        for paragraph in labels['paragraphs']:
            if 'error' in paragraph:
                continue
            sentences = _sentences(blocks[paragraph['block']])
            for item in paragraph['sentences']:
                n = item['i']
                if item['label'] == 'implication' and n < len(sentences) and _plain(sentences[n]) == item['text']:
                    found.append((paragraph['block'], n, sentences[n]))
        return found
    for index in body:
        sentences = _sentences(blocks[index])
        if len(sentences) < 2:
            continue
        for n, sentence in enumerate(sentences):
            if n == 0 or JUDGMENT.search(_plain(sentence)):
                found.append((index, n, sentence))
    return found


def _remove(blocks, targets):
    """Remove the addressed sentences, including a paragraph whose sentences all go."""
    removed = []
    for index in sorted({b for b, _ in targets}):
        original = blocks[index]
        matches = list(re.finditer(r'[^。！？]*[。！？](?:\s*\[@src_[A-Za-z0-9]+\])*', original))
        chunks, cursor, selected = [], 0, []
        for n, match in enumerate(matches):
            chunks.append(original[cursor:match.start()])
            if (index, n) in targets:
                selected.append(match.group().strip())
            else:
                chunks.append(match.group())
            cursor = match.end()
        chunks.append(original[cursor:])
        text = ''.join(chunks).strip()
        # Bold markup can span a sentence boundary. A removed lead leaves one marker.
        if text.count('**') % 2:
            text = text.replace('**', '', 1).strip()
        if not re.sub(r'\[@[^\]]*\]', '', text).replace('**', '').strip():
            text = ''
        if text != original and selected:
            blocks[index] = text
            removed.extend(selected)
    return removed


def _changed(original, blocks, truth):
    """A non-control leg must actually change the submitted Markdown."""
    original_blocks = original.split('\n\n')
    # Preserve original spacing; remove only paragraphs emptied by the removal.
    if truth.get('removed') and len(blocks) == len(original_blocks):
        blocks = [block for index, block in enumerate(blocks) if block or not original_blocks[index]]
    updated = '\n\n'.join(blocks)
    return (updated, truth) if updated != original else (original, None)


def degrade(markdown, kind, source_text, paraphrases=None, labels=None):
    """Return (new markdown, truth) for one kind; truth=None if the report does not allow it."""
    blocks, body = _body_paragraphs(markdown)
    if not body:
        return markdown, None
    if kind == 'filler':
        at = body[min(1, len(body) - 1)]
        blocks.insert(at + 1, FILLER)
        return _changed(markdown, blocks, {'kind': kind, 'inserted': FILLER, 'needles': NEEDLES[kind]})
    if kind == 'off_topic':
        at = body[len(body) // 2]
        blocks.insert(at + 1, OFF_TOPIC)
        return _changed(markdown, blocks, {'kind': kind, 'inserted': OFF_TOPIC, 'needles': NEEDLES[kind]})
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
            return _changed(markdown, blocks, {'kind': kind, 'inserted': paragraph, 'source_id': cited[0],
                                               'needles': NEEDLES[kind]})
        return markdown, None
    if kind == 'conclusions_removed':
        targets = {(b, n) for b, n, _ in judgments(markdown)}
        removed = _remove(blocks, targets)
        if not removed:
            return markdown, None
        return _changed(markdown, blocks, {'kind': kind, 'removed': removed, 'needles': []})
    if kind in ('implications_removed', 'implications_half_removed', 'implications_paraphrased') and labels is None:
        return markdown, None
    if kind in ('implications_removed', 'implications_half_removed'):
        eligible = sorted(judgments(markdown, labels), key=lambda item: item[:2])
        # Half is measured in target sentences, not in paragraphs. Odd counts round down.
        selected = eligible if kind == 'implications_removed' else eligible[:len(eligible) // 2]
        targets = {(b, n) for b, n, _ in selected}
        removed = _remove(blocks, targets)
        if not removed:
            return markdown, None
        return _changed(markdown, blocks, {
            'kind': kind, 'removed': removed, 'needles': [],
            'eligible_count': len(eligible), 'selected_count': len(selected),
            'removed_count': len(removed), 'target_scope': 'confirmed_implication_sentences_only',
            'selection': 'all' if kind == 'implications_removed' else 'first_half_floor',
            'selected_targets': [{'block': b, 'i': n, 'text': _plain(text)} for b, n, text in selected],
        })
    if kind == 'implications_paraphrased':
        # Cached same-meaning replacements. Preserve untouched sentence spans and tails.
        replaced = []
        targets = {(b, n): sentence for b, n, sentence in judgments(markdown, labels)}
        for index in sorted({b for b, _ in targets}):
            original = blocks[index]
            matches = list(re.finditer(r'[^。！？]*[。！？](?:\s*\[@src_[A-Za-z0-9]+\])*', original))
            chunks, cursor = [], 0
            for n, match in enumerate(matches):
                chunks.append(original[cursor:match.start()])
                sentence = match.group()
                new = (paraphrases or {}).get(_plain(sentence)) if (index, n) in targets else None
                if new and new.strip() != _plain(sentence):
                    citations = ''.join(re.findall(r'\s*\[@src_[A-Za-z0-9]+\]', sentence))
                    # Keep the exact bold marker counts; a closing marker may be in the next span.
                    lead = '**' if sentence.lstrip().startswith('**') else ''
                    tail = '**' if sentence.rstrip().endswith('**') else ''
                    chunks.append(lead + new + tail + citations)
                    replaced.append({'original': _plain(sentence), 'paraphrase': new})
                else:
                    chunks.append(sentence)
                cursor = match.end()
            chunks.append(original[cursor:])
            blocks[index] = ''.join(chunks)
        if not replaced:
            return markdown, None
        return _changed(markdown, blocks, {'kind': kind, 'replaced': replaced, 'needles': []})
    if kind == 'paragraphs_reordered':
        # A heading is a semantic boundary: reorder prose only inside the same section.
        sections, heading = {}, None
        for index, block in enumerate(blocks):
            if re.match(r'#{1,6} ', block.strip()):
                heading = index
            elif index in body:
                sections.setdefault(heading, []).append(index)
        reordered = []
        for heading, indexes in sections.items():
            if len(indexes) < 2:
                continue
            original = [blocks[i] for i in indexes]
            if original == list(reversed(original)):
                continue
            for index, text in zip(indexes, reversed(original)):
                blocks[index] = text
            reordered.append({'heading_block': heading, 'blocks': indexes, 'order': list(reversed(indexes))})
        if not reordered:
            return markdown, None
        return _changed(markdown, blocks, {'kind': kind, 'sections': reordered, 'needles': []})
    raise ValueError(kind)


def apply(workspace, version_id, kind, paraphrases=None, labels=None):
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
    markdown, truth = ((brief['markdown'], {'kind': 'control'}) if kind == 'control'
                       else degrade(brief['markdown'], kind, source_text, paraphrases, labels))
    if truth is None or (kind != 'control' and markdown == brief['markdown']):
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
