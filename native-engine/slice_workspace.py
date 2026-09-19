"""Cut an evaluation slice out of a real report.

A full report makes every Reviewer evaluation run cost what a full review
costs. A slice keeps one chapter of a real report with only the sources that
chapter cites, so seeded-defect evaluations stay on real text and real sources
at a fraction of the cost. The slice is written to a NEW workspace directory;
the source workspace is only read.

What a slice keeps and drops:
- report: the text before the first chapter heading plus one chapter, in both
  the markdown and the editor document;
- sources: those the chapter cites; conflicts outside them are removed;
- claims: bindings on kept blocks and their premises; other claims are removed
  so they do not reappear as unused candidates;
- history: other versions, reviews, findings, responses and jobs of the run
  are removed, so a slice carries no review-history work;
- requirements: rewritten to "review this chapter only", word limits scaled to
  the chapter, reader contract dropped (the full report's contract would make
  every other chapter "missing").

Usage:
  python slice_workspace.py list <workspace> <version>
  python slice_workspace.py cut <workspace> <version> <chapter index> <output dir>
"""
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

HEADING = re.compile(r'^## ', re.M)
CITATION = re.compile(r'\[@(src_[A-Za-z0-9]+)')


def _markdown_chapters(markdown):
    starts = [m.start() for m in HEADING.finditer(markdown)]
    head = markdown[:starts[0]] if starts else markdown
    chapters = [markdown[a:b] for a, b in zip(starts, starts[1:] + [len(markdown)])]
    return head, chapters


def _document_chapters(document):
    head, chapters = [], []
    for node in document.get('content', []):
        if node.get('type') == 'heading' and node.get('attrs', {}).get('level') == 2:
            chapters.append([node])
        elif chapters:
            chapters[-1].append(node)
        else:
            head.append(node)
    return head, chapters


def _block_ids(nodes):
    found = []

    def walk(node):
        identity = node.get('attrs', {}).get('blockId')
        if identity:
            found.append(identity)
        for child in node.get('content', []):
            walk(child)
    for node in nodes:
        walk(node)
    return found


def _cited(nodes):
    found = []

    def walk(node):
        if node.get('type') == 'citation' and node.get('attrs', {}).get('sourceId'):
            found.append(node['attrs']['sourceId'])
        for child in node.get('content', []):
            walk(child)
    for node in nodes:
        walk(node)
    return found


def _load(workspace, version):
    db = sqlite3.connect(Path(workspace) / 'briefloop.db')
    db.row_factory = sqlite3.Row
    brief = db.execute('SELECT * FROM briefs WHERE id=?', (version,)).fetchone()
    if brief is None:
        raise SystemExit(f'no version {version} in {workspace}')
    return db, brief


def chapters(workspace, version):
    db, brief = _load(workspace, version)
    _, parts = _markdown_chapters(brief['markdown'])
    rows = []
    for i, part in enumerate(parts):
        title = part.splitlines()[0][3:].strip()
        rows.append({'index': i, 'title': title, 'chars': len(part), 'sources': len(set(CITATION.findall(part)))})
    db.close()
    return rows


def cut(workspace, version, index, output):
    workspace, output = Path(workspace), Path(output)
    if output.exists():
        raise SystemExit(f'{output} exists; slices are never overwritten')
    db, brief = _load(workspace, version)
    run_id = brief['run_id']
    head, parts = _markdown_chapters(brief['markdown'])
    chapter = parts[index]
    title = chapter.splitlines()[0][3:].strip()
    markdown = head + chapter
    sources = set(CITATION.findall(markdown))
    document = None
    blocks = None
    if brief['editor_document']:
        doc = json.loads(brief['editor_document'])
        d_head, d_parts = _document_chapters(doc)
        kept = d_head + d_parts[index]
        document = {**doc, 'content': kept}
        blocks = set(_block_ids(kept))
        sources |= set(_cited(kept))
    db.close()

    shutil.copytree(workspace, output, ignore=shutil.ignore_patterns('jobs', 'server.json', '*.log'))
    from briefloop.document_model import brief_document, document_hash
    from briefloop.store import content_hash
    con = sqlite3.connect(output / 'briefloop.db')
    con.row_factory = sqlite3.Row
    with con:
        if blocks is None:
            from briefloop.evidence import blocks as block_map
            blocks = set(block_map(brief_document({'markdown': markdown, 'editor_document': None})))
        digest = document_hash(document) if document is not None else content_hash(markdown)
        detail = json.loads(brief['detail'])
        plain = re.sub(r'\s', '', markdown)
        detail['citations'] = [c for c in detail.get('citations', []) if c.get('source_id') in sources]
        detail['number_bindings'] = [n for n in detail.get('number_bindings', [])
                                     if n.get('source_id') in sources and re.sub(r'\s', '', n.get('report_quote', ''))[:12] in plain]
        report_data = detail.get('report_data')
        if isinstance(report_data, dict) and isinstance(report_data.get('records'), list):
            detail['report_data'] = {**report_data, 'records': [r for r in report_data['records']
                                                                if r.get('source_id') in sources]}
        detail['reader_contract'] = None
        detail['figures'] = [f for f in detail.get('figures', []) if f in markdown]
        con.execute('UPDATE briefs SET markdown=?,editor_document=?,hash=?,detail=?,parent_id=NULL WHERE id=?',
                    (markdown, json.dumps(document, ensure_ascii=False) if document is not None else None,
                     digest, json.dumps(detail, ensure_ascii=False), version))

        # Only this version remains in the run, with no review history.
        others = [r['id'] for r in con.execute('SELECT id FROM briefs WHERE run_id=? AND id<>?', (run_id, version))]
        versions = others + [version]
        marks = ','.join('?' * len(versions))
        con.execute(f'DELETE FROM review_responses WHERE finding_id IN (SELECT id FROM review_findings WHERE version_id IN ({marks}))', versions)
        con.execute(f'DELETE FROM review_findings WHERE version_id IN ({marks})', versions)
        con.execute(f'DELETE FROM reviews WHERE version_id IN ({marks})', versions)
        con.execute(f'DELETE FROM assessments WHERE version_id IN ({marks})', versions)
        con.execute(f'DELETE FROM claim_bindings WHERE version_id IN ({",".join("?" * len(others)) or "NULL"})', others)
        con.execute(f'DELETE FROM briefs WHERE id IN ({",".join("?" * len(others)) or "NULL"})', others)
        for row in con.execute('SELECT id,block_id FROM claim_bindings WHERE version_id=?', (version,)).fetchall():
            if row['block_id'] not in blocks:
                con.execute('DELETE FROM claim_bindings WHERE id=?', (row['id'],))

        # Claims: bound ones and their premises; source statements on kept sources.
        claims = {r['id']: json.loads(r['data']) for r in con.execute('SELECT id,data FROM claims WHERE run_id=?', (run_id,))}
        keep = {r['claim_id'] for r in con.execute('SELECT claim_id FROM claim_bindings WHERE version_id=?', (version,))}
        frontier = list(keep)
        while frontier:
            for premise in claims.get(frontier.pop(), {}).get('premise_claim_ids', []):
                if premise not in keep:
                    keep.add(premise)
                    frontier.append(premise)
        spans = {r['id']: r['source_id'] for r in con.execute('SELECT id,source_id FROM evidence_spans')}
        for claim_id, data in claims.items():
            if data.get('claim_role') == 'source_statement':
                if all(spans.get(s.get('span_id')) in sources for s in data.get('supports', [])):
                    keep.add(claim_id)
        for claim_id in set(claims) - keep:
            con.execute('DELETE FROM claims WHERE id=?', (claim_id,))
        con.execute('UPDATE claims SET previous_id=NULL WHERE run_id=? AND previous_id IS NOT NULL AND previous_id NOT IN (SELECT id FROM claims)', (run_id,))

        # Sources, conflicts and jobs of the run.
        con.execute('UPDATE runs SET source_ids=? WHERE id=?', (json.dumps(sorted(sources)), run_id))
        con.execute(f'DELETE FROM run_sources WHERE run_id=? AND source_id NOT IN ({",".join("?" * len(sources))})', (run_id, *sorted(sources)))
        for row in con.execute('SELECT id,data FROM conflicts').fetchall():
            if not set(json.loads(row['data']).get('source_ids', [])) <= sources:
                con.execute('DELETE FROM conflicts WHERE id=?', (row['id'],))
        con.execute('DELETE FROM events')
        con.execute('DELETE FROM jobs')

        requirements = json.loads(con.execute('SELECT requirements FROM runs WHERE id=?', (run_id,)).fetchone()[0])
        words = len(re.sub(r'\s|\[@[^\]]*\]', '', chapter))
        objective = (f'评测切片：原报告《{requirements.get("title", "")}》中的「{title}」一节。'
                     f'本版本只保留这一节及报告开头，其余章节不在本次范围，缺少其他章节不算问题；按本节内容与所引来源审阅。')
        for key in ('key_questions', 'manual_sections', 'sections', 'reader_contract'):
            if key in requirements:
                requirements[key] = [] if isinstance(requirements[key], list) else None
        requirements.update(objective=objective, raw_input=objective,
                            target_words=max(200, int(words * 0.9)), max_words=max(300, int(words * 1.3)))
        con.execute('UPDATE runs SET requirements=? WHERE id=?', (json.dumps(requirements, ensure_ascii=False), run_id))
    con.close()
    return {'workspace': str(output), 'version': version, 'chapter': title, 'chars': len(markdown),
            'sources': sorted(sources), 'blocks': len(blocks)}


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
    command = sys.argv[1]
    if command == 'list':
        for row in chapters(sys.argv[2], sys.argv[3]):
            print(json.dumps(row, ensure_ascii=False))
    elif command == 'cut':
        print(json.dumps(cut(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]), ensure_ascii=False))
    else:
        raise SystemExit(__doc__)
