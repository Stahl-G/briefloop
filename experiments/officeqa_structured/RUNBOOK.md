# OfficeQA 结构化评测 · Q3 开发试跑 RUNBOOK

对应 `experiments/officeqa_structured/config.json`（Q3 已冻结，无占位字段）与协议 BL-OQA-SR-v1.0。
所有命令都在工作树 `/Users/yihongguo/Developer/briefloop-worktrees/officeqa-experiment` 下执行，使用其 venv。

## 冻结要点（开跑前自检）

- 模型/宿主：`opencode-go/deepseek-v4.1-flash`，推理档 high。effort 传法已查证（2026-09-15）：
  `opencode run --help` 的 `--variant`（"provider-specific reasoning effort"），且宿主模型目录为该模型声明
  `variants {low, high, max}` → `reasoningEffort`；HTTP 会话 `ModelRef.variant` 同路。A 组经
  `--model/--variant` 传入，B 组经 episode 工作区 settings（`agent_backend=opencode`、`model_variant=high`）
  由 `store.enqueue` 冻结进每个 job payload。
- 开发 6 题：evaluator-only v1 池按 case key 排序前 6（`config.dataset.eligible_questions.dev_pilot.case_keys`），
  已由 `prepare_dataset.py dev-pilot` 写入 `question_only/dev_pilot.jsonl` 并在 ledger 标 dev/exposed；
  gold 只在 evaluator-only。语料条件：这 6 题官方出处（旧 Treasury Bulletin）不在冻结的 V2 全语料（1435 份）中，
  pilot 成绩只作接入诊断。
- 真实执行门：`run` 不带 `--dry-run` 时依次校验 config 冻结（无 null/占位）、数据区审计 green、
  凭据剥离、代码态与 `integration_commit`（`afe0233b`）一致（src/experiments 除 config 与本 RUNBOOK）、
  solver 边界活动探针（seatbelt profile + shim）。任一失败即拒跑。
- 预算：单集墙钟 1800 秒、并发活跃调用 4、格式修复 1 次、检索 30 次/候选 150/按页 60（A/B 同额同工具，
  由 corpus_adapter 按 episode 记账硬限）。
- 付费授权仅限既定 12 个 episodes（6 题 × 2 组）。绝不加跑；重跑失败 episode 需要新的授权与新的 run label。

## 1) smoke：第 1 题双组（2 episodes）

```bash
cd /Users/yihongguo/Developer/briefloop-worktrees/officeqa-experiment && \
.venv/bin/python experiments/officeqa_structured/run_episodes.py run \
  --run-label q3-smoke \
  --case-keys 0149cdbe9d9bb9197ade59d4be8388522fc9537298037459557998132a31161a
```

- 默认 `--arms A,B`、`--episode-workers` = 并发上限 4；A/B 先后按 seed 随机交错（协议 §8.2）。
- 产物：`<data_root>/episodes/q3-smoke/`（A/B 两目录：episode_record.json、submit/、workspace/、
  corpus-budget.jsonl），以及 `run_index.json`（含 model_calls 并发报告、隔离探针、config 哈希）。
- 观察：A 组 `episodes/q3-smoke/A/<key>/workspace/opencode-main.stdout`；B 组 episode workspace 的
  jobs/事件（SQLite）与 `research/` 任务目录。
- 验收：两集均出 `episode_record.json`、`chosen_submission` 或如实缺失；scorer 不参与；无 gold 泄漏路径。

## 2) full：其余 5 题 × 2 组（10 episodes）

smoke 验收通过后执行：

```bash
cd /Users/yihongguo/Developer/briefloop-worktrees/officeqa-experiment && \
.venv/bin/python experiments/officeqa_structured/run_episodes.py run \
  --run-label q3-full \
  --case-keys \
    0150618853280e165f95fb73770d9ad8754138d116fb4a0c5373e597f4d774b5 \
    024fdc37027f0510d882e6d9151bb477a601c619ce091b9caabcfb14c72a4e5d \
    02f497d1512fd70085f5acc5a89deccdc83a3930033cd3b4d7c00e5dcd65b11d \
    080830120bd2a2fd4753db7f22d0f5436a3ac72303a3512be2c108b1b6059c84 \
    0910bdb78dda02ffe01f9fc22f2e20a73d29479e8c35088f29656e029f12be1f
```

- 建议后台执行并轮询：`nohup … > /tmp/q3-full.log 2>&1 &`；子进程（episode）超过 40 分钟无输出即杀掉
  该次运行（`kill` runner 进程树），受影响 episode 按失败/缺失留在分母，冻结评分时计 0；不要为凑分数重跑。
- 单集墙钟由 runner 强制（超时杀进程组/stop_job）；总量约 10 × ≤30 分钟 ÷ 并发 4。

## 3) score：冻结 + 评分（先封存预测，再读 gold）

```bash
cd /Users/yihongguo/Developer/briefloop-worktrees/officeqa-experiment && \
for L in q3-smoke q3-full; do \
  .venv/bin/python experiments/officeqa_structured/run_episodes.py freeze --run-label $L && \
  .venv/bin/python experiments/officeqa_structured/run_episodes.py score --run-label $L; \
done
```

- `freeze` 把两组每题「截止前最新已接纳答案」字节封存进 `evaluator-only/frozen/<label>/`（0600），
  之后才允许 `score` 读取 gold（v2 主集 + v1 开发池都在 evaluator-only）并调用官方 scorer
  （blob `45a22db4…`，容差 0.0）。
- 输出：`evaluator-only/scores/<label>/scores.jsonl` 与 `summary.json`（含 A/B 正确数、弃答/格式错/缺失、
  配对差）。开发 pilot 分数只作接入诊断，不构成泛化结论。
- 未跑 freeze 直接 score 会被拒绝；答案字节与冻结哈希不符同样失败关闭。

## 失败与边界情况

- 任一 episode 崩溃：已接纳提交保留在 submit/ 与 episode_record 中（error 字段记录原因），评分回退到
  此前已接纳答案或计缺失——都在分母。
- run label 已存在会拒跑；换 label 前记得旧的属于已花费的真实 episodes，不要重复同题加跑。
- config 冻结校验失败会列出全部问题字段并拒跑；修复需走新的冻结（协议 §9.4：故障修复后新实验 ID）。
