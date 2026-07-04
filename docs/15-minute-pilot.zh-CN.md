# 15 分钟试用

如果你想先看 BriefLoop 能产生什么，再读架构文档，用这一页。

BriefLoop 帮你生成可以追问、可以复盘、可以修复、可以交接的简报包。它提供
可追溯性和过程问责，不是语义证明。

## BriefLoop 是什么

BriefLoop 是一个 source-first 的周期性业务简报工作流。它把简报背后的工作材料
保留下来，方便检查：

- 来源和来源标签；
- 已登记的 claims；
- 质量检查和 warning；
- 面向读者的最终交付稿。

最快的试用方式是本地 deterministic demo。它只使用 public-safe 示例 artifacts，
不需要 API key。

## BriefLoop 不是什么

BriefLoop 不是：

- 语义证明引擎；
- 自动事实证明器；
- 人类审核的替代品；
- 自动发布或交付批准系统；
- 输出质量提升的证明。

demo 展示 artifact chain。它不证明真实报告已经可以发送。

## 运行本地 demo

从 fresh checkout 开始：

```bash
git clone https://github.com/Stahl-G/briefloop.git
cd briefloop
bash scripts/setup.sh
source .venv/bin/activate
bash scripts/demo.sh
```

这个 demo 是 deterministic 的。它不会调用 LLM，不会抓取来源，也不需要
`OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 或其他模型凭证。

Windows 用户先按 [`getting-started.md`](getting-started.md) 的 PowerShell
流程完成 setup，然后运行：

```powershell
python scripts/demo.py
```

## 先看这三个文件

demo 会打印 workspace 路径。先打开这三个文件：

| 文件 | 为什么重要 |
|---|---|
| `output/intermediate/quality_panel.html` | 静态 audit/operator 视图，展示 run status、warning 和 next actions |
| `output/intermediate/quality_summary.md` | 紧凑的人类可读质量摘要 |
| `output/intermediate/claim_ledger.json` | 机器可读的 claim 与 source metadata 记录 |

把这些文件当成 review surfaces，而不是发布权限。交付仍然是人工触发，并受 gate
约束。

更完整的首次使用路径见 [`getting-started.md`](getting-started.md)。
