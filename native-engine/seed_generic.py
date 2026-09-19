"""Report-agnostic seeded defects for evaluating the independent Reviewer.

Plants problems into a COPY of any workspace's frozen version, chosen by a
random seed, so an evaluation is not a fixed list of sentences an engine can
be tuned to. Every planted value is new: absent from the report and from the
run's sources before seeding. A review therefore detects a defect only when it
names that value (or the changed wording), which is how it is scored.

Categories: a changed number, a changed date, a changed percentage, a swapped
magnitude (万/亿), a dropped hedge (预计… → 已…), a swapped period (同比/环比,
上半年/第二季度) and a fabricated sentence. The ground truth is written OUTSIDE
the workspace; nothing in the packet reveals it. Never run this on a real
workspace.
"""
import hashlib
import json
import random
import re
import sqlite3
from pathlib import Path

UNIT = r'(?:万|亿|倍|美元|元|股|吨|GW|MW|个百分点)'
NUMBER = re.compile(r'(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?=\s*' + UNIT + ')')
PERCENT = re.compile(r'(?<![\d.,])(\d+)(\.\d+)?(?=%)')
SCALE = re.compile(r'(?<![\d.,])(\d{1,3}(?:,\d{3})*|\d+)(\.\d+)?(万|亿)(?=美元|元|股|吨)')
DATE = re.compile(r'(?<!\d)(\d{1,2})月(\d{1,2})日')
# Only a hedge followed by a time ("预计2026年9月投产"), so the rewrite reads as
# a plain statement that it already happened.
HEDGE = re.compile(r'(预计|计划|拟|有望)于?((?:\d{4}年)?(?:\d{1,2}月)?(?:[上中下]旬|底|初|末)?|年内|年底前?|今年|下半年)'
                   r'(投产|完成|上线|发布|落地|生效|开工|交付|签署|实现|启动)')
PERIODS = [('同比', '环比'), ('环比', '同比'), ('上半年', '第二季度'), ('第二季度', '上半年')]
# Business claims with no source behind them; each carries a distinctive phrase.
FABRICATIONS = [
    ('公司同时宣布与一家欧洲客户签署为期五年的独家供货协议。', '独家供货协议'),
    ('管理层表示明年资本开支将翻倍至约三亿美元。', '资本开支将翻倍'),
    ('该项目已获得当地政府一次性专项补贴约一千二百万美元。', '一次性专项补贴'),
    ('公司已完成对一家同业企业的全资收购。', '全资收购'),
]
# Markdown that is not prose: citation markers, links, figure references.
PROTECTED = re.compile(r'\[@[^\]]*\]|\]\([^)]*\)|!\[[^\]]*\]\([^)]*\)|briefloop-figure:\S+')


def _protected_spans(text):
    return [m.span() for m in PROTECTED.finditer(text)]


def _free(span, protected):
    return all(span[1] <= a or span[0] >= b for a, b in protected)


def _normalize(text):
    return re.sub(r'[\s,，]', '', text)


class _Report:
    def __init__(self, markdown, document, corpus):
        self.markdown = markdown
        self.document = document  # JSON text or None
        self.corpus = _normalize(corpus + markdown)

    def unique(self, text):
        # Replaceable only where it occurs once in both representations.
        if self.markdown.count(text) != 1:
            return False
        return self.document is None or self.document.count(json.dumps(text, ensure_ascii=False)[1:-1]) == 1

    def fresh(self, needle):
        return _normalize(needle) not in self.corpus

    def replace(self, old, new):
        self.markdown = self.markdown.replace(old, new, 1)
        if self.document is not None:
            self.document = self.document.replace(json.dumps(old, ensure_ascii=False)[1:-1],
                                                  json.dumps(new, ensure_ascii=False)[1:-1], 1)
        self.corpus += _normalize(new)


def _context(text, start, end, width=6):
    """A unique window around [start, end) inside one line of prose."""
    for pad in range(width, 40, 2):
        a = max(text.rfind('\n', 0, start) + 1, start - pad)
        b = min(len(text) if text.find('\n', end) < 0 else text.find('\n', end), end + pad)
        window = text[a:b]
        if text.count(window) == 1:
            return a, window
    return None


def _changed_digits(digits, rng):
    """Change one non-leading significant digit; keep length and grouping."""
    positions = [i for i, ch in enumerate(digits) if ch.isdigit()][1:] or [0]
    for _ in range(10):
        i = rng.choice(positions)
        new = str((int(digits[i]) + rng.randint(2, 7)) % 10)
        if new != digits[i]:
            return digits[:i] + new + digits[i + 1:]
    return None


def _candidates(report, category, rng):
    text = report.markdown
    protected = _protected_spans(text)
    found = []
    if category in ('number_changed', 'percent_changed'):
        pattern = NUMBER if category == 'number_changed' else PERCENT
        for m in pattern.finditer(text):
            whole = m.group(0)
            if len(_normalize(whole).replace('.', '')) < 2 or not _free(m.span(), protected):
                continue
            new_int = _changed_digits(m.group(1) + (m.group(2) or ''), rng)
            if new_int:
                found.append((m.start(), m.end(), new_int))
    elif category == 'scale_swapped':
        for m in SCALE.finditer(text):
            if _free(m.span(), protected):
                found.append((m.start(3), m.end(3), '亿' if m.group(3) == '万' else '万'))
    elif category == 'date_changed':
        for m in DATE.finditer(text):
            day = int(m.group(2))
            if not _free(m.span(), protected) or not 1 <= day <= 28:
                continue
            shifted = day + rng.choice([-1, 1]) * rng.randint(2, 9)
            if 1 <= shifted <= 28:
                found.append((m.start(), m.end(), f'{int(m.group(1))}月{shifted}日'))
    elif category == 'hedge_dropped':
        for m in HEDGE.finditer(text):
            if m.group(2) and _free(m.span(), protected):
                found.append((m.start(), m.end(), '已于' + m.group(2) + m.group(3)))
    elif category == 'period_swapped':
        for old, new in PERIODS:
            for m in re.finditer(old, text):
                if _free(m.span(), protected):
                    found.append((m.start(), m.end(), new))
    rng.shuffle(found)
    return found


def _plant(report, category, rng):
    if category == 'fabricated_claim':
        text = report.markdown
        ends = [m.end() for m in re.finditer(r'。(?=\[@src_)', text)]
        rng.shuffle(ends)
        for sentence, needle in rng.sample(FABRICATIONS, len(FABRICATIONS)):
            if not report.fresh(needle):
                continue
            for end in ends:
                line_start = text.rfind('\n', 0, end) + 1
                old = None
                for pad in range(8, 40, 4):
                    window = text[max(line_start, end - pad):end]
                    if '[@' not in window and ']' not in window and report.unique(window):
                        old = window
                        break
                if old is None:
                    continue
                new = old + sentence
                if report.unique(old):
                    report.replace(old, new)
                    return {'category': category, 'original': old, 'planted': new, 'needles': [needle]}
        return None
    for start, end, replacement in _candidates(report, category, rng):
        text = report.markdown
        window = _context(text, start, end)
        if not window:
            continue
        w_start, old = window
        new = old[:start - w_start] + replacement + old[end - w_start:]
        if category in ('number_changed', 'percent_changed', 'scale_swapped'):
            # Name the value with its unit so it cannot match an unrelated figure.
            if category == 'scale_swapped':
                digits = re.search(r'[\d,.]+$', text[:start])
                needle = (digits.group(0) if digits else '') + replacement
            else:
                unit = re.match(r'\s*(%|' + UNIT + ')', text[end:end + 6])
                needle = replacement + (unit.group(1) if unit else '')
        elif category in ('date_changed', 'hedge_dropped'):
            needle = replacement
        else:
            # A swapped period word is common text; pair it with what follows
            # or precedes it so only a quote of the changed phrase matches.
            after = _normalize(text[end:end + 12])[:4]
            before = _normalize(text[max(0, start - 12):start])[-3:]
            needle = replacement + after
        needles = [needle] if category != 'period_swapped' else [needle, before + replacement]
        if (any(len(_normalize(n)) < 3 or not report.fresh(n) for n in needles)
                or not report.unique(old)):
            continue
        report.replace(old, new)
        return {'category': category, 'original': old, 'planted': new, 'needles': needles}
    return None


CATEGORIES = ('number_changed', 'percent_changed', 'scale_swapped', 'date_changed',
              'hedge_dropped', 'period_swapped', 'fabricated_claim')


def seed(workspace, version_id, rng_seed, *, categories=CATEGORIES):
    """Plant one defect per category where the report allows; return the truth."""
    from briefloop.document_model import document_hash
    from briefloop.store import Store, content_hash
    workspace = Path(workspace)
    store = Store(workspace)
    brief = store.one('briefs', version_id)
    corpus = []
    for sid in store.source_ids(brief['run_id']):
        try:
            corpus.append(store.source_text(sid))
        except (ValueError, OSError):
            pass
    report = _Report(brief['markdown'], brief['editor_document'], '\n'.join(corpus))
    rng = random.Random(rng_seed)
    planted = []
    for category in categories:
        result = _plant(report, category, rng)
        if result:
            planted.append(result)
    document = json.loads(report.document) if report.document is not None else None
    digest = document_hash(document) if document is not None else content_hash(report.markdown)
    connection = sqlite3.connect(workspace / 'briefloop.db')
    with connection:
        connection.execute('UPDATE briefs SET markdown=?,editor_document=?,hash=? WHERE id=?',
                           (report.markdown, report.document, digest, version_id))
    connection.close()
    return {'version_id': version_id, 'rng_seed': rng_seed, 'planted': planted}


def review_texts(result):
    findings = result.get('findings', []) + (result.get('assessment') or {}).get('findings', [])
    texts = [' '.join(str(f.get(k, '')) for k in ('description', 'evidence', 'report_quote')) for f in findings]
    texts += [u.get('description', '') for u in result.get('unchecked_items', [])]
    texts += [c.get('reason', '') for c in result.get('response_checks', []) if c.get('decision') == 'unresolved']
    return texts


def score(result, truth):
    texts = [_normalize(t) for t in review_texts(result)]
    return {p['category']: any(_normalize(n) in t for n in p['needles'] for t in texts) for p in truth['planted']}
