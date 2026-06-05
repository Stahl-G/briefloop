# v0.4.0 Implementation Plan — Capability Center & Guided Setup

## PR Roadmap

### PR 1: .env.example sync + doctor available-but-unconfigured hints ✅

- [x] Update root `.env.example` to match wizard-generated version (7 API keys)
- [x] Doctor `_add_available_info()` already existed — added tests
- [x] 615 tests pass

### PR 2: Capability Registry Foundation ✅

- [x] `src/multi_agent_brief/capabilities/` package (models, catalog, detect)
- [x] 15 capabilities registered with i18n names (en/zh)
- [x] `scripts/check_capabilities.py` CI gate
- [x] 24 capability tests + CI checks green

### PR 3: `features` command (Capability Center)
### PR 4: `recommend` engine + `setup` command + init integration
### PR 5: Doctor integration + auto docs + CHANGELOG
