"""LLM-based deadline extractor (Google Gemini, structured JSON output).

The model is used ONLY to interpret text: it returns deadline *descriptors* (kind, offset,
unit, direction, reference event, verbatim source quote, English action title). It never
computes dates — that stays in :mod:`app.date_engine`. Every returned quote is verified
against the document text; descriptors whose quote cannot be found verbatim are dropped, so
the "verify against the source" promise of the UI still holds.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

import httpx

from app.models import ANCHOR_LABELS, Deadline, DeadlineKind, Direction, Unit

from .rules import split_sentences

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.8-flash"
API_BASE = "https://generativelanguage.googleapis.com/v1beta"

ANCHOR_KEYS = [k for k in ANCHOR_LABELS if k != "other"]

SYSTEM_PROMPT = f"""You extract deadlines and time-bound obligations from legal, regulatory and
business documents.

Return ONLY a JSON object matching the schema. Rules:
- Write every output string in English, regardless of the document language. Translate
  actions into short imperative English titles ("Submit documents", "Pay invoice",
  "Give notice to Authority").
- "source_quote" must be an EXACT verbatim substring of the document (same language, same
  characters, no paraphrase). Keep it short: the phrase that establishes the deadline.
- Do NOT compute or convert any dates. Only describe the rule.
- kind: "absolute" for a fixed calendar date; "relative" for "within/after/before N units of
  <event>"; "notice" for notice periods ("30 days' written notice", "notice period of 2 months").
- For absolute deadlines set "absolute_date" to ISO YYYY-MM-DD as written in the document
  (day-first for ambiguous numeric dates); leave other fields null.
- For relative/notice deadlines set offset_value (integer), offset_unit
  (days | business_days | weeks | months | years), direction ("after" = event + offset,
  "before" = event - offset) and anchor — the reference event, one of:
  {", ".join(ANCHOR_KEYS)}. Use null when the event is not one of these. Notice periods use
  anchor "notice" and direction "after".
- Only include real obligations or rights with a deadline. Ignore dates that merely describe
  history ("dated 1 January 2026", "the parties met on ...").
- Include every deadline, including several in one sentence. Do not invent any.
"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "deadlines": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "action": {"type": "STRING"},
                    "source_quote": {"type": "STRING"},
                    "kind": {"type": "STRING", "enum": ["absolute", "relative", "notice"]},
                    "absolute_date": {"type": "STRING", "nullable": True},
                    "offset_value": {"type": "INTEGER", "nullable": True},
                    "offset_unit": {
                        "type": "STRING",
                        "enum": ["days", "business_days", "weeks", "months", "years"],
                        "nullable": True,
                    },
                    "direction": {"type": "STRING", "enum": ["after", "before"], "nullable": True},
                    "anchor": {"type": "STRING", "nullable": True},
                },
                "required": ["action", "source_quote", "kind"],
            },
        }
    },
    "required": ["deadlines"],
}

MAX_INPUT_CHARS = 60_000


class LLMExtractionError(RuntimeError):
    """Raised when the model call fails or returns unusable output."""


def find_quote(text: str, quote: str) -> str | None:
    """Return the verbatim span of `quote` inside `text` (whitespace/case tolerant), or None."""
    quote = quote.strip().strip("\"'“”‘’")
    if not quote:
        return None
    if quote in text:
        return quote
    pattern = r"\s+".join(re.escape(w) for w in quote.split())
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(0) if m else None


def sentence_around(text: str, span: str) -> str:
    """The sentence / list item of the document that contains `span` (falls back to `span`)."""
    needle = re.sub(r"\s+", " ", span).strip().lower()
    for sentence in split_sentences(text):
        if needle in sentence.lower():
            return sentence
    return span


def _parse_iso(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def to_deadlines(text: str, payload: dict) -> list[Deadline]:
    """Validate model output against the document and convert to `Deadline` objects."""
    results: list[Deadline] = []
    for item in payload.get("deadlines", []) or []:
        if not isinstance(item, dict):
            continue
        span = find_quote(text, str(item.get("source_quote", "")))
        if span is None:
            log.warning("LLM quote not found in document, dropped: %r", item.get("source_quote"))
            continue
        try:
            kind = DeadlineKind(str(item.get("kind", "")).lower())
        except ValueError:
            continue
        action = re.sub(r"\s+", " ", str(item.get("action") or "")).strip(" .") or "Deadline"
        context = sentence_around(text, span)
        if kind == DeadlineKind.ABSOLUTE:
            parsed = _parse_iso(item.get("absolute_date"))
            results.append(
                Deadline(
                    action=action,
                    source_quote=span,
                    context=context,
                    kind=kind,
                    absolute_date=parsed,
                    note="" if parsed else "Date could not be parsed. Enter it manually.",
                )
            )
            continue
        value = item.get("offset_value")
        unit: Unit | None = None
        if item.get("offset_unit"):
            try:
                unit = Unit(str(item["offset_unit"]).lower())
            except ValueError:
                unit = None
        is_before = str(item.get("direction") or "").lower() == "before"
        direction = Direction.BEFORE if is_before else Direction.AFTER
        anchor = str(item.get("anchor") or "").lower() or None
        if kind == DeadlineKind.NOTICE:
            anchor, direction = "notice", Direction.AFTER
        if anchor not in ANCHOR_LABELS:
            anchor = None
        note = ""
        if anchor is None:
            note = "Reference event not recognised. Set it manually."
        elif kind == DeadlineKind.NOTICE:
            note = "Earliest date the notice takes effect, counted from the day notice is given."
        results.append(
            Deadline(
                action=action,
                source_quote=span,
                context=context,
                kind=kind,
                offset_value=int(value) if isinstance(value, int | float) and value >= 0 else None,
                offset_unit=unit,
                direction=direction,
                anchor=anchor,
                note=note,
            )
        )
    return results


class GeminiExtractor:
    """DeadlineExtractor backed by the Gemini `generateContent` API."""

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Gemini API key is required")
        self.api_key = api_key
        self.model = model
        self.name = f"gemini:{model}"
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def _request_body(self, text: str) -> dict:
        return {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": f"DOCUMENT:\n{text}"}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA,
                # Structured extraction against a fixed schema doesn't need deep reasoning;
                # "low" cuts thinking-token cost/latency. Ignored by models that don't support it.
                "thinkingConfig": {"thinkingLevel": "low"},
            },
        }

    def call_model(self, text: str) -> dict:
        url = f"{API_BASE}/models/{self.model}:generateContent"
        try:
            r = self._client.post(
                url, json=self._request_body(text), headers={"x-goog-api-key": self.api_key}
            )
        except httpx.HTTPError as exc:
            raise LLMExtractionError(f"Gemini request failed: {exc}") from exc
        if r.status_code != 200:
            detail = r.text[:300]
            try:
                detail = r.json()["error"]["message"]
            except Exception:  # noqa: BLE001 - best-effort error message
                pass
            raise LLMExtractionError(f"Gemini API error {r.status_code}: {detail}")
        try:
            raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            payload = json.loads(raw)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMExtractionError(f"Unexpected Gemini response: {exc}") from exc
        if not isinstance(payload, dict):
            raise LLMExtractionError("Gemini returned a non-object JSON payload")
        return payload

    def extract(self, text: str) -> list[Deadline]:
        text = text[:MAX_INPUT_CHARS]
        return to_deadlines(text, self.call_model(text))


class FallbackExtractor:
    """Try the primary extractor; on failure fall back to the secondary and say why."""

    def __init__(self, primary, secondary) -> None:
        self.primary = primary
        self.secondary = secondary
        self.name = getattr(primary, "name", "primary")

    def extract(self, text: str) -> list[Deadline]:
        return self.extract_detailed(text)[0]

    def extract_detailed(self, text: str) -> tuple[list[Deadline], str, str]:
        """Return (deadlines, engine name actually used, warning or '')."""
        try:
            return self.primary.extract(text), self.name, ""
        except LLMExtractionError as exc:
            log.warning("LLM extraction failed, using rules: %s", exc)
            secondary_name = getattr(self.secondary, "name", "rules")
            return self.secondary.extract(text), secondary_name, f"LLM unavailable ({exc})"
