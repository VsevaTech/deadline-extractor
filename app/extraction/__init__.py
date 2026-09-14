"""Deadline extraction.

The extractor interface is deliberately small so that a different implementation
(e.g. an LLM with structured output) can be plugged in later. Only text interpretation
lives here; date arithmetic is done by :mod:`app.date_engine`.
"""

from __future__ import annotations

from typing import Protocol

from app.models import Deadline

from .rules import RuleBasedExtractor


class DeadlineExtractor(Protocol):
    def extract(self, text: str) -> list[Deadline]: ...


def get_extractor() -> DeadlineExtractor:
    return RuleBasedExtractor()


__all__ = ["DeadlineExtractor", "RuleBasedExtractor", "get_extractor"]
