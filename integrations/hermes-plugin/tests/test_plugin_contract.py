import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mabw import schemas, tools  # noqa: E402


def test_schemas_have_specific_descriptions():
    for schema in [
        schemas.MABW_CREATE_ONBOARDING,
        schemas.MABW_INIT_WORKSPACE,
        schemas.MABW_RUN_HANDOFF,
    ]:
        assert schema["name"].startswith("mabw_")
        assert "description" in schema
        assert len(schema["description"]) > 40
        assert "parameters" in schema


def test_create_onboarding_writes_json(tmp_path):
    result = json.loads(tools.create_onboarding({
        "workspace": str(tmp_path / "workspace"),
        "profile": {
            "company_or_org": "阿特斯",
            "industry_or_theme": "光伏和储能",
            "task_objective": "美国光储行业简报",
            "language": "中文",
            "web_search_mode": "runtime_websearch",
        },
    }))

    assert result["ok"] is True
    onboarding_path = Path(result["onboarding_path"])
    assert onboarding_path.exists()
    data = json.loads(onboarding_path.read_text(encoding="utf-8"))
    assert data["company_or_org"] == "阿特斯"
    assert data["audience"] == "management team"


def test_create_onboarding_requires_core_fields(tmp_path):
    result = json.loads(tools.create_onboarding({
        "workspace": str(tmp_path / "workspace"),
        "profile": {"company_or_org": "Only one field"},
    }))
    assert result["ok"] is False
    assert "industry_or_theme" in result["missing"]
    assert "task_objective" in result["missing"]


class FakeCtx:
    def __init__(self):
        self.tools = []
        self.commands = []
        self.skills = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs["name"])

    def register_command(self, name, handler, **kwargs):
        self.commands.append(name)

    def register_skill(self, name, path):
        self.skills.append((name, str(path)))


def test_plugin_registers_tools_command_and_skill():
    import mabw

    ctx = FakeCtx()
    mabw.register(ctx)

    assert set(ctx.tools) == {
        "mabw_create_onboarding",
        "mabw_init_workspace",
        "mabw_run_handoff",
    }
    assert "mabw" in ctx.commands
    assert ctx.skills and ctx.skills[0][0] == "mabw-workflow"
