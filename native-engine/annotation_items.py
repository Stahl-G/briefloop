"""Sample the human-check items for benchmark v2 (#757) and score the answers.

  python annotation_items.py seed --slices DIR --out items.json   # items to write into the annotation page
  python annotation_items.py score --slices DIR --items items.json --answers answers.json

Three item kinds: a blind sentence-label check of sampled paragraphs, one
"should this chapter carry implications for this reader?" question per slice,
and a paraphrase-equivalence check. Sampling is seeded so the set is
reproducible; the answers are compared with the model labels and reported
alongside every benchmark result.
"""
import argparse
import json
import random
import sqlite3
from pathlib import Path

SEED = 757


def seed(root, per_slice=2, pairs_total=30):
    rng = random.Random(SEED)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))['slices']
    items, pair_pool = [], []
    for entry in manifest:
        name = entry['name']
        labels = json.loads((root / 'labels' / f'{name}.json').read_text())
        paragraphs = [p for p in labels['paragraphs'] if 'sentences' in p and 2 <= len(p['sentences']) <= 8]
        for p in rng.sample(paragraphs, min(per_slice, len(paragraphs))):
            items.append({'id': f"s-{name}-{p['block']}", 'kind': 'sentence', 'slice': name, 'set': entry['set'], 'block': p['block'],
                          'reader': labels['reader'], 'sentences': [s['text'] for s in p['sentences']]})
        markdown = sqlite3.connect(root / name / 'briefloop.db').execute(
            'SELECT markdown FROM briefs WHERE id=?', (entry['version'],)).fetchone()[0]
        items.append({'id': f'c-{name}', 'kind': 'chapter', 'slice': name, 'set': entry['set'], 'report': entry.get('family', ''),
                      'reader': labels['reader'], 'text': markdown[:8000]})
        cache = root / 'paraphrases' / f'{name}.json'
        if cache.exists():
            for n, (original, new) in enumerate(json.loads(cache.read_text())['pairs'].items()):
                pair_pool.append({'id': f'p-{name}-{n}', 'kind': 'paraphrase', 'slice': name, 'set': entry['set'], 'original': original, 'paraphrase': new})
    items += rng.sample(pair_pool, min(pairs_total, len(pair_pool)))
    order = {'sentence': 0, 'chapter': 0, 'paraphrase': 0}
    shuffled = items[:]
    rng.shuffle(shuffled)
    for item in shuffled:
        item['order'] = order[item['kind']]
        order[item['kind']] += 1
    return shuffled


def kappa(a, b):
    labels = sorted(set(a) | set(b))
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    expected = sum((a.count(k) / n) * (b.count(k) / n) for k in labels)
    return observed, (observed - expected) / (1 - expected) if expected < 1 else 1.0


def score(root, items, answers):
    by_id = {i['id']: i for i in items}
    model, human, per_set = [], [], {}
    for item_id, answer in answers.items():
        item = by_id.get(item_id)
        if not item or item['kind'] != 'sentence' or not all(answer.get('labels') or [None]):
            continue
        labels = json.loads((root / 'labels' / f"{item['slice']}.json").read_text())
        paragraph = next(p for p in labels['paragraphs'] if p['block'] == item['block'])
        for s, h in zip(paragraph['sentences'], answer['labels']):
            model.append(s['label'])
            human.append(h)
            per_set.setdefault(item['set'], []).append((s['label'], h))
    report = {'sentences': len(model)}
    if model:
        report['agreement'], report['kappa'] = kappa(model, human)
        report['human_implication_share'] = human.count('implication') / len(human)
        report['model_implication_share'] = model.count('implication') / len(model)
        report['by_set'] = {k: dict(zip(('agreement', 'kappa'), kappa([m for m, _ in v], [h for _, h in v]))) | {'n': len(v)} for k, v in per_set.items()}
    chapters = {i['slice']: answers[i['id']].get('value') for i in items if i['kind'] == 'chapter' and i['id'] in answers}
    report['chapters'] = chapters
    pairs = [answers[i['id']].get('value') for i in items if i['kind'] == 'paraphrase' and i['id'] in answers]
    report['paraphrases'] = {v: pairs.count(v) for v in set(pairs)}
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['seed', 'score'])
    parser.add_argument('--slices', required=True)
    parser.add_argument('--out')
    parser.add_argument('--items')
    parser.add_argument('--answers')
    args = parser.parse_args()
    root = Path(args.slices).expanduser()
    if args.command == 'seed':
        items = seed(root)
        Path(args.out).write_text(json.dumps(items, ensure_ascii=False, indent=1))
        print({k: sum(i['kind'] == k for i in items) for k in ('sentence', 'chapter', 'paraphrase')})
    else:
        items = json.loads(Path(args.items).read_text())
        answers = json.loads(Path(args.answers).read_text())
        print(json.dumps(score(root, items, answers), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
