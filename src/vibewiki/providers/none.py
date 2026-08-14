"""Evidence-only fallback when no model is configured."""

from __future__ import annotations

from collections.abc import Sequence

from .base import ProviderResponse


class EvidenceOnlyProvider:
    """Keep the product useful and honest without a network/model call."""

    name = "none"

    def generate(
        self, messages: Sequence[dict[str, str]], *, model: str
    ) -> ProviderResponse:
        del messages
        return ProviderResponse(
            "LLM chưa được bật. VibeWiki đã tìm evidence cục bộ; hãy cấu hình "
            "Ollama hoặc API-compatible provider để nhận phần thảo luận tự nhiên.",
            model,
        )
