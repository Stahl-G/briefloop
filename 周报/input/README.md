# 输入文件夹

请把公开或合成来源文件放在这里。

MVP 支持的格式：

- `.md`
- `.txt`
- `.json`

不要把机密公司文件、内部报告、私有消息、凭据、原始日志或重大非公开信息放入此文件夹，除非该文件夹只保存在本地且已从 Git 中排除。

推荐 JSON 格式：

```json
{
  "source_url": "https://example.com/source",
  "published_at": "2026-06-02",
  "source_tier": "industry_media",
  "items": [
    "Example source-backed statement."
  ]
}
```
