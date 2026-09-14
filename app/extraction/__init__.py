"""Deadline extraction.

The extractor interface is deliberately small so that different implementations can be
swapped: a deterministic rule-based extractor (default, works offline) and an LLM-based one
(Google Gemini). Only text interpretation lives here; date arithmetic is done by
:mod:`app.date_engine`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from app.models import Deadline
from app.settings import settings

from .llm import FallbackExtractor, GeminiExtractor, LLMExtractionError
from .rules import RuleBasedExtractor


class DeadlineExtractor(Protocol):
    name: str

    def extract(self, text: str) -> list[Deadline]: ...


def build_extractor(mode: str, api_key: str, model: str) -> DeadlineExtractor:
    """mode: 'rules' | 'llm' | 'auto' (LLM with rule fallback when a key is configured)."""
    rules = RuleBasedExtractor()
    if mode == "rules" or (mode == "auto" and not api_key):
        return rules
    if not api_key:
        raise ValueError("DE_EXTRACTOR=llm requires DE_GEMINI_API_KEY")
    llm = GeminiExtractor(api_key=api_key, model=model)
    return FallbackExtractor(llm, rules) if mode == "auto" else llm


@lru_cache(maxsize=1)
def get_extractor() -> DeadlineExtractor:
    return build_extractor(settings.extractor, settings.gemini_api_key, settings.gemini_model)


def extract_detailed(extractor: DeadlineExtractor, text: str) -> tuple[list[Deadline], str, str]:
    """Run any extractor and return (deadlines, engine name, warning)."""
    if isinstance(extractor, FallbackExtractor):
        return extractor.extract_detailed(text)
    return extractor.extract(text), getattr(extractor, "name", "rules"), ""


__all__ = [
    "DeadlineExtractor",
    "FallbackExtractor",
    "GeminiExtractor",
    "LLMExtractionError",
    "RuleBasedExtractor",
    "build_extractor",
    "extract_detailed",
    "get_extractor",
]
