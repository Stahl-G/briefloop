"""Local-only prompt sizing for Semantic Evaluator shadow execution."""

from __future__ import annotations

from importlib import metadata
from typing import Any

from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError


OPENAI_PROMPT_SIZER_ID = "openai_tiktoken_v1"
SYNTHETIC_PROMPT_SIZER_ID = "synthetic_fixture_sizer_v1"
SYNTHETIC_PROMPT_SIZER_VERSION = "synthetic_fixture_sizer_v1"

# Responses input uses one instructions string and one user input string.  This
# fixed allowance is instrument policy, not a claim about provider billing.
_RESPONSES_MESSAGE_OVERHEAD = 8


class SyntheticFixturePromptSizerV1:
    """Hermetic local sizer used only by the packaged synthetic adapter."""

    sizer_id = SYNTHETIC_PROMPT_SIZER_ID
    sizer_version = SYNTHETIC_PROMPT_SIZER_VERSION
    package_name = "synthetic"
    package_version = "synthetic-v1"
    encoding_name = "synthetic-unicode-codepoint-v1"

    def count_tokens(self, *, system_text: str, user_text: str) -> int:
        if type(system_text) is not str or type(user_text) is not str:
            raise SemanticEvaluatorError("prompt_sizer_unavailable")
        # Deterministic and deliberately conservative.  Qualification may never
        # use this adapter/sizer pair.
        return len(system_text) + len(user_text) + _RESPONSES_MESSAGE_OVERHEAD


class OpenAITiktokenPromptSizerV1:
    """Frozen local OpenAI prompt sizer with no encoding fallback."""

    sizer_id = OPENAI_PROMPT_SIZER_ID

    def __init__(self, *, model_id: str) -> None:
        if type(model_id) is not str or not model_id:
            raise SemanticEvaluatorError("prompt_sizer_unavailable")
        try:
            import tiktoken  # type: ignore[import-not-found]

            encoding = tiktoken.encoding_for_model(model_id)
            version = metadata.version("tiktoken")
            encoding_name = encoding.name
        except Exception:
            raise SemanticEvaluatorError("prompt_sizer_unavailable") from None
        if any(
            type(value) is not str or not value for value in (version, encoding_name)
        ):
            raise SemanticEvaluatorError("prompt_sizer_unavailable")
        self._encoding: Any = encoding
        self.package_name = "tiktoken"
        self.package_version = version
        self.encoding_name = encoding_name
        self.sizer_version = f"tiktoken-{version}:{encoding_name}"

    def count_tokens(self, *, system_text: str, user_text: str) -> int:
        if type(system_text) is not str or type(user_text) is not str:
            raise SemanticEvaluatorError("prompt_sizer_unavailable")
        try:
            system_count = len(self._encoding.encode(system_text))
            user_count = len(self._encoding.encode(user_text))
        except Exception:
            raise SemanticEvaluatorError("prompt_sizer_unavailable") from None
        count = system_count + user_count + _RESPONSES_MESSAGE_OVERHEAD
        if type(count) is not int or count < 0:
            raise SemanticEvaluatorError("prompt_sizer_unavailable")
        return count


__all__ = [
    "OPENAI_PROMPT_SIZER_ID",
    "SYNTHETIC_PROMPT_SIZER_ID",
    "SYNTHETIC_PROMPT_SIZER_VERSION",
    "OpenAITiktokenPromptSizerV1",
    "SyntheticFixturePromptSizerV1",
]
