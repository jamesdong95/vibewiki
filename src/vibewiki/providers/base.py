"""Provider contract for grounded, optional VibeWiki conversations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """Text returned by a provider without exposing request secrets."""

    text: str
    model: str


class LLMProvider(Protocol):
    """Small interface implemented by local and compatible HTTP providers."""

    name: str

    def generate(
        self, messages: Sequence[dict[str, str]], *, model: str
    ) -> ProviderResponse:
        """Generate one bounded response from already-retrieved context."""

