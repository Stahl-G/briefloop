"""Local-only prompt sizing for private Semantic Evaluator shadow runs."""

from __future__ import annotations

from importlib import metadata
from typing import Any

from multi_agent_brief.semantic_evaluator.errors import SemanticEvaluatorError


OPENAI_PROMPT_SIZER_ID = "openai_tiktoken_v1"
SYNTHETIC_PROMPT_SIZER_ID = "synthetic_fixture_sizer_v4"
SYNTHETIC_PROMPT_SIZER_VERSION = "synthetic_fixture_sizer_v4"
_RESPONSES_MESSAGE_OVERHEAD = 8


def _exact_count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise SemanticEvaluatorError("prompt_sizer_unavailable")
    return value


class SyntheticFixturePromptSizerV4:
    sizer_id = SYNTHETIC_PROMPT_SIZER_ID
    sizer_version = SYNTHETIC_PROMPT_SIZER_VERSION
    package_name = "synthetic"
    package_version = "synthetic-v4"
    encoding_name = "synthetic-unicode-codepoint-v4"

    def count_tokens(self, *, system_text: str, user_text: str) -> int:
        if type(system_text) is not str or type(user_text) is not str:
            raise SemanticEvaluatorError("prompt_sizer_unavailable")
        try:
            system_text.encode("utf-8", errors="strict")
            user_text.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise SemanticEvaluatorError("prompt_sizer_unavailable") from None
        return _exact_count(
            len(system_text) + len(user_text) + _RESPONSES_MESSAGE_OVERHEAD
        )


class OpenAITiktokenPromptSizerV1:
    """Frozen local OpenAI prompt sizer with no guessed encoding fallback."""

    sizer_id = OPENAI_PROMPT_SIZER_ID

    def __init__(self, *, model_id: str) -> None:
        if type(model_id) is not str or not model_id:
            raise SemanticEvaluatorError("prompt_sizer_unavailable")
        try:
            model_id.encode("utf-8", errors="strict")
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
            system_count = _exact_count(len(self._encoding.encode(system_text)))
            user_count = _exact_count(len(self._encoding.encode(user_text)))
        except Exception:
            raise SemanticEvaluatorError("prompt_sizer_unavailable") from None
        return _exact_count(system_count + user_count + _RESPONSES_MESSAGE_OVERHEAD)


SyntheticFixturePromptSizerV1 = SyntheticFixturePromptSizerV4


__all__ = [
    "OPENAI_PROMPT_SIZER_ID",
    "SYNTHETIC_PROMPT_SIZER_ID",
    "SYNTHETIC_PROMPT_SIZER_VERSION",
    "OpenAITiktokenPromptSizerV1",
    "SyntheticFixturePromptSizerV4",
    "SyntheticFixturePromptSizerV1",
]
