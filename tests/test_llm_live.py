"""Live smoke test against the real Gemini API.

Skipped unless both DE_GEMINI_API_KEY and DE_LIVE_LLM=1 are set:

    DE_GEMINI_API_KEY=... DE_LIVE_LLM=1 pytest -q tests/test_llm_live.py -s
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from app import date_engine
from app.extraction import GeminiExtractor
from app.models import DeadlineKind, Unit

pytestmark = pytest.mark.skipif(
    not (os.environ.get("DE_GEMINI_API_KEY") and os.environ.get("DE_LIVE_LLM") == "1"),
    reason="set DE_GEMINI_API_KEY and DE_LIVE_LLM=1 to run the live Gemini test",
)


def test_live_spec_example():
    ex = GeminiExtractor(
        api_key=os.environ["DE_GEMINI_API_KEY"],
        model=os.environ.get("DE_GEMINI_MODEL", "gemini-3.8-flash"),
    )
    found = ex.extract("Documents must be submitted within 14 days from receipt.")
    print("\n", [d.model_dump(mode="json") for d in found])
    assert len(found) == 1
    d = found[0]
    assert d.kind == DeadlineKind.RELATIVE
    assert d.offset_value == 14 and d.offset_unit == Unit.DAYS and d.anchor == "receipt"
    assert d.source_quote in "Documents must be submitted within 14 days from receipt."
    assert d.action.isascii()  # English output
    computed = date_engine.compute(d, {"receipt": date(2026, 9, 14)})
    assert computed.computed_date == date(2026, 9, 28)


def test_live_non_english_document_yields_english_actions():
    ex = GeminiExtractor(api_key=os.environ["DE_GEMINI_API_KEY"])
    text = (
        "Арендатор обязан оплатить счёт в течение 10 рабочих дней с даты получения счёта. "
        "Договор может быть расторгнут любой стороной с уведомлением за 30 дней."
    )
    found = ex.extract(text)
    print("\n", [d.model_dump(mode="json") for d in found])
    assert len(found) >= 2
    for d in found:
        assert d.action.isascii(), d.action  # titles are English
        assert d.source_quote in text  # quotes stay verbatim (Russian)
