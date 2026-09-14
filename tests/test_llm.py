"""Gemini extractor tests with a mocked HTTP transport (no network, no key needed)."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from app import date_engine
from app.extraction import FallbackExtractor, GeminiExtractor, LLMExtractionError, build_extractor
from app.extraction.llm import find_quote, to_deadlines
from app.extraction.rules import RuleBasedExtractor
from app.models import DeadlineKind, Direction, Status, Unit

DOC = (
    "1. Documents must be submitted within 14 days from receipt.\n"
    "2. The Buyer shall pay each invoice no later than 30 September 2026.\n"
    "3. Either party may terminate this Agreement by giving 30 days' written notice."
)


def gemini_response(payload: dict, status: int = 200) -> httpx.Response:
    body = {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}
    return httpx.Response(status, json=body)


def make_extractor(handler) -> GeminiExtractor:
    return GeminiExtractor(api_key="test-key", transport=httpx.MockTransport(handler))


def test_request_shape_and_happy_path():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return gemini_response(
            {
                "deadlines": [
                    {
                        "action": "Submit documents",
                        "source_quote": "within 14 days from receipt",
                        "kind": "relative",
                        "offset_value": 14,
                        "offset_unit": "days",
                        "direction": "after",
                        "anchor": "receipt",
                    },
                    {
                        "action": "Pay invoice",
                        "source_quote": "no later than 30 September 2026",
                        "kind": "absolute",
                        "absolute_date": "2026-09-30",
                    },
                    {
                        "action": "Terminate agreement by giving notice",
                        "source_quote": "30 days' written notice",
                        "kind": "notice",
                        "offset_value": 30,
                        "offset_unit": "days",
                    },
                ]
            }
        )

    found = make_extractor(handler).extract(DOC)
    assert seen["url"].endswith("/models/gemini-2.5-flash:generateContent")
    assert seen["key"] == "test-key"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "English" in seen["body"]["systemInstruction"]["parts"][0]["text"]

    assert [d.kind for d in found] == [
        DeadlineKind.RELATIVE,
        DeadlineKind.ABSOLUTE,
        DeadlineKind.NOTICE,
    ]
    assert found[0].anchor == "receipt" and found[0].offset_unit == Unit.DAYS
    assert found[0].context.startswith("1. Documents must be submitted")
    assert found[2].anchor == "notice" and found[2].direction == Direction.AFTER
    computed = date_engine.compute_all(
        found, {"receipt": date(2026, 9, 14), "notice": date(2026, 9, 14)}
    )
    assert [d.computed_date for d in computed] == [
        date(2026, 9, 28),
        date(2026, 9, 30),
        date(2026, 10, 14),
    ]


def test_hallucinated_quote_is_dropped():
    payload = {
        "deadlines": [
            {"action": "Pay", "source_quote": "within 99 days of the moon", "kind": "relative"},
            {
                "action": "Submit documents",
                "source_quote": "WITHIN 14 DAYS   FROM RECEIPT",  # case/whitespace tolerant
                "kind": "relative",
                "offset_value": 14,
                "offset_unit": "days",
                "anchor": "receipt",
            },
        ]
    }
    found = to_deadlines(DOC, payload)
    assert len(found) == 1
    assert found[0].source_quote == "within 14 days from receipt"  # verbatim span from the doc


def test_unknown_anchor_and_bad_values_become_needs_input():
    payload = {
        "deadlines": [
            {
                "action": "Vacate premises",
                "source_quote": "within 14 days from receipt",
                "kind": "relative",
                "offset_value": 14,
                "offset_unit": "fortnights",
                "anchor": "termination",
            },
            {
                "action": "Pay",
                "source_quote": "30 September 2026",
                "kind": "absolute",
                "absolute_date": "31/02/2026",
            },
        ]
    }
    found = date_engine.compute_all(to_deadlines(DOC, payload), {"receipt": date(2026, 9, 14)})
    assert found[0].status == Status.NEEDS_INPUT and found[0].anchor is None
    assert found[0].offset_unit is None
    assert found[1].status == Status.NEEDS_INPUT and found[1].absolute_date is None


def test_find_quote_strips_wrapping_quotes():
    assert find_quote(DOC, "“within 14 days from receipt”") == "within 14 days from receipt"
    assert find_quote(DOC, "") is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, json={"error": {"message": "API key not valid"}}),
        httpx.Response(500, text="boom"),
        httpx.Response(200, json={"candidates": []}),
        httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}),
        gemini_response(["a", "list"]),
    ],
)
def test_api_errors_raise(response):
    ex = make_extractor(lambda request: response)
    with pytest.raises(LLMExtractionError):
        ex.extract(DOC)


def test_network_error_raises():
    def handler(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(LLMExtractionError, match="request failed"):
        make_extractor(handler).extract(DOC)


def test_fallback_uses_rules_when_llm_fails():
    llm = make_extractor(lambda r: httpx.Response(503, text="overloaded"))
    fb = FallbackExtractor(llm, RuleBasedExtractor())
    found, engine, warning = fb.extract_detailed(DOC)
    assert engine == "rules" and "LLM unavailable" in warning and "503" in warning
    assert len(found) == 3


def test_fallback_reports_llm_engine_on_success():
    llm = make_extractor(lambda r: gemini_response({"deadlines": []}))
    fb = FallbackExtractor(llm, RuleBasedExtractor())
    found, engine, warning = fb.extract_detailed(DOC)
    assert found == [] and engine == "gemini:gemini-2.5-flash" and warning == ""


def test_build_extractor_modes():
    assert isinstance(build_extractor("rules", "key", "m"), RuleBasedExtractor)
    assert isinstance(build_extractor("auto", "", "m"), RuleBasedExtractor)
    assert isinstance(build_extractor("auto", "key", "m"), FallbackExtractor)
    assert isinstance(build_extractor("llm", "key", "m"), GeminiExtractor)
    with pytest.raises(ValueError):
        build_extractor("llm", "", "m")


def test_api_reports_engine(monkeypatch):
    from fastapi.testclient import TestClient

    from app import extraction, main

    llm = make_extractor(
        lambda r: gemini_response(
            {
                "deadlines": [
                    {
                        "action": "Submit documents",
                        "source_quote": "within 14 days from receipt",
                        "kind": "relative",
                        "offset_value": 14,
                        "offset_unit": "days",
                        "anchor": "receipt",
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(main, "get_extractor", lambda: FallbackExtractor(llm, RuleBasedExtractor()))
    client = TestClient(main.app)
    body = client.post(
        "/api/extract",
        json={"text": "Documents must be submitted within 14 days from receipt.", "anchors": {}},
    ).json()
    assert body["engine"] == "gemini:gemini-2.5-flash" and body["warning"] == ""
    assert body["deadlines"][0]["action"] == "Submit documents"
    html = client.post("/extract", data={"text": DOC}).text
    assert "gemini:gemini-2.5-flash" in html
    assert extraction  # imported for clarity of what is being patched
