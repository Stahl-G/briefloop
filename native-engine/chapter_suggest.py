"""Model suggestions for the chapter question in the annotation page (#757).

"Should this chapter carry implications for this reader?" is the user's call
(what the reader wants), not an objective label. A model proposes an answer
and a one-line reason so the user confirms or changes it instead of starting
from nothing; the page shows the suggestion and records the user's answer.

  python chapter_suggest.py --items annotation-items.json --out chapter-suggestions.json [--model swe-2-high]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cli_label import run_devin  # noqa: E402

PROMPT = '''下面是一份中文研究报告里的一章（含报告开头）。读者与任务：{reader}
问题：对这位读者，这一章应该包含「含义判断」吗？含义判断指说明事实或变化对读者意味着什么：后果、取舍、需要做的决定、具体要盯的节点。
- should：读者需要这一章说明影响或取舍，只列事实会让读者自己去猜。
- optional：有更好，只列事实也能接受（例如判断已集中在报告其他章节）。
- not_needed：这一章的职责就是交代事实、来源或口径。
不要运行任何命令或读取任何文件，只根据下面的文字作答。
只输出 JSON：{{"value": "should" | "optional" | "not_needed", "reason": "一句话理由"}}
章节：
{text}'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--items', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--model', default='swe-2-high')
    args = parser.parse_args()
    args.cli = 'devin'
    out = Path(args.out)
    done = json.loads(out.read_text()) if out.exists() else {}
    for item in json.loads(Path(args.items).read_text()):
        if item['kind'] != 'chapter' or item['id'] in done:
            continue
        result, tool_use, usage = run_devin(args, PROMPT.format(reader=item['reader'], text=item['text']))
        if result is None or tool_use or result.get('value') not in ('should', 'optional', 'not_needed'):
            print(item['id'], 'failed', usage if result is None else ('used tools' if tool_use else result), flush=True)
            continue
        done[item['id']] = {'value': result['value'], 'reason': str(result.get('reason', ''))[:200], 'model': f'devin/{args.model}'}
        out.write_text(json.dumps(done, ensure_ascii=False, indent=1))
        print(item['id'], result['value'], flush=True)


if __name__ == '__main__':
    main()
