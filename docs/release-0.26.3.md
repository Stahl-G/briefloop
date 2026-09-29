# BriefLoop 0.26.3

## 统一发行

本版统一 Mac、Windows 和 Python/CLI 的代码与后端工件。包含 0.26.2 已合并的 Windows 长路径、Excel 工作表预览、修订绑定、原生权限问答、Pi 编辑回执及子任务进度修复，并把 Mac 更新检查切换到官方静态清单以减少 GitHub API 限流影响。

正式渠道只有在各自工件和原生验收完成后才能声明已发布。准备提交、CI、草稿发行和本地验收均不等于正式发行；随包验收记录记载实际范围与未测项。

## 打包与交接约束

1. `pyproject.toml` 是唯一版本输入。运行 `python scripts/sync_versions.py` 同步镜像；预发行使用 `0.26.4rc1` / `0.26.4.dev1` 等 PEP 440 编号，脚本转换为 Electron 的 `0.26.4-rc.1` / `0.26.4-dev.1`。候选不得冒用稳定版本编号。
2. 准备好代码、版本、文档和构建输出，通过必要检查后冻结干净提交。发行协调者仅构建一次共享后端：

   `python desktop/electron/scripts/prepare-backend.py --release-commit FULL_SHA --artifact-dir PRIVATE_REGISTRY`

   每个版本在 registry 中保留构建身份和 wheel。再次调用只允许复用同一身份及原有字节；不能覆盖不同提交、不同哈希的同版本工件。中断记录必须保留检查。
3. 其他平台从同一草稿发行下载共享 wheel，再执行：

   `python desktop/electron/scripts/prepare-backend.py --release-commit FULL_SHA --wheel WHEEL_PATH --sha256 SHARED_SHA256`

   校验元数据、包内文件与冻结源码、哈希，再装配后端。Mac 和 Windows 的打包前钩子同时核对 App/后端版本、哈希、冻结提交和工作区状态。Windows checkout 使用 `core.autocrlf=false`，保留共享工件的源码字节。
4. 正式冻结工件先在隔离验收环境验证，不把未发布包替换进用户日常安装。两端验收通过后形成完整静态清单；GitHub 与 PyPI 上传已验证的相同工件，不重新打包。
5. 发布后核对实际渠道，再安全替换本机 App 和共用 CLI。保留用户工作区、旧包与回退路径；核对 active 后端和实际服务 build，不只检查 App 标签。

## 验收要求

- Mac 与 Windows：启动、准备/复用后端、已有工作区保存重开、Word/Excel 导出、原生权限目录和交互问答、更新通道。
- 每平台记录实际 App、CLI、active 后端的版本、共享 wheel SHA-256、冻结提交及未测范围。
- 汇总执行 `scripts/check_versions.py`，发布后核对 PyPI wheel 哈希及下载页链接。
- 发布记录如实列出签名/公证状态；没有签名证据不得宣称已签名。模型质量与新模型调用不属于本次打包验收。
