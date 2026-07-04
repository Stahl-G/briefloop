# BriefLoop 发布验证清单

这份清单用于正式推广前的最后两类人工验证：

1. 维护者本人按黄金路径跑一次真实周报。
2. 0 号试点从 fresh clone / fresh install 开始跑一次 demo 或真实简报。

它不是功能规格，也不是新的 runtime 行为。

## 1. 黄金路径自测

目标：验证 [BriefLoop 黄金路径](golden-path.zh-CN.md) 是否能让维护者在不翻代码、不靠记忆的情况下完成一次真实简报。

### 前提

- 只看 `docs/golden-path.zh-CN.md` 和它明确链接到的文档。
- 不打开 private planning。
- 不使用历史聊天记录补步骤。
- 不临时改代码。

### 操作

先确认全局命令入口和 Claude Code 命令已经指向当前 checkout，而不是旧安装：

```bash
which briefloop
briefloop version
briefloop claude install --repo-workdir .
```

如果 `briefloop version` 不是当前仓库版本，先修安装入口，再开始自测。不要在旧 CLI 上继续跑黄金路径。

记录一个新的测试目录：

```bash
BASE="$HOME/briefloop-runs/golden-path-self-test-$(date +%Y%m%d-%H%M)"
mkdir -p "$BASE"
```

建立 notes：

```bash
cat > "$BASE/golden_path_self_test_notes.md" <<'EOF'
# Golden Path Self-Test Notes

Date:
Repo commit:
Runtime:
Model:
Workspace:

## Step Log

## Confusions

## Commands That Were Not Obvious

## Places I Needed Non-Doc Memory

## Gate / Deliver Result

## Verdict

PASS / FAIL / PARTIAL
EOF
```

按黄金路径执行：

```text
/briefloop new
/briefloop run <workspace>
/briefloop status <workspace>
/briefloop feedback <workspace> "..."
/briefloop deliver <workspace>
```

每次出现“下一步该按哪个键”的犹豫，必须写进 `Confusions`。

### 通过标准

- 能不靠私有计划完成一次 run。
- 能解释 `/briefloop run` 只是创建或刷新 handoff，本身不完成 stage。
- `status` 没有写状态。
- `deliver` 经过 gates、reader-final gate 和 `finalize-complete`。
- 最终 reader output 没有内部 claim ID、流程词、本地路径或空 source 行。

### 失败处理

不要现场修流程。先把失败写进 notes。自测结束后再决定是文档补丁、CLI help 补丁，还是 runtime bug。

## 2. Fresh-Clone 外部验证

目标：验证一个没有上下文的试点用户能不能从仓库说明开始跑通 demo。

### 给试点用户的边界

试点用户只需要做两件事：

- 按 README / quickstart 安装并跑 demo。
- 记录卡住的位置。

不要求她理解 Improvement Ledger、control surfaces、policy packs 或内部架构。

### 建议发给试点用户的话

```text
我想请你帮我测一个开源简报工具的上手路径。

请从一个新的文件夹开始，只看 README 和 quickstart，不看我之前发你的聊天记录。
目标不是评价报告写得好不好，而是记录你在哪里卡住、哪一步不知道该按什么键。

如果跑不通，不要帮我修。把报错、截图、你当时期待发生什么发给我就行。
```

### 试点步骤

```bash
git clone https://github.com/Stahl-G/briefloop.git
cd briefloop
bash scripts/setup.sh
source .venv/bin/activate
which briefloop
briefloop version
python3 -m pytest -q tests/test_runtime_assets.py tests/test_subagent_first_contract.py tests/test_status_commands.py
briefloop init /tmp/briefloop-demo --demo --force
briefloop claude install --repo-workdir .
```

然后在 Claude Code 中尝试：

```text
/briefloop run /tmp/briefloop-demo
/briefloop status /tmp/briefloop-demo
/briefloop deliver /tmp/briefloop-demo
```

如果没有 Claude Code 环境，则只测 CLI demo：

```bash
briefloop doctor --config /tmp/briefloop-demo/config.yaml
briefloop run --workspace /tmp/briefloop-demo --skip-doctor
```

### 需要她反馈什么

```text
1. 哪一步第一次卡住？
2. 是命令找不到、环境装不上、文档看不懂，还是输出不符合预期？
3. README 里哪一句最有帮助？
4. README 里哪一句让你误解？
5. 如果只给 15 分钟，你会不会继续试？
```

### 通过标准

- fresh clone 能完成 setup。
- demo workspace 能初始化。
- 至少能生成 runtime handoff。
- 如果有 Claude Code，能看到 `/briefloop` 五动词。
- 卡点可以归类为文档、环境、runtime、模型或产品理解问题。

## 3. 发布前泄漏扫描

目标：在公开发布、公开 reference pack、或外部试点材料发出前，先用本地私有词和路径做一次可重复扫描。

先扫仓库 tracked files：

```bash
MABW_PUBLIC_SAFETY_BANNED_TERMS="<local private terms>" \
  python3 scripts/check_public_safety.py
```

再扫候选公开 workspace、reference pack 或 demo bundle：

```bash
MABW_PUBLIC_SAFETY_BANNED_TERMS="<local private terms>" \
  python3 scripts/check_public_safety.py --path <candidate-reference-workspace-or-pack>
```

`<local private terms>` 不写进仓库。它应该包含本机用户名、真实公司名、内部项目名、本地绝对路径片段、私有聊天或云文档 token 片段等。

任何真实公司名、用户名、本地绝对路径、聊天/飞书 token、私有扫描词命中，都必须先解释、移除或确认仅存在于本地不发布材料中。

## 4. 发布前记录

把两次验证结果整理成一段 release note 内部记录：

```text
Golden-path self-test: PASS / FAIL / PARTIAL
Fresh-clone pilot: PASS / FAIL / PARTIAL
Public-safety scan: PASS / FAIL / PARTIAL
Top friction:
Release doc changes made:
Known limitations left for next release:
```

只有当这两项都有记录时，才开始更大范围地转发链接或邀请试点。
