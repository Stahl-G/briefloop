"""Label each body sentence of a slice as an implication or a fact (#757 benchmark v2).

The first benchmark treated a paragraph's first sentence as its judgment. That
holds for the TOYO management reports and not for news-style weeklies, whose
first sentence is often a fact, so removing "conclusions" there removed facts.
Perturbations now act on sentences labelled here. A model proposes the labels;
a person checks a sample in the annotation page before any run relies on them,
and the agreement is reported with the results.

Definition (the operational one from the design): an implication sentence says
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
from paraphrase_judgments import request  # noqa: E402

PROMPT = '''你在给一份中文研究报告的句子做标注。读者与任务：{reader}
逐句判断类别：
- implication：说明某个事实或变化对本报告读者意味着什么——后果、取舍、需要做的决定或需要观察的节点，并且以报告里的材料为依据。
- fact：陈述发生了什么、数字、日期、来源说法、口径说明、谨慎提醒；换一种说法重复事实、「值得关注」之类的空泛表态、没有依据的推测，都不算 implication。
一句话里既有事实又有影响判断时，标 implication。
只输出 JSON：{{"labels": [与输入等长、顺序一致，每项为 "implication" 或 "fact"]}}。
段落句子：'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--slices', required=True)
    parser.add_argument('--model', default='opencode-go/deepseek-v4.1-flash')
    args = parser.parse_args()
    root = Path(args.slices).expanduser()
    out = root / 'labels'
    out.mkdir(exist_ok=True)
    for entry in json.loads((root / 'manifest.json').read_text(encoding='utf-8'))['slices']:
        target = out / f"{entry['name']}.json"
        db = sqlite3.connect(root / entry['name'] / 'briefloop.db')
        markdown = db.execute('SELECT markdown FROM briefs WHERE id=?', (entry['version'],)).fetchone()[0]
        run = db.execute('SELECT requirements FROM runs WHERE id=(SELECT run_id FROM briefs WHERE id=?)', (entry['version'],)).fetchone()
        requirements = json.loads(run[0]) if run else {}
        reader = '；'.join(str(requirements.get(k, '')) for k in ('title', 'objective', 'audience') if requirements.get(k))[:600]
        digest = hashlib.sha256(markdown.encode()).hexdigest()
        if target.exists() and json.loads(target.read_text())['markdown_sha256'] == digest:
            continue
        blocks, body = seed_value._body_paragraphs(markdown)
        paragraphs, models, usage = [], set(), []
        for index in body:
            sentences = [seed_value._plain(s) for s in seed_value._sentences(blocks[index])]
            if not sentences:
                continue
            model, answer, used = request(args.model, PROMPT.format(reader=reader) + json.dumps(sentences, ensure_ascii=False))
            labels = answer.get('labels', [])
            models.add(model)
            usage.append(used)
            if len(labels) != len(sentences) or any(label not in ('implication', 'fact') for label in labels):
                paragraphs.append({'block': index, 'error': 'invalid_labels', 'sentences': sentences, 'labels': labels})
                continue
            paragraphs.append({'block': index, 'sentences': [{'i': n, 'text': t, 'label': label}
                                                              for n, (t, label) in enumerate(zip(sentences, labels))]})
        target.write_text(json.dumps({'slice': entry['name'], 'markdown_sha256': digest, 'models': sorted(models),
                                      'reader': reader, 'paragraphs': paragraphs, 'usage': usage}, ensure_ascii=False, indent=1))
        counts = [s['label'] for p in paragraphs for s in p.get('sentences', []) if isinstance(s, dict)]
        print(entry['name'], 'paragraphs', len(paragraphs), 'implication', counts.count('implication'), 'fact', counts.count('fact'),
              'invalid', sum('error' in p for p in paragraphs), flush=True)


if __name__ == '__main__':
    main()
