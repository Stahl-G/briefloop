# BriefLoop 0.26.2

## 更新内容

本次集中改善 Windows 的文件处理、Office 导出与预览，并修复报告修订、宿主问答和子任务进度中的问题。面向用户的完整说明见 [变更记录](../CHANGELOG.md#0262)。

## 分阶段发布

- Windows x64：本次发布目标；仅在冻结工件验证、安装与保存重开检查完成后开放下载。
- Mac Apple Silicon：保持 0.26.1，0.26.2 安装包后续提供。本次代码包含更新检查规避 GitHub API 限流的改进，尚不能视为 Mac 包已交付。
- 共享 Python wheel 和源码包：从同一冻结提交生成并随 GitHub Release 保存，供后续 Mac 与 Python 渠道复用；不得重建为同版本的不同工件。

## 验证与限制

集成提交通过全部 8 项跨平台 CI，Linux Python 1068 项通过、Windows 安装后 140 项通过；Pi 编辑器、权限回答竞争与子任务终态问题均有修前失败、修后通过的检查。发行工件的实际版本、哈希及本机检查另存于随包的 release-manifest.json 和 windows-validation.json。

Windows 包尚未签名；首次准备仍需本机 Python 3.11 或更高版本以及网络下载依赖。没有把代码测试当作真实模型质量或 Mac 原生验收。
