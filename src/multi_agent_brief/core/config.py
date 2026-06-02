from __future__ import annotations

from pathlib import Path
from typing import Any

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
    data["_config_dir"] = str(config_path.parent)
    return data


def build_run_settings(
    *,
    config: dict[str, Any] | None,
    input_dir: str | None,
    output_dir: str | None,
    name: str | None,
    language: str | None,
    audience: str | None,
) -> dict[str, str]:
    config = config or {}
    project = config.get("project", {}) or {}
    report = config.get("report", {}) or {}
    previous = config.get("previous", {}) or {}
    selection = config.get("selection", {}) or {}
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

    return {
        "project_name": name or project.get("name") or "Weekly Intelligence Brief",
        "input_dir": str(input_path),
        "output_dir": str(output_path),
        "language": language or project.get("language") or "en-US",
        "audience": audience or project.get("audience") or "management",
        "report_date": str(report.get("date", "")),
        "max_source_age_days": int(report["max_source_age_days"]) if report.get("max_source_age_days") else None,
        "fail_on_stale_source": bool(report.get("fail_on_stale_source", False)),
        "previous_report_dir": str(previous.get("path", "")),
        "max_claims": int(selection.get("max_claims", 160)),
        "quiet_week_min_claims": int(selection.get("quiet_week_min_claims", 5)),
    }
