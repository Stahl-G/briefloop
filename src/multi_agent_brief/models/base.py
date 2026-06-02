from __future__ import annotations

from abc import ABC, abstractmethod


class ModelProvider(ABC):
    name = "base"

    @abstractmethod
    def complete(self, prompt: str) -> str:
        raise NotImplementedError

