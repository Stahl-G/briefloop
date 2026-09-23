"""Source line search; explicit regular expressions run with a process deadline."""
import json
from pathlib import Path
import re
import subprocess
import sys

REGEX_TIMEOUT_SECONDS = 2


def _hits(pattern, sources, limit):
    matcher = re.compile(pattern, re.IGNORECASE)
    hits = []
    for sid, text in sources:
        for number, line in enumerate(text.splitlines(), 1):
            match = matcher.search(line)
            if match:
                offset = max(0, match.start() - 100)
                hits.append(f'{sid} {number} (start_char={offset}): {line[offset:offset + 300]}')
                if len(hits) >= limit:
                    return hits
    return hits


def search(pattern, sources, limit, *, regex=False):
    if not regex:
        return _hits(re.escape(pattern), sources, limit)
    # Compile in the child too: both compilation and backtracking are bounded.
    # run(timeout=...) kills and reaps the child, including on Windows; threads
    # cannot interrupt a Python re call that holds the GIL.
    try:
        result = subprocess.run(
            [sys.executable, '-I', '-X', 'utf8', str(Path(__file__).resolve())],
            input=json.dumps({'pattern': pattern, 'sources': list(sources), 'limit': limit}, ensure_ascii=False),
            capture_output=True, text=True, encoding='utf-8',
            timeout=REGEX_TIMEOUT_SECONDS, check=True,
            **({'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}),
        )
    except subprocess.TimeoutExpired:
        raise ValueError('正则搜索超时；请简化 pattern、指定 source_id，或使用默认关键词搜索（regex=false）') from None
    answer = json.loads(result.stdout)
    if 'error' in answer:
        raise ValueError(answer['error'])
    return answer['hits']


if __name__ == '__main__':
    request = json.load(sys.stdin)
    try:
        answer = {'hits': _hits(request['pattern'], request['sources'], request['limit'])}
    except re.error:
        answer = {'error': '正则 pattern 无效；请修正表达式，或使用默认关键词搜索（regex=false）'}
    print(json.dumps(answer, ensure_ascii=False))
