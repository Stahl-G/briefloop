"""Text a figure's own script draws onto the image, for the Reviewer.

A chart's title, notes and annotations are part of what the reader sees, and a
stale footnote can contradict the chart's data and the report. Reading them
from pixels depends on the model's vision; the frozen script already holds
them as string literals. This parses the script (never runs it) and lists the
literals that read as display text, with line numbers, so the Reviewer can
compare them with the data file and the report directly.
"""
import ast
import re

CJK = re.compile(r'[㐀-鿿＀-￯]')
# Identifiers, file names, fonts, colours and format specs are not display text.
SKIP = re.compile(r'^(?:[A-Za-z_][\w.-]*|#[0-9A-Fa-f]{3,8}|%[-\w.]*|\s*)$')
FILE = re.compile(r'\.(?:ttf|otf|ttc|woff2?|png|jpe?g|svg|pdf|csv|json|xlsx|py|txt)$', re.I)
PLACEHOLDER = '{…}'


def _display(text):
    text = text.strip()
    if not text or SKIP.match(text) or FILE.search(text):
        return False
    return bool(CJK.search(text)) or (len(text) >= 4 and bool(re.search(r'[A-Za-z]', text)) and ' ' in text)


def _joined(node):
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            parts.append(PLACEHOLDER)
    return ''.join(parts)


def python_texts(source):
    tree = ast.parse(source)
    found = []
    inside_fstring = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                inside_fstring.add(id(value))
            text = _joined(node)
            if _display(text.replace(PLACEHOLDER, '')):
                found.append((node.lineno, text))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in inside_fstring and _display(node.value)):
            found.append((node.lineno, node.value))
    seen = set()
    texts = []
    for line, text in sorted(found):
        if (line, text) not in seen:
            seen.add((line, text))
            texts.append({'line': line, 'text': text})
    return texts


def figure_texts(script_name, blob):
    """{'text_source': 'script'|'none', 'texts': [...], 'note': str}"""
    if not script_name:
        return {'text_source': 'none', 'texts': [], 'note': '没有登记生成脚本，图上文字只能看图核对。'}
    if not script_name.endswith('.py'):
        return {'text_source': 'none', 'texts': [],
                'note': '生成脚本不是 Python 绘图脚本（可能是复用的现成图片），图上文字只能看图核对。'}
    try:
        texts = python_texts(blob.decode('utf-8'))
    except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
        return {'text_source': 'none', 'texts': [], 'note': f'脚本无法解析（{type(exc).__name__}），图上文字只能看图核对。'}
    note = ('取自生成脚本中的字符串，不运行脚本；{…} 是运行时填入的值。'
            '脚本里的文字不一定都画在图上，以图片为准。')
    return {'text_source': 'script', 'texts': texts, 'note': note}
