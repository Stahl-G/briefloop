from __future__ import annotations

from pathlib import Path
from typing import Any

from multi_agent_brief.outputs.naming import DEFAULT_FILENAME_TEMPLATE

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - exercised when PyYAML is not installed
    yaml = None


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "false"}:
        return value == "true"
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def _minimal_yaml_load(text: str) -> dict[str, Any]:
    """Small fallback parser for the simple config files used by the MVP."""
    data: dict[str, Any] = {}
    current_section: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if indent == 0 and line.endswith(":"):
            section_name = line[:-1]
            current_section = {}
            data[section_name] = current_section
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        target = current_section if indent > 0 and current_section is not None else data
        target[key.strip()] = _parse_scalar(value)
    return data


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    config_text = config_path.read_text(encoding="utf-8")
    data = yaml.safe_load(config_text) if yaml is not None else _minimal_yaml_load(config_text)
    data = data or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {config_path}")
    data = normalize_language_config(data)
    data["_config_dir"] = str(config_path.parent)
    return data


def normalize_language_config(config: dict[str, Any]) -> dict[str, Any]:
    project = config.get("project", {}) or {}
    language_config = config.get("language", {}) or {}
    if not isinstance(language_config, dict):
        language_config = {}

    output_language = language_config.get("output") or project.get("language") or "zh-CN"
    interface_language = language_config.get("interface") or output_language
    source_handling = language_config.get("source_handling") or "preserve_original"

    config["language"] = {
        **language_config,
        "interface": interface_language,
        "output": output_language,
        "source_handling": source_handling,
    }
    return config


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def build_run_settings(
    *,
    config: dict[str, Any] | None,
    input_dir: str | None,
    output_dir: str | None,
    name: str | None,
    language: str | None,
    audience: str | None,
) -> dict[str, Any]:
    config = config or {}
    project = config.get("project", {}) or {}
    language_config = config.get("language", {}) or {}
    report = config.get("report", {}) or {}
    previous = config.get("previous", {}) or {}
    selection = config.get("selection", {}) or {}

    # Resolve report.date: "auto" → today's date
    raw_date = report.get("date", "")
    if raw_date == "auto":
        from datetime import date
        raw_date = date.today().isoformat()
    selector = config.get("selector", {}) or {}
    input_config = config.get("input", {}) or {}
    output_config = config.get("output", {}) or {}
    config_dir = Path(config.get("_config_dir", "."))

    resolved_input = input_dir or input_config.get("path")
    if not resolved_input:
        raise ValueError("Input directory is required. Pass input_dir or set input.path in config.")

    input_path = Path(str(resolved_input))
    if input_dir is None and not input_path.is_absolute():
        input_path = config_dir / input_path

    resolved_output = output_dir or output_config.get("path") or "output/demo"
    output_path = Path(str(resolved_output))
    if output_dir is None and output_config.get("path") and not output_path.is_absolute():
        output_path = config_dir / output_path

    project_name = name or project.get("name") or "Weekly Intelligence Brief"
    resolved_language = language or language_config.get("output") or project.get("language") or "zh-CN"
    resolved_audience = audience or project.get("audience") or "management"
    output_filename_template = (
        output_config.get("filename_template")
        or output_config.get("file_name_template")
        or output_config.get("name_template")
        or output_config.get("filename")
        or DEFAULT_FILENAME_TEMPLATE
    )
    output_filename_tokens = {
        "project_name": str(project_name),
        "title": str(project_name),
        "company": str(project.get("company", "")),
        "industry": str(project.get("industry", "")),
        "audience": str(resolved_audience),
        "cadence": str(report.get("cadence", "")),
        "report_date": str(raw_date),
        "date": str(raw_date),
        "language": str(resolved_language),
    }

    return {
        "project_name": project_name,
        "input_dir": str(input_path),
        "output_dir": str(output_path),
        "language": resolved_language,
        "audience": resolved_audience,
        "report_date": str(raw_date),
        "max_source_age_days": int(report["max_source_age_days"]) if "max_source_age_days" in report else None,
        "fail_on_stale_source": bool(report.get("fail_on_stale_source", False)),
        "previous_report_dir": str(previous.get("path", "")),
        "max_claims": (
            int(selector["max_items"]) if selector.get("max_items") is not None
            else int(selection.get("max_claims", 160))
        ),
        "quiet_week_min_claims": int(selection.get("quiet_week_min_claims", 5)),
        "output_formats": output_config.get("formats", ["markdown"]),
        "output_footer": output_config.get("footer", ""),
        "output_filename_template": str(output_filename_template),
        "output_filename_tokens": output_filename_tokens,
        "output_named_outputs": _as_bool(output_config.get("named_outputs"), True),
    }
