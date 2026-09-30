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
import urllib.error
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
    providers = json.loads(Path('~/.config/briefloop/native-engine/providers.json').expanduser().read_text())
    # Other models on the same gateway reuse its saved endpoint and key.
    provider = providers.get(model) or next(v for k, v in providers.items() if k.split('/', 1)[0] == model.split('/', 1)[0])
    body = {'model': model.split('/', 1)[1], 'response_format': {'type': 'json_object'},
            'messages': [{'role': 'user', 'content': content}]}
    http = urllib.request.Request(provider['base_url'].rstrip('/') + '/chat/completions', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + provider['api_key'],
                                              'User-Agent': 'briefloop-eval/1', 'x-opencode-session': 'briefloop-eval-' + uuid.uuid4().hex})
    try:
        with urllib.request.urlopen(http, timeout=300) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'{model}: HTTP {error.code} {error.read()[:300]!r}') from None
    return data.get('model'), json.loads(data['choices'][0]['message']['content']), data.get('usage')


def call(model, sentences):
    model_id, answer, usage = request(model, PROMPT + json.dumps(sentences, ensure_ascii=False))
    return model_id, answer.get('paraphrases', []), usage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--slices', required=True)
    parser.add_argument('--model', default='opencode-go/deepseek-v4.1-flash')
    parser.add_argument('--out', '--out-dir', default='paraphrases', help='new cache output directory')
    parser.add_argument('--labels', default='labels', help='explicit frozen sentence-label directory')
    args = parser.parse_args()
    from label_sentences import RULES, frozen_source, cache_identity, matching_cache, write_cache, digest
    root = Path(args.slices).expanduser().resolve()
    out = root / args.out
    tasks = []
    for entry in json.loads((root / 'manifest.json').read_text(encoding='utf-8'))['slices']:
        markdown, requirements, reader = frozen_source(root, entry)
        target = out / f"{entry['name']}.json"
        labels_path = root / args.labels / f"{entry['name']}.json"
        if not labels_path.exists():
            raise ValueError(f'{labels_path}: frozen labels required; no heuristic fallback')
        labels = json.loads(labels_path.read_text(encoding='utf-8'))
        if (labels.get('markdown_sha256') != hashlib.sha256(markdown.encode()).hexdigest()
                or labels.get('rules') != RULES or any('error' in p for p in labels.get('paragraphs', []))):
            raise ValueError(f"{entry['name']}: incomplete/stale labels or different rules")
        originals = list(dict.fromkeys(seed_value._plain(s) for _, _, s in seed_value.judgments(markdown, labels)))
        prompt = PROMPT + json.dumps(originals, ensure_ascii=False)
        identity = cache_identity(entry, markdown, requirements, reader, 'provider-chat-paraphrase', args.model, prompt,
                                  extra={'labels_sha256': digest(labels)})
        if matching_cache(target, identity):
            continue
        if originals:
            tasks.append((entry, originals, identity, target))
    out.mkdir(parents=True, exist_ok=True)
    for entry, originals, identity, target in tasks:
        model, proposed, usage = call(args.model, originals)
        pairs, rejected = {}, []
        valid_count = isinstance(proposed, list) and len(proposed) == len(originals)
        for original, new in zip(originals, proposed if valid_count else []):
            reason = check(original, new)
            if reason:
                rejected.append({'original': original, 'proposed': new, 'reason': reason})
            else:
                pairs[original] = new.strip()
        if not valid_count:
            rejected.append({'reason': 'count_mismatch', 'expected': len(originals),
                             'got': len(proposed) if isinstance(proposed, list) else None})
        write_cache(target, {'slice': entry['name'], 'version': entry['version'],
                            'markdown_sha256': identity['markdown_sha256'],
                            'reader_contract_sha256': identity['reader_contract_sha256'],
                            'reader_sha256': identity['reader_sha256'], 'cache_identity': identity,
                            'complete': valid_count and not rejected, 'requested_model': args.model,
                            'model': model, 'labelled': True, 'rules': RULES,
                            'pairs': pairs, 'rejected': rejected, 'usage': usage})
        print(entry['name'], 'judgments', len(originals), 'kept', len(pairs), 'rejected', len(rejected), flush=True)


if __name__ == '__main__':
    main()
