# MABW 黄金路径

这是一份给写作者看的最短操作路径。它不解释所有控制面，只回答一件事：从零开始，按什么顺序把一份简报交付出去。

## 0. 开始前

确认你正在 Claude Code 的 MABW 项目里，并且 `/mabw` 命令可用。

先在仓库目录确认你实际调用到的是当前版本：

```bash
which multi-agent-brief
multi-agent-brief version
```

如果版本不是当前仓库声明的版本，先刷新安装入口。开发 checkout 中可用：

```bash
bash scripts/setup.sh
source .venv/bin/activate
multi-agent-brief version
```

如果 `/mabw` 不可用，先在仓库目录运行：

```bash
multi-agent-brief claude install --repo-workdir .
```

## 首次运行变体：本地材料，零 API key

如果你只是想用自己的几份材料跑一遍，不要先配置搜索后端。先走最小路径：

1. 用 `/mabw new` 创建 workspace。
2. 把少量已整理好的本地文本材料放进 `input/sources/`。
3. 用 `/mabw run <workspace>` 生成 handoff。
4. 用 `/generate-brief <workspace>` 执行 delegated workflow。
5. 用 `/mabw status <workspace>` 看哪里被拦住。
6. 用 `/mabw deliver <workspace>` 交付。

第一次建议只放 3-5 份 Markdown 或纯文本材料。PDF / DOCX 如果不能被当前输入治理路径直接读取，先转成文本再放入 `input/sources/`。不要为了快而绕过 Claim Ledger、gates 或 reader-final gate。

## 1. `/mabw new`

用它创建一份新简报工作区。

你需要回答几类问题：

- 写给谁；
- 本期范围是什么；
- 重点关注什么；
- 输出想要什么形态。

它会创建 workspace、配置基础文件，并准备本期运行交接单。它不会生成简报，也不会自动批准任何偏好。

## 2. `/mabw run <workspace>`

用它创建或刷新本期 runtime handoff。

这一步会准备运行时需要看的控制面和交接单，但不会替你跑完整 pipeline，也不会绕过阶段完成事务。

如果要执行完整 delegated subagent workflow，按 handoff 提示使用：

```text
/generate-brief <workspace>
```

## 3. 中途随时 `/mabw status <workspace>`

`status` 是只读的。它只回答四件事：

- 本期写到哪了；
- 来源相关 surface 是否已出现或可能过期；具体数字要去 Claim Ledger / 来源附录 / 审计记录里查；
- 哪些读者偏好已经在本 run 冻结；
- 交付前有哪些门禁或反馈还在拦。

如果它说状态可能过期，不要把这当成失败。按它提示的显式命令刷新控制记录。

## 4. 被拦住时怎么办

先运行：

```text
/mabw status <workspace>
```

看清楚拦住的是哪一类：

| 类型 | 怎么处理 |
|---|---|
| 缺产物 | 让对应阶段继续执行，或按 handoff 重新跑该阶段。 |
| 事实/来源问题 | 走 feedback / repair / audit 路径，不要记成长期偏好。 |
| 固定格式问题 | 先作为反馈记录；反复出现时应升级成模板或交付标准。 |
| 读者偏好 | 由人写成 guidance，进入 Improvement Ledger proposal，再由人批准。 |
| 已由系统执行 | 查看它指向的 gate/report，不要重复写进 memory。 |

## 5. `/mabw feedback <workspace> "..."`

读草稿时，不顺眼的地方直接用人话说。

例子：

```text
/mabw feedback <workspace> "这一段太像新闻摘要了，先说对我们公司的影响。"
```

它会先记录反馈。后续处置必须再确认：

- 本期修复；
- 生成 repair plan；
- 标记 issue resolved；
- 提成长期读者偏好；
- 批准或撤销 Improvement Ledger 条目。

事实问题不会被记成长期偏好。固定格式应该升级为模板或交付标准，而不是长期停在 memory 里。

## 6. 已批准偏好什么时候生效

批准一条 Improvement Ledger guidance 不会改变已经创建的当前 run snapshot。

它只会在下一次：

```text
/mabw run <workspace>
```

或等价的 `run` / `start` / `handoff` 中被冻结为新的 `output/intermediate/improvement_memory_snapshot.md`。

一句话：它会观察、会提议；但只有你点头的，才会被记住，而且记在一本你随时能翻、能撤销的账上。

## 7. `/mabw deliver <workspace>`

用它交付最终读者文件。

它必须经过：

- quality gates；
- reader-final gate；
- `state finalize-complete`。

通过后才把 `output/delivery/` 里的 reader-facing artifacts 当成交付结果：

- `output/delivery/brief.md`
- `output/delivery/<命名周报>.docx`

审计追溯文件继续保留，但不要当作交付给读者的文件：

- `output/intermediate/claim_ledger.json`
- `output/intermediate/audit_report.json`
- `output/source_appendix.md`

如果 reader-final gate 失败，不要手工搬走坏文件。看 `output/intermediate/finalize_report.json`，先处理残留的内部 ID、路径、空来源行或流程痕迹。

## 下一次真实周报测试要求

下一份真实周报只能照这份黄金路径操作；中途每一次“该按哪个键”的困惑都要记录为文档缺陷。
