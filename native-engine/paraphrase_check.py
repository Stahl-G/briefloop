"""Independent meaning check of the paraphrase pairs (#757 benchmark v2).

The mechanical checks in paraphrase_judgments.py cannot see a changed subject,
a softened certainty or a flipped direction. A second model from another
family judges every cached pair; pairs it calls changed or unsure go to the
annotation page for the user, and only pairs judged the same enter the
score-invariant paraphrase perturbation.

  python paraphrase_check.py --slices DIR --out paraphrase-checks.json [--model swe-2-high]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cli_label import run_devin  # noqa: E402
from label_sentences import RULES, digest  # noqa: E402

PROMPT = '''下面每一项是一句中文报告原句和它的改写。逐项判断改写是否与原句意思完全相同：事实、数字、主体、时间、限定条件、确定程度（「可能」与「将」不同）和判断方向都必须不变，只允许换说法。
- same：意思完全相同。
- changed：任何一处信息、确定程度或方向有变化，或增删了信息。
- unsure：无法确定。
不要运行任何命令或读取任何文件，只根据下面的文字作答。
只输出 JSON：{{"checks": [{{"id": 输入中的 id, "value": "same" | "changed" | "unsure", "reason": "changed 或 unsure 时写一句理由"}}]}}，覆盖全部输入。
输入：
{pairs}'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--slices', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--paraphrases', default='paraphrases', help='explicit frozen paraphrase directory')
    parser.add_argument('--model', default='swe-2-high')
    args = parser.parse_args()
    args.cli = 'devin'
    root = Path(args.slices).expanduser()
    out = Path(args.out)
    done = json.loads(out.read_text()) if out.exists() else {}
    tasks = []
    for cache in sorted((root / args.paraphrases).glob('*.json')):
        data = json.loads(cache.read_text(encoding='utf-8'))
        pairs = [{'id': f"{data['slice']}#{n}", 'original': o, 'paraphrase': p}
                 for n, (o, p) in enumerate(data['pairs'].items())]
        todo = []
        for pair in pairs:
            identity = {'schema_version': 1, 'rules': RULES, 'checker': 'devin', 'requested_model': args.model,
                        'prompt_sha256': digest(PROMPT), 'original': pair['original'], 'paraphrase': pair['paraphrase'],
                        'source_cache_identity_sha256': digest(data.get('cache_identity')),
                        'markdown_sha256': data.get('markdown_sha256'),
                        'reader_contract_sha256': data.get('reader_contract_sha256')}
            prior = done.get(pair['id'])
            if prior is not None:
                if (not isinstance(prior, dict) or prior.get('cache_identity') != identity
                        or prior.get('original') != pair['original'] or prior.get('paraphrase') != pair['paraphrase']
                        or prior.get('value') not in ('same', 'changed', 'unsure')):
                    raise ValueError(f"{pair['id']}: check cache differs/incomplete; choose a new --out file; original preserved")
                continue
            todo.append({**pair, 'cache_identity': identity})
        if todo:
            tasks.append((data['slice'], todo))
    for name, todo in tasks:
        inputs = [{k: p[k] for k in ('id', 'original', 'paraphrase')} for p in todo]
        result, tool_use, usage = run_devin(args, PROMPT.format(pairs=json.dumps(inputs, ensure_ascii=False)))
        if result is None or tool_use:
            print(name, 'failed', usage if result is None else 'used tools', flush=True)
            continue
        entries = result.get('checks', []) if isinstance(result, dict) else []
        by_id = {}
        for check in entries if isinstance(entries, list) else []:
            if isinstance(check, dict):
                by_id.setdefault(check.get('id'), []).append(check)
        for pair in todo:
            matches = by_id.get(pair['id'], [])
            check = matches[0] if len(matches) == 1 else None
            if check and check.get('value') in ('same', 'changed', 'unsure'):
                done[pair['id']] = {**pair, 'value': check['value'], 'reason': str(check.get('reason', ''))[:200],
                                    'model': f'devin/{args.model}'}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding='utf-8')
        values = [done[p['id']]['value'] for p in todo if p['id'] in done]
        print(name, {v: values.count(v) for v in set(values)}, 'missing', len(todo) - len(values), flush=True)


if __name__ == '__main__':
    main()
