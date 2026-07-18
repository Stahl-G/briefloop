# Feishu/Lark Integration

BriefLoop supports bidirectional integration with Feishu (飞书)
through the official [lark-cli](https://github.com/larksuite/cli) tool.

## Prerequisites

Install and authenticate lark-cli:

```bash
# Install (one-time)
npx @larksuite/cli@latest install

# Configure app credentials
lark-cli config init

# Log in with recommended scopes
lark-cli auth login --recommend

# Verify
lark-cli auth status
```

## Feishu as Source (Input)

Configure in `sources.yaml`:

```yaml
source_strategy:
  enabled_providers:
    - feishu

feishu:
  enabled: true
  sources:
    - name: "weekly-meeting"
      token: "..."           # from feishu doc URL
      type: doc              # doc | minutes | base | sheet | agenda | approval
```

Supported source types:

| Type | Source | lark-cli command |
|------|--------|-----------------|
| `doc` | Feishu Document | `lark-cli markdown fetch --token <token>` |
| `minutes` | Meeting Minutes | `lark-cli minutes get --token <token>` |
| `base` | Base table records | `lark-cli base record list --base-token <token> --table-id <id>` |
| `sheet` | Spreadsheet values | `lark-cli sheets values read --spreadsheet-token <token>` |
| `agenda` | Calendar agenda | `lark-cli calendar +agenda` |
| `approval` | Approval tasks | `lark-cli approval tasks list` |

## Feishu as Delivery (Output)

The Feishu delivery connector sends generated briefs to Feishu.

Supported channels:

| Channel | Behavior | lark-cli command |
|---------|----------|-----------------|
| `chat` | Send brief as IM message | `lark-cli im +messages-send --chat-id <id> --text <content>` |
| `doc` | Create a Feishu document | `lark-cli docs +create --doc-format markdown --content <md>` |
| `drive` | Upload file to Drive | `lark-cli drive upload --file <path>` |

Usage:

```python
from multi_agent_brief.delivery.feishu import FeishuDeliveryConnector
from multi_agent_brief.delivery.base import DeliveryArtifact, DeliveryTarget

connector = FeishuDeliveryConnector()
artifact = DeliveryArtifact(path="output/delivery/brief.md", title="Weekly Brief")
target = DeliveryTarget(channel="chat", recipient="oc_xxxxxxxxxx")
result = connector.deliver(artifact, target)
```

## Security Notes

- Tokens and credentials are stored in lark-cli's OS keychain, not in config files.
- The `lark-cli auth` session is managed separately from BriefLoop.
- All lark-cli commands run as subprocesses with timeouts.
- No network calls are made from Python — lark-cli handles all API communication.
