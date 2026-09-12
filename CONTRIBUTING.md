# 贡献与反馈

普通问题请使用仓库的 Bug report 模板，附最小复现。优先描述丢稿、版本混用、错误放行、停止失效等用户行为，不必提供整份工作区。

## 可复现的信息

1. `briefloop --version`、操作系统版本、所选宿主名称和 CLI 版本。
2. 从空工作区开始的操作步骤，预期结果、实际结果和错误发生时间。
3. 能触发问题的合成材料；来源、正文和截图都应先去除真实客户与内部信息。
4. 如需日志，只截取错误前后必要片段。`server.log`、任务公开日志、截图与审计包可能包含材料正文、路径、提示词、网址和账号信息。手动检查并删去 Key、Authorization、Cookie、会话令牌、私有 endpoint、个人路径等内容；自动脱敏不是完整保证。

不要上传 `.env`、宿主认证文件、完整数据库、工作区压缩包或未经检查的 `briefloop status` 输出。安全问题不要在公开 Issue 中附利用细节或敏感样本，见 [SECURITY.md](SECURITY.md)。

## 开发检查

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]' build
npm ci
node runtime-bridge/build.mjs
npm run build
npm test
node --test runtime-bridge/bridge.test.mjs
.venv/bin/python -m pytest -q
.venv/bin/python -m build
.venv/bin/python tests/check_distribution.py dist/*.whl dist/*.tar.gz
```

前端修改后同时提交生成的 `app.js` 和许可证清单；CI 会检查是否漂移。第三方代码保留原始许可和来源。新增依赖需要明确用途并检查发行包包含相应许可。

回归检查应保护真实行为，优先复用已有用例，不增加只锁文案或复制常量的测试。涉及保存、恢复、交付或隔离时，提供实际失败场景与修复证据。常规自动检查不调用模型、搜索服务，也不使用真实私有材料。

提交代码时请确认你有权贡献这些内容，并接受仓库 MIT 许可证。请勿提交凭据、运行数据、私有方案或第三方受限材料。
