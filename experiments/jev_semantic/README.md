# 报告语义判断隔离实验

这是已有本地实验代码的整理与协议补全，未接入 BriefLoop 的正式交付、评分或学习控制。沿用原来的只读导出、分组拆分、Jev / LLM 选择对照、概率变体、引文年份前置过滤和本地盲标页；治理入口收敛到 `semantic_experiment.py`。项目 MIT 许可证适用。旧 Phase 0 的个人路径脚本没有迁入本目录，原文件与历史实验应单独保留。

先读 [POLICY.md](POLICY.md)。所有运行数据、输入、请求、标签与结果存到仓库外。以下 `$DATASET`、`$DEV_RUN` 等是调用者自己选择的本地路径，不是产品内部控制文件。

```sh
PYTHONPATH=src python experiments/jev_semantic/semantic_experiment.py --help
PYTHONPATH=src python experiments/jev_semantic/semantic_experiment.py list --workspace "$WORKSPACE"
PYTHONPATH=src python experiments/jev_semantic/semantic_experiment.py export \
  --workspace "$WORKSPACE" --version "$BRIEF_VERSION" --out "$DATASET"
PYTHONPATH=src python experiments/jev_semantic/semantic_experiment.py run \
  --dataset "$DATASET" --out "$DEV_RUN" --split dev
```

默认是离线规则，不会调用模型。导出默认标记 private，必须明确选择具体稿件版本；原工作区以 SQLite URI 只读打开。`demo --out "$DATASET"` 可以生成明确标识的合成操作样例，不带人工标签。完整输入超过边界时保留跳过记录，不截断数据。分组划分的样本数取决于报告/来源组，不能为获得更好的分数反复换 seed。

## 受控模型对照

仅在获得材料外发和实际调用授权之后使用模型路径：

```sh
PYTHONPATH=src python experiments/jev_semantic/semantic_experiment.py run \
  --dataset "$DATASET" --out "$CHOICE_DEV" --split dev --provider llm \
  --model "$MODEL_REVISION" --endpoint "$LLM_ENDPOINT" --key-env SEMANTIC_LLM_API_KEY \
  --allow-network
# 概率变体使用独立输出目录和同一请求模型，另加 --with-probabilities。
# Jev 使用 --provider jev；其原有接口始终要求返回概率分布。
# 私有材料另需 --allow-private-external；不默认继承其他宿主的登录或额度。
```

密钥仅由指定环境变量传入，不放到参数、数据集或 Git。默认模型别名不是固定模型修订号；运行保留请求模型和实际返回模型、原请求、公开回答字段和数值用量；响应经过公共字段投影，不保存隐藏推理，不称为完整原始返回。现有代码支持这些接口形状，不等于某提供方当前服务已连通。请先做单独获批探测。

`--prefilter` 开启原有保守数字定位过滤：明确引文年份/页行编号不进模型。事件年份、版本/产品编号及无明确引用语境的标题年份仍需判断；带年份的正文前后恰好各有一个书名号引用，不能据此把事件年份过滤掉。前置过滤结果单列，不计为模型准确率。

## 标注、冻结和留出

```sh
# 人工盲标；页面本身不自动产生真值。
PYTHONPATH=src python experiments/jev_semantic/semantic_experiment.py serve \
  --dataset "$DATASET" --annotations "$HUMAN_LABELS" --mode label
# 各对照臂先完成 dev 和标签；没有标签就停止质量结论。
PYTHONPATH=src python experiments/jev_semantic/semantic_experiment.py evaluate \
  --dataset "$DATASET" --run "$CHOICE_DEV" --run "$PROB_DEV" \
  --annotations "$HUMAN_LABELS" --out "$DEV_COMPARISON"
PYTHONPATH=src python experiments/jev_semantic/semantic_experiment.py freeze-policy \
  --dataset "$DATASET" --run "$PROB_DEV" --labels "$HUMAN_LABELS"
```

最后一个命令输出登记的策略文件路径。**所有计划中的臂先冻结完，才开始任何 validation**。`--label-source construction` 只允许合成数字定位标签；材料性和旧结论更新不能用它绕过人工标注。

冻结后 `run --split validation --policy "$FROZEN_POLICY"` 使用与 dev 相同的 provider/model/endpoint/概率选项和执行边界，只换 split 与输出路径。运行仍需原有授权选项。全部结果及标签检查完毕，调用：

```sh
PYTHONPATH=src python experiments/jev_semantic/semantic_experiment.py evaluate-policy \
  --dataset "$DATASET" --run "$VALIDATION_RUN" --policy "$FROZEN_POLICY" \
  --labels "$HUMAN_LABELS" --out "$VALIDATION_REPORT"
```

该评价只保存一次。随后才能按同一个策略执行/评价 `--split test`。不完整 validation 会保留原记录并继续封存 test。原输出目录可在冻结尝试上限内恢复；新目录、临时阈值或换模型不能获得第二次验证机会。代码文件改变也会改变协议身份，因此必须随实验保留冻结源码。

本地账本不是文件权限隔离：运行器会校验完整数据集的哈希，仅将所选 split 发送给模型；不提供物理防读或不可篡改存储。盲标人可以为留出样本标注，实验执行者不应借此挑选阈值。

## 当前验收边界

随代码提供的检查只使用合成离线夹具，验证：只读导出、完整输入/身份、失败恢复、实际本地盲标提交、两种响应形状、阈值与模型绑定、防留出重复执行、test 顺序及无标签边界。

没有在本次整理中运行历史 validation/test、没有新增真实模型调用，也没有补造材料性/结论更新的人工标签。因此仍缺：实际概率变体的供应商对照和费用证据、自然材料上的阈值验证、实际人工标注、一次真实封存测试。不能因这些离线检查通过而关闭所有语义效果验收项。
