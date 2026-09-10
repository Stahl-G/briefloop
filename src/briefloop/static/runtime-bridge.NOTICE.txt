Open Design https://github.com/nexu-io/open-design
Upstream commit: be0887b39273993d939e83c4768922115f104bed
Apache-2.0. Files copied unmodified except core/index.ts is a minimal export.
Runtime definitions in runtime-bridge/catalog.json extracted from upstream defs. Lifecycle glue in runtime-bridge is BriefLoop code; no Open Design design prompts, auto approval, or analytics included.

BYOK source mapping (reference originals, not bundled):
- byok-reference/byok-opencode.ts ← apps/daemon/src/runtimes/byok-opencode.ts
- byok-reference/provider-models.ts ← apps/daemon/src/integrations/provider-models.ts
BriefLoop's backends/opencode_server.py adapts the SDK package mapping and catalog
status classification. Local changes: protocol is explicit (including Responses
on custom hosts), no guessed context/output limits, no permission bypass, no
new inference loop. Native OpenCode auth storage is reused instead of placing
keys in runtime task payloads. Catalog probes do not follow credential-bearing
redirects or return upstream error bodies. Short tool tests use native restricted
read permissions and the existing ChatStore lifecycle.

Model directory reuse:
- runtime-models/models.ts ← runtimes/models.ts (unchanged; model helpers only).
- runtime-models/mmd-routes.ts ← runtimes/mmd-routes.ts (unchanged).
- runtime-models/codex-models.ts ← parser functions/constants preceding
  GPT_5_5_SERVICE_TIER_OPTIONS in runtimes/defs/codex.ts; imports narrowed.
- runtime-models/opencode-models.ts ← parser functions/constants before
  opencodeAgentDef in runtimes/defs/opencode.ts; imports narrowed.
- runtime-models/fallbacks.json ← id/label entries from the five upstream
  fallbackModels definitions (Claude, Codex, Kimi, Hermes, OpenCode).
Bridge uses upstream detectAcpModels, parseCodexDebugModels,
parseOpenCodeModels and local route discovery. Fallbacks are labeled hints,
not verified account availability. User-entered model IDs remain accepted.
No launch permission bypass or design-prompt code is used by these imports.
