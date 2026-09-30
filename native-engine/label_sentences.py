"""Label each body sentence of a slice as an implication or a fact (#757 benchmark v2).

The first benchmark treated a paragraph's first sentence as its judgment. That
holds for the TOYO management reports and not for news-style weeklies, whose
first sentence is often a fact, so removing "conclusions" there removed facts.
Perturbations now act on sentences labelled here. A model proposes the labels;
a person checks a sample in the annotation page before any run relies on them,
and the agreement is reported with the results.

Definition (the operational one from the design; grey zones A–E in PROMPT): an implication sentence says
what a fact or development means for this report's reader — a consequence, a
trade-off, a decision to make, or something to watch — and rests on the
material. Caveats, "worth watching", restating a fact in other words, and
unsupported speculation are not implications.

  python label_sentences.py --slices DIR [--model opencode-go/deepseek-v4.1-flash]
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import seed_value  # noqa: E402

# r2 (2026-09-30): the five grey zones where annotators split, settled by the user.
RULES = 'r2'
PROMPT = '''你在给一份中文研究报告的句子做标注。读者与任务：{reader}
逐句判断类别。先问：这句只是在说发生了什么，还是在说这对读者意味着什么？
- implication：说出了后果、取舍、需要做的决定或具体要盯的节点之一，并且能从报告里的材料推出。
- fact：陈述发生了什么、数字、日期、来源说法、口径说明、谨慎提醒；换一种说法重复事实、「值得关注」「影响深远」之类的空泛表态、没有依据的推测，都不算 implication。
一句话里既有事实又有影响判断时，标 implication。
边界规则：
A. 解释传导路径的句子：落到读者的收入、成本、融资或合规等后果上，标 implication；只讲政策或机制本身如何运作，标 fact。
B. 带论断的小标题句：按内容判断；说出了后果或压力，标 implication；只是话题名，标 fact。
C.「后续观察……」清单：只列出要看什么，标 fact；同时说明为什么看、出现什么结果意味着什么，标 implication。
D. 数据缺口、无法核验、口径限制的说明：标 fact。
E. 竞争定位（某公司是谁的竞争者、属于哪一类）：只给定位，标 fact；说出这对读者意味着什么（价格压力、客户重叠等），标 implication。
只输出 JSON：{{"labels": [与输入等长、顺序一致，每项为 "implication" 或 "fact"]}}。
段落句子：'''



def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def frozen_source(root, entry):
    database = (Path(root) / entry['name'] / 'briefloop.db').resolve()
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT markdown,run_id FROM briefs WHERE id=?', (entry['version'],)).fetchone()
        if row is None:
            raise ValueError(f"Frozen version missing: {entry['name']}")
        run = db.execute('SELECT requirements FROM runs WHERE id=?', (row[1],)).fetchone()
    if run is None:
        raise ValueError(f"Reader requirements missing: {entry['name']}")
    requirements = json.loads(run[0])
    reader = '；'.join(str(requirements.get(k, '')) for k in ('title', 'objective', 'audience')
                      if requirements.get(k))[:600]
    return row[0], requirements, reader


def sentence_units(markdown):
    blocks, body = seed_value._body_paragraphs(markdown)
    return [{'block': index, 'sentences': [seed_value._plain(s) for s in seed_value._sentences(blocks[index])]}
            for index in body if seed_value._sentences(blocks[index])]


def cache_identity(entry, markdown, requirements, reader, annotator, model, prompt, *, effort=None, extra=None):
    return {'schema_version': 1, 'slice': entry['name'], 'version': entry['version'], 'rules': RULES,
            'markdown_sha256': hashlib.sha256(markdown.encode()).hexdigest(),
            'reader_contract_sha256': digest(requirements),
            'reader_sha256': digest({'reader': reader, **{k: requirements.get(k) for k in ('title', 'objective', 'audience')}}),
            'annotator': annotator, 'requested_model': model, 'effort': effort,
            'prompt_sha256': digest(prompt), 'extra': {'rule_prompt_sha256': digest(PROMPT), **(extra or {})}}


def matching_cache(path, identity):
    """Never overwrite or silently reuse an old/incomplete cache."""
    if not path.exists():
        return False
    try:
        cached = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'{path}: invalid cache; choose a new --out directory') from exc
    if (cached.get('cache_identity') != identity or cached.get('complete') is not True
            or cached.get('payload_sha256') != digest({k: v for k, v in cached.items() if k != 'payload_sha256'})):
        raise ValueError(f'{path}: cache identity/integrity differs; choose a new --out directory; original preserved')
    return True


def write_cache(path, data):
    # Exclusive creation also protects against another annotator starting concurrently.
    data = {**data, 'payload_sha256': digest(data)}
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=1)


def main():
    from paraphrase_judgments import request
    parser = argparse.ArgumentParser()
    parser.add_argument('--slices', required=True)
    parser.add_argument('--model', default='opencode-go/deepseek-v4.1-flash')
    parser.add_argument('--out', '--out-dir', default='labels', help='new output directory; incompatible caches are never overwritten')
    args = parser.parse_args()
    root = Path(args.slices).expanduser().resolve()
    out = root / args.out
    tasks = []
    for entry in json.loads((root / 'manifest.json').read_text(encoding='utf-8'))['slices']:
        markdown, requirements, reader = frozen_source(root, entry)
        units = sentence_units(markdown)
        prompts = [PROMPT.format(reader=reader) + json.dumps(unit['sentences'], ensure_ascii=False) for unit in units]
        identity = cache_identity(entry, markdown, requirements, reader, 'provider-chat', args.model, prompts)
        target = out / f"{entry['name']}.json"
        if matching_cache(target, identity):
            continue
        tasks.append((entry, reader, units, prompts, identity, target))
    out.mkdir(parents=True, exist_ok=True)
    for entry, reader, units, prompts, identity, target in tasks:
        paragraphs, models, usage = [], set(), []
        for unit, prompt in zip(units, prompts):
            try:
                model, answer, used = request(args.model, prompt)
            except (RuntimeError, ValueError, KeyError) as error:
                paragraphs.append({**unit, 'error': str(error)[:300]})
                continue
            labels = answer.get('labels', [])
            models.add(model)
            usage.append(used)
            if (not isinstance(labels, list) or len(labels) != len(unit['sentences'])
                    or any(label not in ('implication', 'fact') for label in labels)):
                paragraphs.append({**unit, 'error': 'invalid_labels', 'labels': labels})
                continue
            paragraphs.append({'block': unit['block'], 'sentences': [{'i': n, 'text': t, 'label': label}
                              for n, (t, label) in enumerate(zip(unit['sentences'], labels))]})
        write_cache(target, {'slice': entry['name'], 'version': entry['version'],
                            'markdown_sha256': identity['markdown_sha256'],
                            'reader_contract_sha256': identity['reader_contract_sha256'],
                            'reader_sha256': identity['reader_sha256'], 'cache_identity': identity,
                            'complete': not any('error' in p for p in paragraphs),
                            'requested_model': args.model, 'models': sorted(models),
                            'rules': RULES, 'reader': reader, 'paragraphs': paragraphs, 'usage': usage})
        counts = [s['label'] for p in paragraphs for s in p.get('sentences', []) if isinstance(s, dict)]
        print(entry['name'], 'paragraphs', len(paragraphs), 'implication', counts.count('implication'), 'fact', counts.count('fact'),
              'invalid', sum('error' in p for p in paragraphs), flush=True)


if __name__ == '__main__':
    main()
