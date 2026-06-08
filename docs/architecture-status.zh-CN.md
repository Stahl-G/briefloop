# 当前架构状态

本页用于区分当前实现状态和 roadmap 目标。按 roadmap 做开发前，先读这里。

## 已实现的公开基线

- 标准用户路径是 subagent-first。
- `multi-agent-brief run` 生成 runtime handoff artifacts，而不是自己生成完整 brief。
- Python 命令负责 setup、source tooling、validation、audit support 和 rendering。
- Hermes、Claude Code、Codex、OpenCode 和 manual fallback 都是 agent runtime surfaces。
- Input governance 已区分 evidence、feedback、instructions 和 background context。
- 旧 Python-pipeline 叙事不再是标准 workflow。

## Roadmap 目标

roadmap 中提到的概念不一定已经实现。除非代码、测试和 support matrix 已确认，否则都按目标处理：

- Orchestrator contracts
- runtime state and artifact validation
- evidence and execution provenance
- quality evaluation and feedback loops
- policy packs
- public-safe reference workflows

## Experimental 或有限能力

标记为 experimental、interface-only 或 CLI-only 的能力，不应被当成稳定用户承诺。使用前先查 support matrix 和 CLI 输出。

## Contributor 规则

roadmap 方向不等于已实现代码。实现 roadmap item 前，先确认当前代码路径、对应 validator 或 test，以及该能力属于 public、experimental 还是 internal planning。
