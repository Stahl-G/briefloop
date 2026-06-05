"""Analysis Modules — pluggable post-Screener analysis pipeline extensions.

Each module consumes a screened ClaimLedger and produces structured artifacts
(AnalysisCards, events, coverage reports, etc.) before the Analyst agent
writes the final brief.  Modules are registered in MODULE_REGISTRY and enabled
via config.yaml's ``modules`` section.
"""

