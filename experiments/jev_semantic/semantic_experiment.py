import argparse
import os
from pathlib import Path

from assessment import evaluate, review_queue, score_construction
from dataset import ReadOnlyStore, dump, export_workspaces, freeze_dataset, make_case, save_new
from engine import execute
from governance import evaluate_policy, freeze_policy, registry
from review_ui import serve


def demo_cases():
    rows = []
    for i, sentence in enumerate(('甲公司计划于2027年形成10GW产能。', '本季度已签约客户数量为12家。',
                                  '1. 经营摘要', '参见原披露第23页，收入同比增长8%。')):
        import re
        for match in re.finditer(r'\d+(?:\.\d+)?', sentence):
            provenance = {'run_id': 'synthetic-report-' + str(i), 'version_id': 'synthetic-v1-' + str(i),
                          'brief_hash': 'synthetic', 'sources': [], 'body_range': list(match.span()), 'exposure': 'public'}
            rows.append(make_case('numeric_omission', {'report': {'objective': '跟踪项目进度和经营变化', 'audience': '内部研究者'},
                'paragraph': sentence, 'target': {'text': match.group(), 'start': match.start(), 'end': match.end()}},
                provenance, observed={'verification': 'unbound'}, origin='synthetic'))
    for i, (old, new) in enumerate((('计划2027年投产。', '现预计2028年投产。'), ('产能为10GW。', '产能为10GW。'),
                                  ('甲公司产量为8GW。', '乙公司产能为10GW。'))):
        state = {'report': {'objective': '追踪甲公司开工投产节点', 'key_questions': ['投产时间是否变化？']},
                 'old_source': {'text': old}, 'new_source': {'text': new}}
        provenance = {'run_id': 'synthetic-change-' + str(i), 'version_id': 'synthetic-c1-' + str(i),
                      'brief_hash': 'synthetic', 'sources': [], 'exposure': 'public'}
        rows.append(make_case('change_materiality', state, provenance, origin='synthetic'))
        rows.append(make_case('conclusion_update', {**state, 'old_conclusion': {'statement': old}}, provenance,
                              origin='synthetic'))
    return rows


def main():
    parser = argparse.ArgumentParser(description='默认离线的报告语义判断实验；不改生产、不继承 OfficeQA 答案标签')
    sub = parser.add_subparsers(dest='command', required=True)
    ls = sub.add_parser('list', help='只读列出可选报告版本')
    ls.add_argument('--workspace', required=True)
    export = sub.add_parser('export', help='只读导出明确选定的报告版本及来源更新')
    export.add_argument('--workspace', required=True)
    export.add_argument('--version', action='append', required=True)
    export.add_argument('--out', required=True)
    export.add_argument('--exposure', choices=['private', 'public'], default='private')
    export.add_argument('--seed', default='semantic-v2')
    export.add_argument('--origin', choices=['natural', 'synthetic'], default='natural')
    demo = sub.add_parser('demo', help='生成明确标记为合成的操作样例，不附人工真值')
    demo.add_argument('--out', required=True)
    run = sub.add_parser('run', help='运行同题对照；模型调用需显式授权')
    run.add_argument('--dataset', required=True)
    run.add_argument('--out', required=True)
    run.add_argument('--provider', choices=['rules', 'jev', 'llm'], default='rules')
    run.add_argument('--model')
    run.add_argument('--endpoint')
    run.add_argument('--key-env', help='只指定环境变量名称，不传凭据值')
    run.add_argument('--policy', help='validation/test 必须提供 freeze-policy 登记的策略文件')
    run.add_argument('--split', choices=['dev', 'validation', 'test'], default='dev')
    run.add_argument('--allow-network', action='store_true')
    run.add_argument('--allow-private-external', action='store_true')
    run.add_argument('--prefilter', action='store_true', help='确定性引文/定位前置过滤，命中者不进模型')
    run.add_argument('--with-probabilities', action='store_true', help='仅 llm 臂：要求类别概率分布')
    run.add_argument('--response-format', choices=['json_schema', 'json_object'], default='json_schema',
                     help='仅 llm 臂：端点不支持严格 schema 时用 json_object，回答仍按同一 schema 严格校验')
    run.add_argument('--timeout', type=float, default=30)
    run.add_argument('--attempts', type=int, default=2)
    run.add_argument('--max-request-bytes', type=int, default=60000)
    ui = sub.add_parser('serve', help='本地盲标或候选复核页面')
    ui.add_argument('--dataset', required=True)
    ui.add_argument('--annotations', required=True)
    ui.add_argument('--port', type=int, default=0)
    ui.add_argument('--mode', choices=['label', 'review'], default='label')
    ui.add_argument('--run')
    ev = sub.add_parser('evaluate', help='按人工局部标签评价；没有标签则显示未评测')
    ev.add_argument('--dataset', required=True)
    ev.add_argument('--run', action='append', required=True)
    ev.add_argument('--annotations', required=True)
    ev.add_argument('--out', required=True)
    score = sub.add_parser('score', help='按构造标签给合成数据集打分；可选正类概率阈值')
    score.add_argument('--dataset', required=True)
    score.add_argument('--run', required=True)
    score.add_argument('--truth', required=True)
    score.add_argument('--threshold', type=float)
    score.add_argument('--out')
    freeze = sub.add_parser('freeze-policy', help='仅用 dev 标签冻结策略；留出集开始后禁止重新拟合')
    freeze.add_argument('--dataset', required=True)
    freeze.add_argument('--run', required=True)
    freeze.add_argument('--labels', required=True)
    freeze.add_argument('--label-source', choices=['human', 'construction'], default='human')
    held = sub.add_parser('evaluate-policy', help='保存一次留出评价；重试只返回原记录，不重新评分')
    held.add_argument('--dataset', required=True)
    held.add_argument('--run', required=True)
    held.add_argument('--policy', required=True)
    held.add_argument('--labels', required=True)
    held.add_argument('--out', required=True)
    queue = sub.add_parser('queue', help='导出全部候选及其处理状态，不隐藏低优先级项')
    queue.add_argument('--dataset', required=True)
    queue.add_argument('--run', required=True)
    queue.add_argument('--out', required=True)
    queue.add_argument('--annotations')
    args = parser.parse_args()
    if args.command == 'list':
        store = ReadOnlyStore(args.workspace)
        try:
            for row in store.rows('SELECT id,run_id,created FROM briefs ORDER BY created DESC'):
                print(dump(row))
        finally:
            store.close()
    elif args.command == 'export':
        print(dump(export_workspaces([(args.workspace, args.version)], args.out, args.exposure, args.seed, args.origin)))
    elif args.command == 'demo':
        print(dump(freeze_dataset(demo_cases(), args.out, metadata={'synthetic': True, 'quality_evidence': False})))
    elif args.command == 'run':
        model, endpoint, key = args.model, args.endpoint, None
        if args.provider != 'rules':
            env_name = args.key_env or ('TYPESAFE_API_KEY' if args.provider == 'jev' else 'SEMANTIC_LLM_API_KEY')
            model = model or ('jev-latest' if args.provider == 'jev' else os.environ.get('SEMANTIC_LLM_MODEL'))
            endpoint = endpoint or ('https://api.typesafe.ai/v1/systemone' if args.provider == 'jev' else os.environ.get('SEMANTIC_LLM_URL'))
            key = os.environ.get(env_name)
        print(dump(execute(args.dataset, args.out, args.provider, model, endpoint, key, args.split,
            args.allow_network, args.allow_private_external, args.timeout, args.attempts, args.max_request_bytes,
            args.prefilter, args.with_probabilities, frozen_policy=args.policy,
            response_format=args.response_format)))
    elif args.command == 'serve':
        server = serve(args.dataset, args.annotations, args.port, args.mode, args.run)
        print(f'http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    elif args.command == 'evaluate':
        result = evaluate(args.dataset, args.run, args.annotations)
        save_new(args.out, result)
        print(dump(result))
    elif args.command == 'score':
        result = score_construction(args.dataset, args.run, args.truth, args.threshold)
        if args.out:
            save_new(args.out, result)
        print(dump(result))
    elif args.command == 'freeze-policy':
        result = freeze_policy(args.dataset, args.run, args.labels, args.label_source)
        print(dump({'policy_id': result['policy_id'], 'path': str(registry(args.dataset) / ('policy-' + result['policy_id'] + '.json'))}))
    elif args.command == 'evaluate-policy':
        result = evaluate_policy(args.dataset, args.run, args.policy, args.labels)
        save_new(args.out, result)
        print(dump(result))
    elif args.command == 'queue':
        result = review_queue(args.dataset, args.run, args.annotations)
        save_new(args.out, result)
        print(f'已导出 {len(result)} 项；全部保留，待独立复核')


if __name__ == '__main__':
    main()
