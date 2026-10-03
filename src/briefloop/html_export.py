"""Standalone, self-contained HTML report export for reading, print and archive."""
import base64
import hashlib
import html
import json
import re
from datetime import datetime
from importlib import resources
from urllib.parse import urlsplit

from .document_model import (normalize_document, brief_document, table_layout,
                             citation_label_text)
from .document_export import (reader_labels, reader_locator, reader_source_blocks,
                              without_duplicate_cover_heading)
from .models import report_language
from .delivery_state import unresolved_findings, unresolved_gap_records
from .task_labels import label as task_label


def _labels(language=None):
    if report_language(language) == 'en':
        return {'appendix_a': 'Appendix A · Sources & Evidence', 'appendix_b': 'Appendix B · Check Status',
                'appendix_c': 'Appendix C · About This File', 'toc': 'Contents',
                'released': 'This version has a formal delivery; this export is not part of its frozen package', 'draft': 'Working draft · not independently reviewed',
                'reviewing': 'Independent review in progress', 'review_failed': 'Latest independent review did not complete',
                'review_stale': 'Latest saved review does not apply to the current inputs · review required',
                'finding_status': {'open': 'Open', 'addressed_pending_review': 'Response awaiting review'},
                'gap_status': {'open': 'Open', 'addressed': 'Addressed · awaiting review',
                               'review_needed': 'Review required', 'unresolved': 'Unresolved'},
                'reviewed': 'Independently reviewed · not released', 'open_findings': 'open findings',
                'release_id': 'Release', 'manifest': 'Manifest',
                'version': 'Version', 'body_hash': 'Body hash', 'exported': 'Exported',
                'counts': '{n} sources · {m} citations',
                'stored_at': 'Stored', 'hash_label': 'Content SHA-256', 'locator': 'Snapshot locator',
                'no_excerpts': 'Excerpts are not included in this file.', 'backrefs': 'Cited in body',
                'original_title': 'Original title: ', 'excerpts_excluded': 'Excerpts not included in this file',
                'appendix_a_note': 'A locator or excerpt only marks where evidence was stored; it does not prove the source supports the conclusion. The web page may have changed — the stored snapshot and hash are authoritative.',
                'checks_unavailable': 'Deterministic checks are temporarily unavailable.',
                'checks_note': 'Deterministic checks do not prove factual correctness.',
                'numbers': 'Number bindings checked', 'matched': 'matched', 'unmatched': 'mismatched', 'skipped': 'not checked', 'layout': 'Layout', 'assessment': 'Assessment',
                'layout_status': {'ok': 'pass', 'issues': 'issues found'},
                'gaps': 'Known gaps', 'conflicts': 'Unresolved conflicts', 'review': 'Independent review',
                'no_review': 'This version has no completed independent review.',
                'no_issues': 'None recorded.',
                'about_files': 'This file does not contain source originals, tool output or run records; for a complete audit trail export the audit bundle in BriefLoop.',
                'about_manifest': 'The embedded manifest can be compared with the workspace or audit bundle, but it is unsigned and cannot by itself prove the file was not modified.',
                'cite_aria': 'Source {n}: {title}', 'view_entry': 'View appendix entry',
                'status': 'Status'}
    return {'appendix_a': '附录 A 来源与证据', 'appendix_b': '附录 B 核查状态', 'appendix_c': '附录 C 文件说明',
            'toc': '目录',
            'released': '该版本已有正式交付；本导出文件未纳入冻结交付包', 'draft': '工作稿 · 未经独立审阅',
            'reviewing': '独立审阅进行中', 'review_failed': '最近一次独立审阅未完成',
            'review_stale': '最近一次已保存审阅不适用于当前输入 · 需重新审阅',
            'finding_status': {'open': '未结', 'addressed_pending_review': '已回应 · 待复核'},
            'gap_status': {'open': '未结', 'addressed': '已处理 · 待复核',
                           'review_needed': '需复核', 'unresolved': '未解决'},
            'reviewed': '已完成独立审阅 · 未正式交付', 'open_findings': '项未结发现',
            'release_id': '交付', 'manifest': '清单',
            'version': '版本', 'body_hash': '正文哈希', 'exported': '导出',
            'counts': '{n} 个来源 · {m} 处引用',
            'stored_at': '入库时间', 'hash_label': '内容 SHA-256', 'locator': '快照定位',
            'no_excerpts': '摘录未包含在本文件', 'backrefs': '正文引用',
            'original_title': '原标题：', 'excerpts_excluded': '摘录未包含在本文件',
            'appendix_a_note': '引用位置有原文摘录不代表该原文在语义上支持结论；网页可能已更新，以入库内容与哈希为准。',
            'checks_unavailable': '确定性检查暂不可用',
            'checks_note': '确定性检查不代表事实正确。',
            'numbers': '数字绑定核查', 'matched': '匹配', 'unmatched': '不一致', 'skipped': '未核对', 'layout': '版式检查', 'assessment': '整体评价',
            'layout_status': {'ok': '通过', 'issues': '有问题'},
            'gaps': '已知缺口', 'conflicts': '未决分歧', 'review': task_label('review'),
            'no_review': '本版本未经过独立审阅。',
            'no_issues': '无记录。',
            'about_files': '本文件不含来源原件、工具输出或运行记录；完整核对请在 BriefLoop 中导出审计包。',
            'about_manifest': '内嵌清单可与工作区或审计包比对，但未签名，不能单独证明文件未被修改。',
            'cite_aria': '来源 {n}：{title}', 'view_entry': '查看附录条目',
            'status': '状态'}


def _esc(value):
    return html.escape(str(value), quote=True)


def _plain(node):
    return str(node.get('text', '')) + ''.join(_plain(c) for c in node.get('content', []))


def _local_time(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        return str(value)


def _image_mime(data):
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data.lstrip()[:5].lower().startswith(b'<svg') or b'<svg' in data[:200].lower():
        return 'image/svg+xml'
    raise ValueError('图表图片格式无效')


def html_report(store, version_id, *, excerpts=True):
    from . import __version__
    from .figure_support import export_figures
    from .market_convention import (resolve_market, market_colors, semantic_delta_spans,
                                    _WHOLE_NUMBER, _ARROWS)
    from .export_labeling import brief_label, metadata_json

    brief = store.one('briefs', version_id)
    run = store.one('runs', brief['run_id'])
    requirements = json.loads(run['requirements'])
    detail = json.loads(brief['detail'])
    language = requirements.get('language')
    labels = reader_labels(language)
    t = _labels(language)
    english = report_language(language) == 'en'
    market = resolve_market(requirements)
    colors = market_colors(requirements)

    title = detail.get('title') or requirements.get('title') or ''
    document = brief_document(brief)
    if requirements.get('report_profile') == 'industry_periodic':
        document = without_duplicate_cover_heading(document, title)
    document = normalize_document(document)
    sources = {}
    for sid in store.source_ids(brief['run_id']):
        try:
            sources[sid] = store.one('sources', sid)
        except ValueError:
            pass
    citations = [c for c in detail.get('citations', []) if isinstance(c, dict)]
    figures = export_figures(store, brief)

    # Review/release status: shown on the cover and in Appendix B. A broken
    # status projection must not block reading the saved version itself.
    try:
        from .review import review_status
        status = review_status(store, version_id)
    except Exception:
        status = {'reviews': [], 'findings': [], 'conflicts': []}
    try:
        from .release import list_releases
        releases = [r for r in list_releases(store, brief['run_id'])
                    if r['version_id'] == version_id and r['status'] == 'released' and r['result']]
    except Exception:
        releases = []
    release = releases[0] if releases else None
    reviews = status.get('reviews') or []
    newest_review = reviews[0] if reviews else None
    open_findings = unresolved_findings(status.get('findings', []))
    review_applicable = None
    review_error = None
    if newest_review and newest_review['status'] == 'complete':
        from .review import validate_applicable_review
        try:
            validate_applicable_review(store, newest_review['id'], version_id)
            review_applicable = True
        except (ValueError, OSError):
            review_applicable = False
            review_error = 'input_unverified'

    label = brief_label(store, brief, language=language)

    used = []
    occurrence_count = {}
    occurrences = []  # {'n','id','section'} per citation anchor, in body order
    toc = []
    ctx = {'section': ''}

    def cite(sid):
        if sid not in used:
            used.append(sid)
        return used.index(sid) + 1

    def display_title(sid):
        for ref in citations:
            if ref.get('source_id') == sid and str(ref.get('source_title') or '').strip():
                return str(ref['source_title']).strip()
        source = sources.get(sid) or {}
        return source.get('name') or labels['unlinked']

    def domain_of(sid):
        url = (sources.get(sid) or {}).get('url') or ''
        host = urlsplit(url).hostname or ''
        return host[4:] if host.startswith('www.') else host

    def reader_locators(sid):
        out = []
        source = sources.get(sid)
        for ref in citations:
            if ref.get('source_id') != sid or not isinstance(ref.get('locator'), str):
                continue
            value = reader_locator(ref['locator'], source)
            if value and value not in out:
                out.append(value)
        return out

    def cite_anchor(sid):
        n = cite(sid)
        occurrence_count[n] = occurrence_count.get(n, 0) + 1
        k = occurrence_count[n]
        anchor_id = 'cite-%d-%d' % (n, k)
        occurrences.append({'n': n, 'id': anchor_id, 'section': ctx['section']})
        locs = reader_locators(sid)
        excerpt = ''
        if excerpts:
            for ref in citations:
                if ref.get('source_id') == sid and str(ref.get('excerpt') or '').strip():
                    excerpt = str(ref['excerpt']).strip()
                    break
        attrs = (' data-title="%s" data-domain="%s" data-locator="%s" data-excerpt="%s"'
                 % (_esc(display_title(sid)), _esc(domain_of(sid)),
                    _esc(locs[0] if locs else ''), _esc(excerpt)))
        aria = t['cite_aria'].format(n=n, title=display_title(sid))
        return ('<a class="cite" href="#ref-%d" id="%s" data-ref="%d" aria-label="%s"%s>%d</a>'
                % (n, anchor_id, n, _esc(aria), attrs, n)), '[%d]' % n

    def apply_marks(fragment, marks):
        if 'code' in marks:
            fragment = '<code>' + fragment + '</code>'
        if 'bold' in marks:
            fragment = '<strong>' + fragment + '</strong>'
        if 'italic' in marks:
            fragment = '<em>' + fragment + '</em>'
        if 'underline' in marks:
            fragment = '<u>' + fragment + '</u>'
        if 'strike' in marks:
            fragment = '<s>' + fragment + '</s>'
        if marks.get('textStyle', {}).get('color'):
            fragment = '<span style="color:%s">' % _esc(marks['textStyle']['color']) + fragment + '</span>'
        return fragment

    def inline(nodes, *, context='', market=True):
        parts = []  # ('cite', html) or ('html', html)
        plain = ''
        text_spans = []  # (node, start, end) ranges within plain
        for node in nodes:
            kind = node['type']
            if kind == 'hardBreak':
                parts.append(('break', '<br>'))
                plain += '\n'
                continue
            if kind == 'citation':
                anchor, placeholder = cite_anchor(node['attrs']['sourceId'])
                parts.append(('cite', anchor))
                plain += placeholder
                continue
            if kind != 'text':
                raise ValueError('段落中包含不支持的内容')
            marks = {m['type']: m.get('attrs', {}) for m in node.get('marks', [])}
            href = marks.get('link', {}).get('href')
            trailing = None
            if href and href.startswith('#source-'):
                anchor, placeholder = cite_anchor(href[8:])
                if not citation_label_text(node['text']):
                    parts.append(('cite', anchor))
                    plain += placeholder
                    continue
                # Authored link text stays in full; the citation follows it.
                trailing = (anchor, placeholder)
                href = None
            start = len(plain)
            plain += node['text']
            text_spans.append((node, start, len(plain), href))
            if trailing:
                parts.append(('html', '\x00%d' % (len(text_spans) - 1)))
                parts.append(('cite', trailing[0]))
                plain += trailing[1]
                continue
            parts.append(('html', '\x00%d' % (len(text_spans) - 1)))
        spans = semantic_delta_spans(plain, context=context) if market else []

        def render_text(node, base, href, hits):
            text = node['text']
            marks = {m['type']: m.get('attrs', {}) for m in node.get('marks', [])}
            cuts = sorted({0, len(text), *(p for a, b, _ in hits for p in (a, b))})
            out = []
            for a, b in zip(cuts, cuts[1:]):
                fragment = _esc(text[a:b])
                fragment = apply_marks(fragment, marks)
                if href:
                    fragment = '<a href="%s">%s</a>' % (_esc(href), fragment)
                direction = next((d for left, right, d in hits if left <= a and b <= right), None)
                if direction:
                    fragment = ('<span data-direction="%s" style="color:#%s">%s</span>'
                                % (direction, colors[direction], fragment))
                out.append(fragment)
            return ''.join(out)

        rendered = []
        for kind, part in parts:
            if kind in ('cite', 'break'):
                rendered.append((kind, part))
                continue
            index = int(part[1:])
            node, start, end, href = text_spans[index]
            marks = {m['type']: m.get('attrs', {}) for m in node.get('marks', [])}
            protected = 'code' in marks or bool(marks.get('textStyle', {}).get('color'))
            hits = [] if protected else [(max(a, start) - start, min(b, end) - start, d)
                                         for a, b, d in spans if a < end and b > start]
            rendered.append(('html', render_text(node, start, href, hits)))
        # Adjacent citation markers merge into one bracketed superscript group.
        out = []
        i = 0
        while i < len(rendered):
            if rendered[i][0] == 'cite':
                group = []
                while i < len(rendered) and rendered[i][0] == 'cite':
                    group.append(rendered[i][1])
                    i += 1
                punct = '<span class="cite-punct">%s</span>'
                out.append('<sup class="cite-group">' + punct % '['
                           + (punct % ', ').join(group) + punct % ']' + '</sup>')
            else:
                out.append(rendered[i][1])
                i += 1
        return ''.join(out)

    def align_attr(attrs):
        return ' style="text-align:%s"' % _esc(attrs['textAlign']) if attrs.get('textAlign') else ''

    def para(tag, node, *, context='', attrs=None):
        attrs = attrs if attrs is not None else node.get('attrs', {})
        children = node.get('content', [])
        style = 'text-align:%s' % attrs['textAlign'] if attrs.get('textAlign') else ''
        if context and not style:
            # Citations render as '[N]' text in Word; any bracket placeholder
            # keeps whole-number cell detection aligned with the docx pass.
            text = ''.join(c['text'] if c['type'] == 'text' else
                           ('[x]' if c['type'] == 'citation' else '') for c in children)
            spans = semantic_delta_spans(text, context=context)
            if spans and (_WHOLE_NUMBER.fullmatch(text) or _ARROWS.fullmatch(text.strip())):
                style = 'text-align:right'
        bid = ' data-block-id="%s"' % _esc(attrs['blockId']) if attrs.get('blockId') else ''
        return '<%s%s%s>%s</%s>' % (tag, ' style="%s"' % style if style else '', bid,
                                   inline(children, context=context), tag)

    def figure_html(attrs):
        fid = attrs['src'].split(':', 1)[1]
        if fid not in figures:
            raise ValueError('图表资源未登记到此版本：' + fid)
        figure = dict(figures[fid])
        if attrs.get('caption') is not None:
            figure['caption'] = attrs['caption']
        data = figure.get('image_bytes')
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise ValueError('图表 ' + fid + ' 缺少已授权的图片数据')
        uri = 'data:%s;base64,%s' % (_image_mime(bytes(data)), base64.b64encode(bytes(data)).decode())
        bid = ' data-block-id="%s"' % _esc(attrs['blockId']) if attrs.get('blockId') else ''
        captions = []
        for value in (figure.get('title') or attrs.get('alt'), figure.get('caption')):
            if value and str(value).strip() not in captions:
                captions.append(str(value).strip())
        srcs = figure.get('source_labels') or []
        if isinstance(srcs, str):
            srcs = [srcs]
        srcs = [str(v) for v in srcs if v]
        if srcs:
            captions.append(labels['figure_source'] + labels['separator'].join(srcs))
        caption = ('<figcaption>' + '<br>'.join(_esc(c) for c in captions) + '</figcaption>') if captions else ''
        return ('<figure%s><img src="%s" alt="%s">%s</figure>'
                % (bid, uri, _esc(attrs.get('alt') or figure.get('title') or ''), caption))

    def table_html(node):
        rows = node['content']
        nr, nc, cells = table_layout(node)
        merged = any(rs > 1 or cs > 1 for _, _, rs, cs, _ in cells)
        header_text = {}
        if not merged:
            for r, c, rs, cs, cell in cells:
                if r == 0:
                    header_text[c] = _plain(cell)
        has_head = bool(rows) and all(cell.get('type') == 'tableHeader' for cell in rows[0].get('content', []))
        per_row = {}
        for r, c, rs, cs, cell in cells:
            per_row.setdefault(r, []).append((c, rs, cs, cell))
        body = []
        for r in range(nr):
            line = ['<tr>']
            for c, rs, cs, cell in sorted(per_row.get(r, [])):
                ca = cell.get('attrs', {})
                tag = 'th' if cell['type'] == 'tableHeader' else 'td'
                attr = ''
                if rs > 1:
                    attr += ' rowspan="%d"' % rs
                if cs > 1:
                    attr += ' colspan="%d"' % cs
                styles = []
                if ca.get('backgroundColor'):
                    styles.append('background-color:%s' % ca['backgroundColor'])
                if ca.get('textAlign'):
                    styles.append('text-align:%s' % ca['textAlign'])
                widths = ca.get('colwidth')
                if widths and all(widths):
                    styles.append('width:%dpx' % sum(widths))
                if ca.get('blockId'):
                    attr += ' data-block-id="%s"' % _esc(ca['blockId'])
                if styles:
                    attr += ' style="%s"' % ';'.join(styles)
                context = header_text.get(c, '') if not merged and r > 0 else ''
                inner = ''.join(block(child, context=context) for child in cell.get('content', []))
                line.append('<%s%s>%s</%s>' % (tag, attr, inner, tag))
            line.append('</tr>')
            body.append(''.join(line))
        head_html = '<thead>' + body[0] + '</thead>' if has_head else ''
        rest = body[1:] if has_head else body
        return ('<div class="table-wrap"><table>%s<tbody>%s</tbody></table></div>'
                % (head_html, ''.join(rest)))

    def block(node, *, depth=0, context=''):
        kind = node['type']
        attrs = node.get('attrs', {})
        children = node.get('content', [])
        if kind == 'paragraph':
            return para('p', node, context=context)
        if kind == 'heading':
            level = min(6, max(1, attrs.get('level', 2)))
            bid = attrs.get('blockId', '')
            ident = ' id="%s"' % _esc(bid) if bid else ''
            text = _plain(node)
            if level in (2, 3):
                toc.append({'level': level, 'text': text.strip(), 'id': bid})
            if level == 2:
                ctx['section'] = text.strip()
            return '<h%d%s%s%s>%s</h%d>' % (level, ident, align_attr(attrs),
                                          ' data-block-id="%s"' % _esc(bid) if bid else '',
                                          inline(children), level)
        if kind == 'codeBlock':
            bid = ' data-block-id="%s"' % _esc(attrs['blockId']) if attrs.get('blockId') else ''
            return '<pre%s><code>%s</code></pre>' % (bid, _esc(''.join(_plain(c) for c in children)))
        if kind in ('bulletList', 'orderedList'):
            tag = 'ul' if kind == 'bulletList' else 'ol'
            start = ' start="%d"' % attrs['start'] if kind == 'orderedList' and attrs.get('start', 1) != 1 else ''
            return '<%s%s>%s</%s>' % (tag, start, ''.join(block(c, depth=depth + 1) for c in children), tag)
        if kind == 'listItem':
            return '<li>%s</li>' % ''.join(block(c, depth=depth) for c in children)
        if kind == 'blockquote':
            return '<blockquote>%s</blockquote>' % ''.join(block(c, depth=depth + 1) for c in children)
        if kind == 'horizontalRule':
            return '<hr>'
        if kind == 'image':
            return figure_html(attrs)
        if kind == 'table':
            return table_html(node)
        raise ValueError('不支持的导出内容：' + kind)

    blocks, indexed = reader_source_blocks(document, sources)
    body_html = ''.join(block(node) for node in blocks)
    for sid in indexed:
        if sid not in used:
            used.append(sid)

    # ----- cover -----
    if release:
        result = release['result'] if isinstance(release['result'], dict) else json.loads(release['result'])
        banner_cls = 'released'
        banner_text = (t['released'] + ' · ' + t['release_id'] + ' ' + str(release['id'])
                       + ' · ' + t['manifest'] + ' ' + str(result.get('manifest_hash', ''))[:12] + '…')
    elif newest_review is None:
        banner_cls, banner_text = 'draft', t['draft']
    elif newest_review['status'] in ('queued', 'running'):
        banner_cls, banner_text = 'progress', t['reviewing']
    elif newest_review['status'] == 'complete' and review_applicable:
        banner_cls = 'reviewed'
        banner_text = t['reviewed'] + ' · %d %s' % (len(open_findings), t['open_findings'])
    elif newest_review['status'] == 'complete':
        banner_cls, banner_text = 'warn', t['review_stale']
    else:
        banner_cls, banner_text = 'warn', t['review_failed']
    if open_findings and banner_cls != 'reviewed':
        banner_text += ' · %d %s' % (len(open_findings), t['open_findings'])

    cover_fields = ' · '.join(_esc(requirements[k]) for k in ('period', 'organization', 'industry', 'report_date')
                             if str(requirements.get(k) or '').strip())
    exported = datetime.now().astimezone()
    exported_at = exported.isoformat(timespec='seconds')
    exported_display = exported.strftime('%Y-%m-%d %H:%M')
    marker_total = sum(occurrence_count.values())
    meta_bits = [t['version'] + ' ' + _esc(version_id),
                 t['body_hash'] + ' <span class="hash" title="%s">%s…</span>' % (_esc(brief['hash']), _esc(brief['hash'][:12])),
                 t['exported'] + ' ' + _esc(exported_display),
                 'BriefLoop ' + _esc(__version__),
                 _esc(t['counts'].format(n=len(used), m=marker_total))]
    cover = ('<header class="report-cover"><h1>%s</h1>%s'
             '<p class="status-banner %s">%s</p><p class="cover-meta">%s</p></header>'
             % (_esc(title), '<p class="cover-fields">' + cover_fields + '</p>' if cover_fields else '',
                banner_cls, _esc(banner_text), ' · '.join(meta_bits)))

    # ----- appendix A -----
    appendix_a = ['<section id="appendix-sources"><h2>%s</h2>' % _esc(t['appendix_a'])]
    if used:
        appendix_a.append('<p class="appendix-note">%s</p><ol class="ref-list">' % _esc(t['appendix_a_note']))
        for n, sid in enumerate(used, 1):
            source = sources.get(sid) or {}
            name = source.get('name') or ''
            shown = display_title(sid)
            url = source.get('url') or ''
            domain = domain_of(sid)
            title_html = _esc(shown)
            if url.startswith(('https://', 'http://')):
                title_html = '<a class="ref-link" href="%s">%s</a>' % (_esc(url), title_html)
            row = ['<li id="ref-%d"><div class="ref-head"><span class="ref-title">%s</span>' % (n, title_html)]
            if domain:
                row.append('<span class="ref-domain">%s</span>' % _esc(domain))
            row.append('</div>')
            if name and shown != name:
                row.append('<p class="ref-secondary">%s%s</p>' % (_esc(t['original_title']), _esc(name)))
            meta = [t['stored_at'] + ' ' + _esc(_local_time(source.get('created', '')))] if source else []
            if source.get('hash'):
                meta.append(t['hash_label'] + ' <code class="hash" title="%s">%s…</code>'
                            % (_esc(source['hash']), _esc(source['hash'][:12])))
            if source.get('status') and source['status'] != 'ready':
                meta.append(t['status'] + ' ' + _esc(source['status']))
            if meta:
                row.append('<p class="ref-meta">' + ' · '.join(meta) + '</p>')
            pairs = []
            for ref in citations:
                if ref.get('source_id') != sid:
                    continue
                pair = (str(ref.get('locator') or '').strip(), str(ref.get('excerpt') or '').strip())
                if pair != ('', '') and pair not in pairs:
                    pairs.append(pair)
            for locator, excerpt in pairs:
                row.append('<div class="ref-loc">')
                if locator:
                    row.append('<span class="ref-loc-label">%s</span> <code class="ref-locator">%s</code>'
                               % (_esc(t['locator']), _esc(locator)))
                if excerpts:
                    if excerpt:
                        row.append('<blockquote class="excerpt">%s</blockquote>' % _esc(excerpt))
                else:
                    row.append('<p class="ref-noexcerpt">%s</p>' % _esc(t['no_excerpts']))
                row.append('</div>')
            backs = [o for o in occurrences if o['n'] == n]
            if backs:
                links = ' '.join('<a class="backref" href="#%s">↩%s</a>'
                                 % (o['id'], _esc((o['section'] or '')[:24] or '¶'))
                                 for o in backs)
                row.append('<p class="ref-backs">%s %s</p>' % (_esc(t['backrefs']), links))
            row.append('</li>')
            appendix_a.append(''.join(row))
        appendix_a.append('</ol>')
    appendix_a.append('</section>')

    # ----- appendix B -----
    appendix_b = ['<section id="appendix-checks"><h2>%s</h2>' % _esc(t['appendix_b'])]
    try:
        from .delivery_checks import brief_checks
        checks = brief_checks(store, version_id)
        numbers = checks.get('numbers') or {}
        layout_status = (checks.get('layout') or {}).get('status', '')
        bits = ['%s %s/%s · %s %s · %s %s · %s %s' % (
                    t['numbers'], numbers.get('checked', 0), numbers.get('total', 0),
                    t['matched'], numbers.get('matched', 0),
                    t['unmatched'], len(numbers.get('unmatched') or []),
                    t['skipped'], len(numbers.get('skipped') or [])),
                '%s：%s' % (t['layout'], _esc(t['layout_status'].get(layout_status, layout_status)))]
        if checks.get('assessment_overall'):
            bits.append('%s：%s' % (t['assessment'], _esc(checks['assessment_overall'])))
        appendix_b.append('<p class="check-line">' + ' · '.join(bits) + '</p>')
        unresolved_numbers = (numbers.get('unmatched') or []) + (numbers.get('skipped') or [])
        if unresolved_numbers:
            appendix_b.append('<ul>' + ''.join('<li>%s</li>' % _esc(' · '.join(
                str(row[k]) for k in ('label', 'expected', 'reason') if row.get(k) not in (None, '')))
                for row in unresolved_numbers) + '</ul>')
    except (ValueError, OSError):
        appendix_b.append('<p class="check-line">%s</p>' % _esc(t['checks_unavailable']))
    appendix_b.append('<p class="appendix-note">%s</p>' % _esc(t['checks_note']))

    gap_records = unresolved_gap_records(detail)
    items = ''.join('<li><strong>%s</strong>%s%s · %s</li>'
                        % (_esc(g.get('impact', '')),
                           (' · ' + _esc(g['related'])) if g.get('related') else '',
                           (' · ' + _esc(g['action'])) if g.get('action') else '',
                           _esc(t['gap_status'].get(g.get('status', 'open'), g.get('status', 'open'))))
                        for g in gap_records)
    appendix_b.append('<h3>%s</h3>%s' % (_esc(t['gaps']), '<ul>%s</ul>' % items if items else
                                        '<p class="muted">%s</p>' % _esc(t['no_issues'])))

    open_conflicts = [c for c in status.get('conflicts', []) if c.get('status') != 'resolved']
    if open_conflicts:
        rows = ''.join('<li>%s</li>' % _esc(str((c.get('data') or {}).get('description') or c.get('id'))[:120])
                       for c in open_conflicts)
        appendix_b.append('<h3>%s（%d）</h3><ul>%s</ul>' % (_esc(t['conflicts']), len(open_conflicts), rows))
    else:
        appendix_b.append('<h3>%s</h3><p class="muted">%s</p>' % (_esc(t['conflicts']), _esc(t['no_issues'])))

    appendix_b.append('<h3>%s</h3>' % _esc(t['review']))
    if review_applicable:
        review_text = t['reviewed']
    elif newest_review and newest_review['status'] == 'complete':
        review_text = t['review_stale']
    elif newest_review and newest_review['status'] in ('queued', 'running'):
        review_text = t['reviewing']
    elif newest_review:
        review_text = t['review_failed']
    else:
        review_text = t['no_review']
    appendix_b.append('<p class="check-line">%s</p>' % _esc(review_text))
    if open_findings:
        rows = ''.join('<li><span class="sev sev-%s">%s</span> %s · %s</li>'
                           % (_esc((f.get('data') or {}).get('severity', '')),
                              _esc((f.get('data') or {}).get('severity', '')),
                              _esc((f.get('data') or {}).get('description')
                                   or (f.get('data') or {}).get('kind') or ''),
                              _esc(t['finding_status'].get(f.get('status'), f.get('status', 'open'))))
                           for f in open_findings)
        appendix_b.append('<ul>%s</ul>' % rows)
    elif review_applicable:
        appendix_b.append('<p class="muted">%s</p>' % _esc(t['no_issues']))
    appendix_b.append('</section>')

    # ----- appendix C -----
    appendix_c = ['<section id="appendix-about"><h2>%s</h2>' % _esc(t['appendix_c'])]
    if label:
        appendix_c.append('<p class="ai-export-notice">%s</p>' % _esc(label['notice']))
    appendix_c.append('<p>%s</p><p>%s</p></section>' % (_esc(t['about_files']), _esc(t['about_manifest'])))

    # ----- TOC / manifest / CSP -----
    toc_items = ''.join('<li class="toc-l%d"><a href="#%s">%s</a></li>' % (e['level'], _esc(e['id']), _esc(e['text']))
                        for e in toc if e['id'])
    toc_items += ''.join('<li class="toc-l2 toc-appendix"><a href="#%s">%s</a></li>' % (anchor, _esc(text))
                         for anchor, text in (('appendix-sources', t['appendix_a']),
                                              ('appendix-checks', t['appendix_b']),
                                              ('appendix-about', t['appendix_c'])))
    nav = ('<nav class="toc"><details id="toc-details" open><summary>%s</summary><ul class="toc-list">%s</ul></details></nav>'
           % (_esc(t['toc']), toc_items))

    manifest = {'schema': 1, 'version_id': version_id, 'run_id': brief['run_id'],
                'brief_hash': brief['hash'], 'exported_at': exported_at,
                'briefloop_version': __version__, 'market': market,
                'release': ({'id': release['id'], 'manifest_hash': (release['result'] if isinstance(release['result'], dict) else json.loads(release['result'])).get('manifest_hash')}
                            if release else None),
                'review': ({'id': newest_review['id'], 'status': newest_review['status'],
                            'applicable': review_applicable, 'applicability_error': review_error}
                           if newest_review else None),
                'sources': [{'n': n, 'id': sid, 'url': (sources.get(sid) or {}).get('url'),
                             'sha256': (sources.get(sid) or {}).get('hash'),
                             'created': (sources.get(sid) or {}).get('created')}
                            for n, sid in enumerate(used, 1)]}
    manifest_json = json.dumps(manifest, ensure_ascii=False).replace('</', '<\\/')

    tokens = resources.files('briefloop').joinpath('static/tokens.css').read_text(encoding='utf-8')
    css = tokens + '\n' + resources.files('briefloop').joinpath('html_export_assets/report.css').read_text(encoding='utf-8')
    js = resources.files('briefloop').joinpath('html_export_assets/report.js').read_text(encoding='utf-8')
    script_text = '\n' + js + '\n'
    script_hash = base64.b64encode(hashlib.sha256(script_text.encode('utf-8')).digest()).decode()
    csp = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
           "script-src 'sha256-%s'; base-uri 'none'; form-action 'none'" % script_hash)

    metas = ['<meta charset="utf-8">', '<meta name="viewport" content="width=device-width">',
             '<meta http-equiv="Content-Security-Policy" content="%s">' % _esc(csp),
             '<meta name="BriefLoopMarketConvention" content="%s">' % _esc(market)]
    if label:
        metas.append('<meta name="AIGC" content="%s">' % _esc(metadata_json(label)))

    return ('<!doctype html>\n<html lang="%s" data-market="%s">\n<head>\n%s\n<title>%s</title>\n'
            '<style>\n%s\n</style>\n</head>\n<body data-view-entry="%s">\n'
            '<div class="layout">\n%s\n<main>\n%s\n<article class="report-body">\n%s\n</article>\n%s%s\n</main>\n</div>\n'
            '<script type="application/json" id="briefloop-manifest">%s</script>\n'
            '<script>%s</script>\n</body>\n</html>\n'
            % ('en' if english else 'zh-CN', _esc(market), '\n'.join(metas), _esc(title), css,
               _esc(t['view_entry']), nav, cover, body_html,
               ''.join(appendix_a), ''.join(appendix_b) + ''.join(appendix_c),
               manifest_json, script_text))
