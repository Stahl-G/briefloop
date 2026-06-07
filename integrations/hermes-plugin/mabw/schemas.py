"""Tool schemas for the Hermes MABW plugin."""

MABW_CREATE_ONBOARDING = {
    "name": "mabw_create_onboarding",
    "description": (
        "Create onboarding.json for a Multi-Agent Brief Workflow workspace "
        "from brief profile answers collected in chat. Use this before initializing "
        "a real MABW workspace in Hermes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace directory to create or use, e.g. /Users/name/mabw-workspace.",
            },
            "profile": {
                "type": "object",
                "description": "Brief profile collected in chat.",
                "properties": {
                    "company_or_org": {"type": "string"},
                    "industry_or_theme": {"type": "string"},
                    "task_objective": {"type": "string"},
                    "audience": {"type": "string"},
                    "language": {"type": "string"},
                    "cadence": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
                    "source_style": {
                        "type": "string",
                        "enum": ["official only", "reliable research", "broad scan"],
                    },
                    "output_style": {"type": "string"},
                    "must_watch": {"type": "array", "items": {"type": "string"}},
                    "forbidden_sources": {"type": "array", "items": {"type": "string"}},
                    "web_search_mode": {
                        "type": "string",
                        "enum": ["local_only", "runtime_websearch", "external_api", "configure_later"],
                    },
                },
                "required": ["company_or_org", "industry_or_theme", "task_objective"],
            },
            "onboarding_filename": {
                "type": "string",
                "description": "Optional filename for the onboarding JSON. Defaults to onboarding.json.",
            },
        },
        "required": ["workspace", "profile"],
    },
}

MABW_INIT_WORKSPACE = {
    "name": "mabw_init_workspace",
    "description": (
        "Initialize a MABW workspace from onboarding.json by running "
        "multi-agent-brief init <workspace> --from-onboarding <file>."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace directory to initialize.",
            },
            "onboarding_path": {
                "type": "string",
                "description": "Path to onboarding.json created from chat-collected brief profile.",
            },
        },
        "required": ["workspace", "onboarding_path"],
    },
}

MABW_RUN_HANDOFF = {
    "name": "mabw_run_handoff",
    "description": (
        "Run the MABW runtime handoff launcher for an initialized workspace. "
        "Use this after workspace initialization to create agent_handoff.md and agent_handoff.json."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Initialized MABW workspace directory.",
            },
            "runtime": {
                "type": "string",
                "description": "Optional runtime target. Defaults to hermes.",
                "enum": ["auto", "hermes", "claude", "opencode", "codex", "manual"],
            },
        },
        "required": ["workspace"],
    },
}
