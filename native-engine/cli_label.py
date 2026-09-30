"""Second-annotator sentence labels from an agent CLI — Codex or Devin (#757 benchmark v2).

Same definition and prompt as label_sentences.py, one `codex exec` per slice
with every body paragraph in the prompt. The run starts in an empty directory
with a read-only sandbox, and a label file is kept only when the event stream
shows no command or tool call, so the labels come from reading the prompt and
not from looking at other annotators' files.

  python cli_label.py --slices DIR --cli codex [--model gpt-6.1-sol] [--effort medium]
  python cli_label.py --slices DIR --cli devin [--model swe-2-high]
"""
import argparse
import re
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import seed_value  # noqa: E402
from label_sentences import PROMPT, RULES  # noqa: E402

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['paragraphs'],
          'properties': {'paragraphs': {'type': 'array', 'items': {
              'type': 'object', 'additionalProperties': False, 'required': ['block', 'labels'],
              'properties': {'block': {'type': 'integer'},
                             'labels': {'type': 'array', 'items': {'type': 'string', 'enum': ['implication', 'fact']}}}}}}}
NOTE = ('\n不要运行任何命令或读取任何文件，只根据下面给出的文字作答。下面有多个段落，每段单独标注；'
        '输出 {"paragraphs": [{"block": 段落编号, "labels": [...与该段句子等长]}]}，覆盖全部段落。\n')


def run_codex(args, prompt):
    """One codex exec in an empty directory; returns (result, tool_use, usage)."""
    with tempfile.TemporaryDirectory() as work, tempfile.TemporaryDirectory() as side:
        schema, answer = Path(side, 'schema.json'), Path(side, 'answer.json')
        schema.write_text(json.dumps(SCHEMA))
        run = subprocess.run(['codex', 'exec', '-m', args.model, '-c', f'model_reasoning_effort="{args.effort}"',
                              '-s', 'read-only', '--skip-git-repo-check', '--ephemeral', '--json', '-C', work,
                              '--output-schema', str(schema), '-o', str(answer), '-'],
                             input=prompt, capture_output=True, text=True, timeout=1800)
        events = [json.loads(line) for line in run.stdout.splitlines() if line.startswith('{')]
        tool_use = [e for e in events if e.get('item', {}).get('type') in ('command_execution', 'mcp_tool_call', 'file_change', 'web_search')]
        usage = [e.get('usage') for e in events if e.get('type') == 'turn.completed']
        if run.returncode or not answer.exists():
            return None, tool_use, run.stderr[-300:]
        return json.loads(answer.read_text()), tool_use, usage


def run_devin(args, prompt):
    """One devin -p in an empty directory; tool use is read from the exported trajectory."""
    with tempfile.TemporaryDirectory() as work, tempfile.TemporaryDirectory() as side:
        prompt_file, export = Path(side, 'prompt.txt'), Path(side, 'conv.json')
        prompt_file.write_text(prompt + '\n只输出 JSON，不要代码块或其他文字。')
        run = subprocess.run(['devin', '--model', args.model, '--respect-workspace-trust', 'false', '--export', str(export),
                              '--prompt-file', str(prompt_file), '-p'], cwd=work, capture_output=True, text=True, timeout=1800)
        if run.returncode or not export.exists():
            return None, [], run.stderr[-300:]
        steps = [s for s in json.loads(export.read_text())['steps'] if s.get('source') == 'agent']
        tool_use = [c for s in steps for c in (s.get('tool_calls') or [])]
        usage = [s.get('metrics') for s in steps]
        text = re.sub(r'^```(?:json)?|```$', '', (steps[-1].get('message') or '').strip(), flags=re.M).strip() if steps else ''
        try:
            return json.loads(text), tool_use, usage
        except ValueError:
            return None, tool_use, 'unparseable answer: ' + text[:200]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--slices', required=True)
    parser.add_argument('--cli', choices=['codex', 'devin'], default='codex')
    parser.add_argument('--model')
    parser.add_argument('--out', help='subdirectory; default labels-<rules>-<model>')
    parser.add_argument('--effort', default='medium')
    args = parser.parse_args()
    args.model = args.model or {'codex': 'gpt-6.1-sol', 'devin': 'swe-2-high'}[args.cli]
    root = Path(args.slices).expanduser()
    out = root / (args.out or f'labels-{RULES}-{args.model}')
    out.mkdir(exist_ok=True)
    for entry in json.loads((root / 'manifest.json').read_text(encoding='utf-8'))['slices']:
        target = out / f"{entry['name']}.json"
        db = sqlite3.connect(root / entry['name'] / 'briefloop.db')
        markdown = db.execute('SELECT markdown FROM briefs WHERE id=?', (entry['version'],)).fetchone()[0]
        digest = hashlib.sha256(markdown.encode()).hexdigest()
        if target.exists() and json.loads(target.read_text())['markdown_sha256'] == digest:
            continue
        # The reader line and sentence split are taken from the first annotator's file, so every annotator labels the same units.
        reference = json.loads((root / 'labels' / f"{entry['name']}.json").read_text())
        units = [{'block': p['block'], 'sentences': [s['text'] for s in p['sentences']]} for p in reference['paragraphs'] if 'sentences' in p]
        if not units:
            continue
        prompt = PROMPT.format(reader=reference['reader']).rsplit('只输出', 1)[0] + NOTE + json.dumps(units, ensure_ascii=False)
        runner = run_codex if args.cli == 'codex' else run_devin
        result, tool_use, usage = runner(args, prompt)
        if result is None:
            print(entry['name'], 'failed', usage, flush=True)
            continue
        if tool_use:
            print(entry['name'], 'rejected: the run used tools', flush=True)
            continue
        by_block = {p['block']: p['labels'] for p in result['paragraphs']}
        paragraphs = []
        for unit in units:
            labels = by_block.get(unit['block'])
            if not labels or len(labels) != len(unit['sentences']):
                paragraphs.append({'block': unit['block'], 'error': 'invalid_labels', 'sentences': unit['sentences'], 'labels': labels})
                continue
            paragraphs.append({'block': unit['block'], 'sentences': [{'i': n, 'text': t, 'label': label}
                                                                      for n, (t, label) in enumerate(zip(unit['sentences'], labels))]})
        target.write_text(json.dumps({'slice': entry['name'], 'markdown_sha256': digest, 'models': [f'{args.cli}/{args.model}'],
                                      'effort': args.effort, 'rules': RULES, 'reader': reference['reader'], 'paragraphs': paragraphs,
                                      'usage': usage}, ensure_ascii=False, indent=1))
        counts = [s['label'] for p in paragraphs for s in p.get('sentences', [])]
        print(entry['name'], 'implication', counts.count('implication'), 'fact', counts.count('fact'),
              'invalid', sum('error' in p for p in paragraphs), flush=True)


if __name__ == '__main__':
    main()
