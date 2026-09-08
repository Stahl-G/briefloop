# BriefLoop

一个面向 macOS 的个人本地简报工具：从来源生成简报，独立评分，直接改稿，再从修订中整理经验、改进后续任务。

## 启动

只需要 clone **BriefLoop 这一个仓库**，不需要再 clone WikiSkill，不需要手动安装它，也不需要构建前端。

```sh
cd briefloop
./start.sh
```

首次启动自动建立 `.venv`、安装随仓库提供的 WikiSkill wheel 和其他 Python 依赖、启动本地服务并打开浏览器。后续再次执行会复用环境和已有服务。

前提是本机有 Python 3.11+；生成简报使用已安装、登录且支持原生子 agent 的 Codex CLI，沿用你的模型配置，不修改全局认证设置。首次安装普通依赖需要网络。

可选：`./start.sh --workspace /path/to/workspace --no-open`。端口默认自动选择；指定 `--port 8765` 可固定端口。关闭网页不停止后台任务。

## 使用

1. 在“需求与来源”填写用途、读者、篇幅和时间范围，上传材料。需要时开启联网补充。
2. 生成后直接看稿；Scorer 独立完成四项评价，问题可查看来源。低分不隐藏稿件。
3. 直接编辑正文和表格，自动保存修订并保留原稿。也可留下评论。
4. 在反馈面板调整“WikiSkill 最多演化轮数 K”，默认 1。更多轮数可能消耗更多 Token，未必更好。
5. 开启自动学习后，服务端在编辑静默约 30 秒后合并反馈；也可立即处理。当前学习期间的新反馈进入下一批。
6. Wiki 页面查看经验与技能版本；学习任务可看比较、停止和恢复。候选更好则自动用于下一轮，随时回退。

试验生成稿不混入正常稿件列表；可以在“查看比较”中读到旧稿与候选稿。评分只对应其具体版本，用户改稿后不沿用旧分数。

## 状态和恢复

```sh
.venv/bin/briefloop status --workspace ./workspaces/default
.venv/bin/briefloop doctor --workspace ./workspaces/default
```

服务日志在工作区 `server.log`。每项任务的 `jobs/<id>/` 保存实际 prompt、native agent 记录、输出和使用量。失败或中断后从页面恢复，复用已完成产物；评分失败可以只重新评分。服务/电脑停止时运行会停止或等待恢复，系统不会声称离线仍在执行。

来源和稿件在 SQLite/本地文件中保存，请备份整个工作区。运行数据、凭据和私有计划不进 Git。仅监听本机 loopback；不是多用户托管服务。

## WikiSkill 与扩展

普通用户只使用这一个 BriefLoop 仓库。`vendor/wheels` 随项目提供 WikiSkill 的已构建依赖包，启动时自动安装。WikiSkill 的开发源码另行维护，BriefLoop 不包含第二套源码 checkout；反馈学习扩展归该依赖，应用只保留适配代码。保留原数值评分流程及历史研究结果。

默认可演化角色为 Scout、Analyst。`briefloop.skills.register_target(store, role_id, instruction)` 可注册其他角色；同一技能绑定、任务注入和反馈学习路径会携带这些角色。Scorer 的评价规则保持独立，不能用自己改过的标准证明自己变好。

## 开发

网页编辑器资源已打包。改前端时：

```sh
npm ci
npm run build
.venv/bin/python -m unittest discover -s tests -v
```

只保留核心行为测试，不移植旧项目的千级测试，也不把模拟评分当作实际改进。

本地核验已完成一次真实闭环：Scout → Analyst → Scorer → 网页改稿 → Maintainer → Proposer → 候选执行与独立比较。合成案例的结果为平局，因此保留基础技能并保留 Wiki；没有把更高的单次评分当作已证明的改善。三个核心行为测试及单仓库启动检查通过。该样例不代表跨任务效果或统计结论。
