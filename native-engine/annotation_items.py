"""Sample the human-check items for benchmark v2 (#757) and score the answers.

  python annotation_items.py seed --slices DIR --out items.json   # items to write into the annotation page
  python annotation_items.py score --slices DIR --items items.json --answers answers.json
  python annotation_items.py agree --slices DIR --dirs labels-r2-a labels-r2-b ...   # annotator agreement

Three item kinds: a blind sentence-label check of sampled paragraphs, one
"should this chapter carry implications for this reader?" question per slice,
and a paraphrase-equivalence check. Sampling is seeded so the set is
reproducible; the answers are compared with the model labels and reported
alongside every benchmark result.
"""
import argparse
import json
import random
from collections import Counter
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



LABELS = ('fact', 'implication')
CHAPTER_VALUES = ('should', 'optional', 'not_needed')
PARAPHRASE_VALUES = ('same', 'changed', 'unsure')


def kappa(a, b):
    if len(a) != len(b):
        raise ValueError('Agreement requires equal-length label vectors')
    if not a:
        return None, None
    labels = sorted(set(a) | set(b))
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    expected = sum((a.count(k) / n) * (b.count(k) / n) for k in labels)
    # A constant unanimous vector has no variation for chance-corrected agreement.
    return observed, (observed - expected) / (1 - expected) if expected < 1 else None


def score(root, items, answers):
    """Validate every submitted answer before counting; model votes are not truth."""
    if not isinstance(items, list) or not isinstance(answers, dict):
        raise ValueError('Expected an item list and answers keyed by item ID')
    by_id = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or item['id'] in by_id:
            raise ValueError('Annotation items need unique explicit IDs')
        by_id[item['id']] = item
    model, human, per_set, disputes, chapters, pairs = [], [], {}, [], {}, []
    invalid, missing, excluded, valid = [], [], [], []
    for item_id, item in by_id.items():
        if item_id not in answers:
            missing.append(item_id)
            continue
        answer = answers[item_id]
        if not isinstance(answer, dict):
            invalid.append({'id': item_id, 'reason': 'answer_not_object'})
            continue
        kind = item.get('kind')
        if kind == 'sentence':
            texts = item.get('sentences')
            labels = answer.get('labels')
            if (not isinstance(texts, list) or not texts or any(not isinstance(t, str) for t in texts)
                    or not isinstance(labels, list) or len(labels) != len(texts) or any(v not in LABELS for v in labels)):
                invalid.append({'id': item_id, 'reason': 'sentence_count_or_label_invalid'})
                continue
            try:
                cached = json.loads((root / 'labels' / f"{item['slice']}.json").read_text(encoding='utf-8'))
                paragraph = next(p for p in cached['paragraphs'] if p.get('block') == item['block'])
                reference = paragraph.get('sentences', [])
                if ('error' in paragraph or len(reference) != len(texts)
                        or any(not isinstance(s, dict) or s.get('i') != n or s.get('text') != texts[n]
                               or s.get('label') not in LABELS for n, s in enumerate(reference))
                        or (item.get('markdown_sha256') is not None and item['markdown_sha256'] != cached.get('markdown_sha256'))
                        or (item.get('rules') is not None and item['rules'] != cached.get('rules'))):
                    raise ValueError('reference_identity_or_text_invalid')
            except (OSError, ValueError, KeyError, TypeError, StopIteration):
                excluded.append({'id': item_id, 'reason': 'model_reference_missing_or_mismatched'})
                continue
            for sentence, label in zip(reference, labels):
                model.append(sentence['label'])
                human.append(label)
                per_set.setdefault(item.get('set', 'unknown'), []).append((sentence['label'], label))
        elif kind == 'dispute':
            # The v2 page uses one sentence index in a paragraph's context and a value answer.
            context, index = item.get('context'), item.get('i')
            value = answer.get('value')
            if (not isinstance(context, list) or isinstance(index, bool) or not isinstance(index, int)
                    or not 0 <= index < len(context) or not isinstance(context[index], str) or value not in LABELS):
                invalid.append({'id': item_id, 'reason': 'dispute_address_or_label_invalid'})
                continue
            votes = item.get('votes')
            if (not isinstance(votes, dict) or not votes or any(not isinstance(k, str) or v not in LABELS for k, v in votes.items())):
                excluded.append({'id': item_id, 'reason': 'dispute_model_votes_invalid'})
                continue
            disputes.append({'id': item_id, 'slice': item.get('slice'), 'text': context[index], 'answer': value, 'votes': votes})
        elif kind == 'chapter':
            if answer.get('value') not in CHAPTER_VALUES:
                invalid.append({'id': item_id, 'reason': 'chapter_value_invalid'})
                continue
            chapters[item['slice']] = answer['value']
        elif kind == 'paraphrase':
            if answer.get('value') not in PARAPHRASE_VALUES:
                invalid.append({'id': item_id, 'reason': 'paraphrase_value_invalid'})
                continue
            pairs.append(answer['value'])
        else:
            invalid.append({'id': item_id, 'reason': 'unknown_item_kind'})
            continue
        valid.append(item_id)
    unknown = sorted(set(answers) - set(by_id))
    report = {'sentences': len(model), 'agreement': None, 'kappa': None,
              'chapters': chapters, 'paraphrases': dict(Counter(pairs)),
              'items': {'total': len(items), 'valid': len(valid), 'missing': len(missing),
                        'invalid': len(invalid), 'excluded': len(excluded), 'unknown_answers': len(unknown)},
              'missing': missing, 'invalid': invalid, 'excluded': excluded, 'unknown_answers': unknown,
              'disputes': {'items': len(disputes), 'answers': disputes, 'by_annotator': {}}}
    if model:
        report['agreement'], report['kappa'] = kappa(model, human)
        report['human_implication_share'] = human.count('implication') / len(human)
        report['model_implication_share'] = model.count('implication') / len(model)
        report['by_set'] = {k: dict(zip(('agreement', 'kappa'), kappa([m for m, _ in v], [h for _, h in v])))
                           | {'n': len(v)} for k, v in per_set.items()}
    for name in sorted({name for d in disputes for name in d['votes']}):
        rows = [d for d in disputes if name in d['votes']]
        report['disputes']['by_annotator'][name] = dict(zip(('agreement', 'kappa'),
            kappa([d['votes'][name] for d in rows], [d['answer'] for d in rows]))) | {'n': len(rows)}
    return report


def _vote_table(root, dirs):
    if len(dirs) < 2 or len(set(dirs)) != len(dirs):
        raise ValueError('Agreement needs at least two distinct annotator directories')
    table, identities, invalid, legacy = {}, {}, [], []
    for directory in dirs:
        for path in sorted((root / directory).glob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                name = data['slice']
                identity = {k: data.get(k) for k in ('slice', 'version', 'markdown_sha256', 'rules', 'reader')}
                cache_identity = data.get('cache_identity')
                if not isinstance(cache_identity, dict):
                    legacy.append({'dir': directory, 'slice': name, 'status': 'legacy-unverified'})
                else:
                    for key in ('slice', 'version', 'markdown_sha256', 'rules', 'reader_contract_sha256'):
                        if cache_identity.get(key) != data.get(key):
                            raise ValueError('cache/source identity mismatch')
                    identity['rule_prompt_sha256'] = (cache_identity.get('extra') or {}).get('rule_prompt_sha256')
                    if not identity['rule_prompt_sha256'] or not isinstance(data.get('version'), str):
                        raise ValueError('cache lacks source version or comparable rule prompt identity')
                if (not isinstance(name, str) or not isinstance(identity['markdown_sha256'], str)
                        or len(identity['markdown_sha256']) != 64 or not isinstance(identity['rules'], str)
                        or not identity['rules'] or not isinstance(identity['reader'], str)
                        or not isinstance(data.get('paragraphs'), list)):
                    raise ValueError('missing source identity')
                # New caches include full reader contracts; don't mix them with a cache lacking that identity.
                if 'reader_contract_sha256' in data:
                    identity['reader_contract_sha256'] = data['reader_contract_sha256']
                if directory in identities.setdefault(name, {}):
                    raise ValueError('duplicate slice cache')
                identities[name][directory] = identity
                seen = set()
                for paragraph in data['paragraphs']:
                    if (not isinstance(paragraph, dict) or 'error' in paragraph
                            or isinstance(paragraph.get('block'), bool) or not isinstance(paragraph.get('block'), int)
                            or not isinstance(paragraph.get('sentences'), list)):
                        invalid.append({'dir': directory, 'slice': name, 'reason': 'invalid_paragraph'})
                        continue
                    for sentence in paragraph['sentences']:
                        if (not isinstance(sentence, dict) or isinstance(sentence.get('i'), bool)
                                or not isinstance(sentence.get('i'), int) or sentence['i'] < 0
                                or not isinstance(sentence.get('text'), str) or sentence.get('label') not in LABELS):
                            invalid.append({'dir': directory, 'slice': name, 'reason': 'invalid_sentence'})
                            continue
                        key = (name, paragraph['block'], sentence['i'])
                        if key in seen:
                            invalid.append({'dir': directory, 'slice': name, 'reason': 'duplicate_sentence'})
                            table.get(key, {}).pop(directory, None)
                            continue
                        seen.add(key)
                        table.setdefault(key, {})[directory] = (sentence['label'], sentence['text'])
            except (OSError, ValueError, KeyError, TypeError) as error:
                invalid.append({'dir': directory, 'file': path.name, 'reason': str(error)[:160]})
    included, excluded = {}, Counter()
    for key, votes in table.items():
        if len(votes) != len(dirs):
            excluded['missing_annotator'] += 1
        elif len({json.dumps(i, sort_keys=True, ensure_ascii=False) for i in identities[key[0]].values()}) != 1:
            excluded['source_identity_mismatch'] += 1
        elif len({text for _, text in votes.values()}) != 1:
            excluded['sentence_text_mismatch'] += 1
        else:
            included[key] = votes
    coverage = {'candidate_sentences': len(table), 'included_sentences': len(included),
                'excluded_sentences': len(table) - len(included), 'excluded_by_reason': dict(excluded),
                'available_by_annotator': {d: sum(d in v for v in table.values()) for d in dirs},
                'missing_by_annotator': {d: sum(d not in v for v in table.values()) for d in dirs},
                'invalid_entries': invalid, 'legacy_unverified_caches': legacy,
                'identity_status': 'legacy-unverified' if legacy else 'verified',
                'legacy_included_sentences': sum(any(entry['slice'] == key[0] for entry in legacy) for key in included),
                'verified_included_sentences': sum(not any(entry['slice'] == key[0] for entry in legacy) for key in included)}
    return included, coverage


def votes(root, dirs):
    return _vote_table(root, dirs)[0]


def agree(root, dirs):
    table, coverage = _vote_table(root, dirs)
    count = len(table)
    report = {'sentences': count, 'coverage': coverage,
              'unanimous': sum(len({label for label, _ in v.values()}) == 1 for v in table.values()) / count if count else None,
              'pairs': {}}
    for n, a in enumerate(dirs):
        for b in dirs[n + 1:]:
            x = [table[k][a][0] for k in table]
            y = [table[k][b][0] for k in table]
            report['pairs'][f'{a} vs {b}'] = dict(zip(('agreement', 'kappa'), kappa(x, y))) | {'n': count}
    report['implication_share'] = {d: sum(v[d][0] == 'implication' for v in table.values()) / count if count else None for d in dirs}
    report['disagreements'] = [{'slice': k[0], 'block': k[1], 'i': k[2], 'text': next(iter(v.values()))[1],
                                'votes': {d: v[d][0] for d in dirs}} for k, v in sorted(table.items())
                               if len({label for label, _ in v.values()}) > 1]
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['seed', 'score', 'agree'])
    parser.add_argument('--dirs', nargs='+')
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
    elif args.command == 'agree':
        print(json.dumps(agree(root, args.dirs), ensure_ascii=False, indent=1))
    else:
        items = json.loads(Path(args.items).read_text())
        answers = json.loads(Path(args.answers).read_text())
        print(json.dumps(score(root, items, answers), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
