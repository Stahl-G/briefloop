# v0.5.5 Hermes Adapter Plan

v0.5.5 定位为 Hermes 专属适配层。它不重写 MABW 主 pipeline，也不引入新的事实抽取逻辑，而是把 Hermes 的 cron、skill、fresh session 和消息交付能力接到现有 workspace / source provider / prepare / finalize 流程上。

## 设计判断

Hermes 适合做：

- 定时触发层：日报 scout、周报生成、月报生成。
- 轻量采集层：每天把公开、可引用信号写入 workspace cache。
- 消息交付层：把 cron 结果投递到 local、Feishu、Telegram 等 Hermes 已配置渠道。
- 历史自动化底座：保留已有 Hermes cron / gateway / profile / skills 资产。

MABW 继续负责：

- workspace 初始化与配置。
- source provider 归一化。
- Scout / Screener / Claim Ledger。
- deterministic audit、final quality、rendered output gate。
- Markdown / DOCX reader-facing artifact。

## 官方 Hermes 约束

v0.5.5 的实现遵循以下 Hermes 约束：

- Cron job 是 fresh agent session，不能假设继承交互上下文。
- Skill 必须显式附加到 cron job。
- 项目目录必须通过 `--workdir` 指定，否则不会加载 repo 的 `AGENTS.md` / `CLAUDE.md` / `.cursorrules`。
- CLI 支持 `hermes cron create <schedule> <prompt> --skill <skill> --workdir <path> --profile <profile> --name <name>`。
- Cron 可以交付到 local 或配置好的消息平台；CLI 默认 local。
- 频繁轮询后续可升级为 `wakeAgent` / script-only gate，但 v0.5.5 先不自动写 Hermes scripts。
- `context_from` 适合多阶段 cron chain，但 CLI 创建时通常需要先知道上游 job id；v0.5.5 先在 JSON plan 中保留关系，在 shell command 中保持可直接创建。

## 用户工作流

### 1. 生成 Hermes skill

```bash
multi-agent-brief hermes skill
```

默认输出：

```text
.agents/hermes-skills/multi-agent-brief-hermes/SKILL.md
```

安装方式：

- 复制到 `~/.hermes/skills/multi-agent-brief-hermes/SKILL.md`。
- 或在 `~/.hermes/config.yaml` 配置 `skills.external_dirs` 指向 repo 的 `.agents/hermes-skills`。

### 2. 让 workspace 消费 Hermes 日更 cache

```bash
multi-agent-brief hermes sync-sources --config <workspace>/config.yaml
```

该命令只做一件事：在 `<workspace>/sources.yaml` 中启用 `cached_package`，并加入：

```yaml
cached_package:
  enabled: true
  paths:
    - input/hermes_cache
  formats:
    - json
    - md
    - txt
```

### 3. 生成 cron plan

```bash
multi-agent-brief hermes cron-plan \
  --config <workspace>/config.yaml \
  --repo-workdir <repo-root> \
  --cadence weekly,monthly \
  --deliver feishu
```

默认输出：

```text
<workspace>/output/intermediate/hermes_cron_plan.json
```

当用户需要周报和月报时，计划会生成三类 job：

- Daily scout：每天采集公开信号，写入 `input/hermes_cache/YYYY-MM-DD.json`。
- Weekly brief：每周读取 daily cache 和 workspace sources，运行 doctor / prepare / finalize。
- Monthly brief：每月读取 daily cache 和 workspace sources，生成月度综合。

### 4. 生成 Hermes cron create 命令

```bash
multi-agent-brief hermes cron-commands \
  --config <workspace>/config.yaml \
  --repo-workdir <repo-root> \
  --cadence weekly,monthly
```

输出可复制的 `hermes cron create` 命令。命令会显式带：

- `--skill multi-agent-brief-hermes`
- `--workdir <repo-root>`
- `--name <job-name>`
- 可选 `--profile <existing-profile>`
- 可选 `--deliver <target>`

## Cron 分层

```text
Daily Hermes Scout
→ input/hermes_cache/YYYY-MM-DD.json
→ cached_package provider
→ MABW prepare
→ audit / final quality / rendered output gates
→ finalize
→ Hermes delivery
```

日报 job 只负责采集，不写最终报告。周报/月报 job 才负责调用 MABW pipeline。

## 新增代码

- `src/multi_agent_brief/hermes/adapter.py`
  - `HermesCronJob`
  - `HermesCronPlan`
  - `build_hermes_cron_plan`
  - `render_hermes_cron_commands`
  - `render_hermes_cron_markdown`
  - `render_hermes_skill`
  - `sync_cached_package_source`
- `multi-agent-brief hermes skill`
- `multi-agent-brief hermes sync-sources`
- `multi-agent-brief hermes cron-plan`
- `multi-agent-brief hermes cron-commands`
- `.agents/hermes-skills/multi-agent-brief-hermes/SKILL.md`
- `tests/test_hermes_adapter.py`

## 不做

- 不自动安装 Hermes。
- 不直接写 `~/.hermes/cron/jobs.json`。
- 不自动创建 Hermes profile。
- 不在 MABW 中实现 Hermes gateway。
- 不绕过 MABW doctor / prepare / finalize。
- 不把 daily scout 输出直接当成 reader-facing brief。
- 不把 Hermes cron job 失败伪装成 MABW 成功。

## 完成标准

- 可以从一个 workspace 生成 Hermes skill、cron plan 和 cron create commands。
- `sync-sources` 能把 Hermes daily cache 接进 `cached_package` provider。
- 当用户选择 weekly + monthly 时，自动生成 daily scout + weekly brief + monthly brief 三段计划。
- 所有 cron prompt 都包含 workspace、project、audience、language、cache directory。
- 所有 cron job 都显式附加 Hermes skill，并设置绝对 workdir。
- 测试覆盖 plan、skill、CLI 写文件和 sources.yaml 同步。
