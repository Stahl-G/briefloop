"""Cache meaning-preserving paraphrases of each slice's judgment sentences (#757).

The judgments_paraphrased perturbation must not change what a paragraph claims,
only how it is worded, so a good evaluator gives it the same score as the
original. Paraphrases are generated once, checked mechanically (same numbers,
similar length, actually different), cached next to the slices and reused by
every run, so all arms see the same text. A person still checks a sample: the
mechanical checks cannot prove the meaning is unchanged.

  python paraphrase_judgments.py --slices DIR [--model opencode-go/deepseek-v4.1-flash]
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import seed_value  # noqa: E402

PROMPT = ('下面是一份中文研究报告里的若干判断句。请逐句改写：意思、事实、数字、主体、时间、限定条件和判断方向都必须完全不变，'
          '不增加也不删除任何信息，不改变确定程度（例如「可能」不能变成「将」），长度与原句相近，只换一种说法和句式。'
          '只输出 JSON：{"paraphrases": [与输入等长、顺序一致的字符串数组]}。\n输入：')


def numbers(text):
    return sorted(re.findall(r'\d+(?:[.,]\d+)*', text))


def check(original, new):
    if not isinstance(new, str) or not new.strip():
        return 'empty'
    if new.strip() == original:
        return 'unchanged'
    if numbers(original) != numbers(new):
        return 'numbers_differ'
    if not 0.7 <= len(new) / max(len(original), 1) <= 1.3:
        return 'length_changed'
    return None


def request(model, content):
    """One JSON-object chat call through the native engine's saved provider; returns (model, answer, usage)."""
    provider = json.loads(Path('~/.config/briefloop/native-engine/providers.json').expanduser().read_text())[model]
    body = {'model': model.split('/', 1)[1], 'response_format': {'type': 'json_object'},
            'messages': [{'role': 'user', 'content': content}]}
    http = urllib.request.Request(provider['base_url'].rstrip('/') + '/chat/completions', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + provider['api_key'],
                                              'User-Agent': 'briefloop-eval/1', 'x-opencode-session': 'briefloop-eval-' + uuid.uuid4().hex})
    with urllib.request.urlopen(http, timeout=300) as response:
        data = json.loads(response.read())
    return data.get('model'), json.loads(data['choices'][0]['message']['content']), data.get('usage')


def call(model, sentences):
    model_id, answer, usage = request(model, PROMPT + json.dumps(sentences, ensure_ascii=False))
    return model_id, answer.get('paraphrases', []), usage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--slices', required=True)
    parser.add_argument('--model', default='opencode-go/deepseek-v4.1-flash')
    args = parser.parse_args()
    root = Path(args.slices).expanduser()
    out = root / 'paraphrases'
    out.mkdir(exist_ok=True)
    for entry in json.loads((root / 'manifest.json').read_text(encoding='utf-8'))['slices']:
        target = out / f"{entry['name']}.json"
        markdown = sqlite3.connect(root / entry['name'] / 'briefloop.db').execute(
            'SELECT markdown FROM briefs WHERE id=?', (entry['version'],)).fetchone()[0]
        digest = hashlib.sha256(markdown.encode()).hexdigest()
        if target.exists() and json.loads(target.read_text()).get('labelled') and json.loads(target.read_text())['markdown_sha256'] == digest:
            continue
        # Implication sentences from the checked labels when present; else the legacy heuristic.
        labels_path = root / 'labels' / f"{entry['name']}.json"
        labels = json.loads(labels_path.read_text()) if labels_path.exists() else None
        if labels and labels['markdown_sha256'] != digest:
            raise SystemExit(f"{entry['name']}: labels are for another version of the text")
        originals = list(dict.fromkeys(seed_value._plain(s) for _, _, s in seed_value.judgments(markdown, labels)))
        if not originals:
            continue
        model, proposed, usage = call(args.model, originals)
        pairs, rejected = {}, []
        for original, new in zip(originals, proposed if len(proposed) == len(originals) else []):
            reason = check(original, new)
            if reason:
                rejected.append({'original': original, 'proposed': new, 'reason': reason})
            else:
                pairs[original] = new.strip()
        if len(proposed) != len(originals):
            rejected.append({'reason': 'count_mismatch', 'expected': len(originals), 'got': len(proposed)})
        target.write_text(json.dumps({'slice': entry['name'], 'markdown_sha256': digest, 'model': model, 'labelled': bool(labels),
                                      'pairs': pairs, 'rejected': rejected, 'usage': usage}, ensure_ascii=False, indent=1))
        print(entry['name'], 'judgments', len(originals), 'kept', len(pairs), 'rejected', len(rejected), flush=True)


if __name__ == '__main__':
    main()
