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
    parser.add_argument('--model', default='swe-2-high')
    args = parser.parse_args()
    args.cli = 'devin'
    root = Path(args.slices).expanduser()
    out = Path(args.out)
    done = json.loads(out.read_text()) if out.exists() else {}
    for cache in sorted((root / 'paraphrases').glob('*.json')):
        data = json.loads(cache.read_text())
        pairs = [{'id': f"{data['slice']}#{n}", 'original': o, 'paraphrase': p} for n, (o, p) in enumerate(data['pairs'].items())]
        todo = [p for p in pairs if done.get(p['id'], {}).get('original') != p['original']]
        if not todo:
            continue
        result, tool_use, usage = run_devin(args, PROMPT.format(pairs=json.dumps(todo, ensure_ascii=False)))
        if result is None or tool_use:
            print(data['slice'], 'failed', usage if result is None else 'used tools', flush=True)
            continue
        by_id = {c.get('id'): c for c in result.get('checks', [])}
        for p in todo:
            check = by_id.get(p['id'])
            if check and check.get('value') in ('same', 'changed', 'unsure'):
                done[p['id']] = {**p, 'value': check['value'], 'reason': str(check.get('reason', ''))[:200], 'model': f'devin/{args.model}'}
        out.write_text(json.dumps(done, ensure_ascii=False, indent=1))
        values = [done[p['id']]['value'] for p in todo if p['id'] in done]
        print(data['slice'], {v: values.count(v) for v in set(values)}, 'missing', len(todo) - len(values), flush=True)


if __name__ == '__main__':
    main()
