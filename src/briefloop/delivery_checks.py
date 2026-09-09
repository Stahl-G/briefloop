"""Deterministic delivery checks: no model calls, no waiting.

Covers what code can decide cheaply so the model judges only the rest:
- every [@source_id] marker in the body resolves to a real source record;
- important numbers recorded in number_bindings reappear in the body in a
  unit-consistent form (catches e.g. $13.6B written as 1360亿美元）;
- the exported file itself is complete (no escaped bold, figure markers
  registered).

Saving/revising is never blocked by these checks; they only decide whether
a version may be labelled as formally deliverable.
"""
import re

REF_RE = re.compile(r'\\?\[@(src\\?_[a-zA-Z0-9]+)\\?\]')
ESCAPED_BOLD_RE = re.compile(r'\\\*\\\*')

# unit label -> scale in base unit. Only unambiguous units are listed:
# no 兆 (1e12 vs 1e6 by region), no 百分点 (points vs percent).
_MONEY_USD = {
    '$': 1, 'dollar': 1, 'dollars': 1, 'usd': 1, '美元': 1,
    'million': 1e6, 'millions': 1e6, '百万美元': 1e6,
    'billion': 1e9, 'billions': 1e9, '亿美元': 1e8, '十亿美元': 1e9,
    '万': 1e4, '千万': 1e7, '万元': 1e4, '亿元': 1e8,
}
_CAPACITY_W = {
    'w': 1, '瓦': 1,
    'kw': 1e3, '千瓦': 1e3,
    'mw': 1e6, '兆瓦': 1e6,
    'gw': 1e9, '吉瓦': 1e9,
}
_PERCENT = {'%': 1, '％': 1, 'percent': 1}

# base-kind -> [(output label, scale, prefix_style)]
_MONEY_FORMS = [('$', 1, True), ('美元', 1, False), ('万元', 1e4, False),
                ('亿美元', 1e8, False), ('million', 1e6, False), ('billion', 1e9, False)]
_CAPACITY_FORMS = [('瓦', 1, False), ('千瓦', 1e3, False), ('兆瓦', 1e6, False),
                   ('吉瓦', 1e9, False), ('W', 1, False), ('kW', 1e3, False),
                   ('MW', 1e6, False), ('GW', 1e9, False)]


def _norm_unit(unit):
    text = (unit or '').strip()
    low = text.lower()
    if low in _MONEY_USD:
        return _MONEY_USD[low], 'money'
    if low in _CAPACITY_W:
        return _CAPACITY_W[low], 'capacity'
    if text in _PERCENT:
        return 1, 'percent'
    # Compound money units like "billion USD" / "USD millions": longest match wins.
    for word in sorted(_MONEY_USD, key=len, reverse=True):
        if len(word) > 1 and word in low:
            return _MONEY_USD[word], 'money'
    return None


def _fmt(value):
    rounded = round(value, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f'{rounded:.2f}'.rstrip('0').rstrip('.')


def _clean(value):
    return 0 < abs(value) < 1e15 and round(value, 2) == value


def _sample(forms):
    return min(forms, key=lambda s: (0 if any(c in s for c in '亿万吉瓦兆瓦千瓦%百分之') else 1, len(s)))


def equivalent_forms(value, unit):
    """All body spellings a binding value may legitimately take."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return set()
    parsed = _norm_unit(unit)
    if parsed is None:
        return set()
    scale, kind = parsed
    base = number * scale
    forms = set()
    if kind == 'percent':
        if _clean(base):
            text = _fmt(base)
            forms.update({text + '%', text + ' %', text + ' percent', '百分之' + text})
        return forms
    targets = _MONEY_FORMS if kind == 'money' else _CAPACITY_FORMS
    for label, target_scale, prefix in targets:
        converted = base / target_scale
        if not _clean(converted):
            continue
        text = _fmt(converted)
        if prefix:
            forms.add('$' + text)
        else:
            forms.add(text + label)
            if label.isascii():
                forms.add(text + ' ' + label)
    return forms


def body_refs(markdown):
    return list(dict.fromkeys(match.replace('\\', '') for match in REF_RE.findall(markdown or '')))


def check_refs(store, markdown):
    broken = []
    for sid in body_refs(markdown):
        try:
            store.one('sources', sid)
        except ValueError:
            broken.append(sid)
    return broken


def check_numbers(markdown, bindings):
    """Each binding: found / unmatched / skipped (unsupported unit or value)."""
    body = markdown or ''
    results = []
    for item in bindings or []:
        if not isinstance(item, dict):
            continue
        forms = equivalent_forms(item.get('value'), item.get('unit'))
        if not forms:
            results.append({'label': item.get('label', ''), 'checked': False,
                            'found': False, 'expected': ''})
            continue
        found = any(form and form in body for form in forms)
        results.append({'label': item.get('label', ''), 'checked': True,
                        'found': found, 'expected': _sample(forms)})
    return results


def check_export(markdown):
    import re as _re
    markers = sorted(set(_re.findall(r'briefloop-figure:([A-Za-z0-9_-]+)', markdown or '')))
    return {'escaped_bold': bool(ESCAPED_BOLD_RE.search(markdown or '')),
            'figure_markers': markers}


def brief_checks(store, version_id):
    """One honest status object per saved version; never raises on content."""
    brief = store.one('briefs', version_id)
    import json
    detail = json.loads(brief.get('detail') or '{}')
    markdown = brief['markdown']
    numbers = check_numbers(markdown, detail.get('number_bindings'))
    rows = store.rows('SELECT data FROM assessments WHERE version_id=? ORDER BY rowid DESC LIMIT 1',
                      (version_id,))
    overall = json.loads(rows[0]['data']).get('overall') if rows else None
    return {'version_id': version_id,
            'broken_refs': check_refs(store, markdown),
            'numbers': {'total': len(numbers),
                        'unmatched': [r for r in numbers if r['checked'] and not r['found']],
                        'skipped': [r['label'] for r in numbers if not r['checked']]},
            'export': check_export(markdown),
            'assessment_overall': overall}
