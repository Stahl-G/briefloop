# OpenCLI Source Provider

`opencli` lets the pipeline collect user-authorized browser/session signals through local OpenCLI adapters, then routes the results through the normal source workflow:

```text
OpenCLI adapter output -> SourceItem -> Scout -> Screener -> Claim Ledger -> Analyst
```

This provider is intended for private or login-state sources such as Bilibili, YouTube, Zhihu, Xiaohongshu, Reddit, or Twitter/X, when the user has authorized local access. It does not bypass source screening, claim ledger creation, or audit gates.

## Configuration

```yaml
source_strategy:
  profile: aggressive_signal
  enabled_providers:
    - manual
    - opencli

opencli:
  enabled: true
  timeout: 60
  commands:
    - name: zhihu-hot
      site: zhihu
      command: hot
      args: ["--limit", "5"]
      reliability: medium

    - name: youtube-search
      site: youtube
      command: search
      query_from_keywords: true
      args: ["--limit", "5"]
      reliability: medium
```

The provider appends `-f json` automatically unless the command already specifies `-f` or `--format`.

## Guardrails

- Only read-only commands are allowed by default.
- Write-like commands such as `like`, `comment`, `follow`, `subscribe`, `post`, and `delete` are blocked.
- Failed OpenCLI commands become low-reliability error items and are filtered out by the registry.
- Private/session output is treated as signal evidence, not verified fact.
- Do not store credentials, cookies, raw logs, or personal data in workspace files.

Run:

```bash
briefloop doctor --config path/to/workspace/config.yaml
```

The doctor check validates whether `opencli` is installed and whether configured commands are allowed.
