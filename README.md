# Deadline Extractor

Upload a PDF / DOCX or paste text → the service finds obligations with deadlines, shows the
**source quote** for each one, computes the exact date with deterministic calendar arithmetic,
lets you review/edit/confirm, and exports the confirmed deadlines as a `.ics` calendar.

```text
Document / text → extract deadlines → show source quote → calculate exact date → user confirms → export .ics
```

Example. Input clause:

```text
"Documents must be submitted within 14 days from receipt."
```

User enters `Document received: 14 Sep 2026`. Output:

```text
Action:   Submit documents
Deadline: 28 Sep 2026
Source:   "within 14 days from receipt"
```

> **Always verify extracted deadlines against the original document.** Extraction is automated
> (rule-based patterns or an LLM, see below). Either can miss clauses, mis-attribute an action,
> or pick the wrong reference event. The source quote and the full sentence are shown next to
> every result precisely so you can check them before confirming.

## Features

- Input: PDF (text layer), DOCX, plain text / Markdown, or pasted text.
- Deadline kinds: absolute dates (`no later than 30 September 2026`, `due on 2026-10-01`,
  `by October 15th, 2026`, `31.12.2026`), relative periods (`within 14 days from receipt`,
  `within 5 business days of the effective date`, `at least 60 days prior to the expiry`),
  notice periods (`thirty (30) days' written notice`, `notice period of 2 months`).
- Multiple deadlines per document and per sentence.
- `Needs input` status when the reference date is unknown or the reference event is not
  recognised; the user supplies reference dates (receipt, signing, invoice, notice, …) once
  and every dependent deadline is recomputed.
- Manual editing of every result (action, kind, offset, unit, direction, reference event) and
  removal of false positives.
- Confirm → export `.ics` (all-day events with a 1-day-before reminder; description carries the
  source quote and the rule used).
- JSON API (`/api/extract`, `/api/extract-file`) for scripting.

Date arithmetic is **deterministic code** (`app/date_engine.py`). Text interpretation is isolated
behind a tiny `DeadlineExtractor` protocol (`app/extraction/`) with two implementations:

| Engine (`DE_EXTRACTOR`) | What it does | Needs |
|---|---|---|
| `rules` | Deterministic regex patterns over English contract phrasing. Offline, free, predictable. | nothing |
| `llm` | Google Gemini with a strict JSON schema. Handles free-form wording and **non-English documents** (quotes stay verbatim, action titles are always English). | `DE_GEMINI_API_KEY` |
| `auto` (default) | `llm` when a key is configured, with automatic fallback to `rules` if the API fails (the UI shows which engine produced the result and why). Without a key behaves as `rules`. | — |

In both engines the model/regex only *describes* the rule (kind, offset, unit, direction,
reference event, verbatim quote). Dates are always computed by `date_engine.py`, and every LLM
quote is checked against the document text — descriptors whose quote is not found verbatim are
dropped, so nothing is shown that you cannot verify in the source.

## Quick start

```bash
git clone https://github.com/VsevaTech/deadline-extractor.git
cd deadline-extractor
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python examples/generate_examples.py   # creates the synthetic PDF/DOCX examples
uvicorn app.main:app --reload
# open http://127.0.0.1:8000
```

Docker:

```bash
docker compose up --build
# open http://127.0.0.1:8000
```

Configuration is optional — see `.env.example` (`DE_*` variables). To enable the LLM engine:

```bash
cp .env.example .env
# set DE_GEMINI_API_KEY=<key from https://aistudio.google.com/apikey>
uvicorn app.main:app --reload        # or: docker compose up --build (reads .env)
```

The results header shows `extractor: gemini:gemini-3.8-flash` or `extractor: rules`; the JSON API
returns the same in `engine` (plus `warning` when the LLM failed and rules were used instead).

## Demo

```text
upload examples/regulatory_notice.pdf
→ 3 deadlines found
   • Submit documents                     — needs input  ("within 14 days from receipt of this notice")
   • File annual compliance certificate   — 30 Sep 2026  ("no later than 30 September 2026")
   • Give notice to the Authority         — needs input  ("5 business days' prior written notice")
→ enter reference dates: Document received = 2026-09-14, Notice given = 2026-09-14
→ 28 Sep 2026 / 30 Sep 2026 / 21 Sep 2026 — verify each against the shown source sentence
→ Confirm each row → Download calendar.ics
```

Or via the API:

```bash
curl -s -X POST http://127.0.0.1:8000/api/extract \
  -H 'content-type: application/json' \
  -d '{"text":"Documents must be submitted within 14 days from receipt.","anchors":{"receipt":"2026-09-14"}}'
```

Synthetic example documents live in `examples/` (all fictional): `regulatory_notice.pdf`
(3 deadlines, the demo), `commercial_lease.docx` (6 deadlines incl. months and “before expiry”),
`service_agreement.txt` (6 deadlines), `meeting_minutes_no_deadlines.docx` (dates but no
obligations → 0 results). The PDF/DOCX files are generated, not committed — run
`python examples/generate_examples.py` once after cloning (Docker, CI and pytest do it
automatically).

## Calculation rules

| Clause                                       | Rule                                              |
|----------------------------------------------|---------------------------------------------------|
| `within N days from X`                       | X + N calendar days (X is day 0)                  |
| `within N business/working days of X`        | skip Saturday & Sunday; no public-holiday calendar|
| `within N weeks / months / years of X`       | calendar arithmetic, month-end clamped (31 Jan + 1 month = 28/29 Feb) |
| `at least N days prior to / before X`        | X − N units                                       |
| `N days' notice`                             | notice date + N units = earliest effective date   |
| absolute date                                | used as is                                        |

No weekend/holiday roll-forward is applied; a note is attached when a result falls on a weekend.

## Development

```bash
pytest -q          # 73 tests, fully offline: absolute dates, within-N-days, notice periods,
                   # missing reference date, multiple deadlines, no deadlines, invalid files,
                   # date edge cases, UI flow, Gemini extractor with a mocked HTTP transport
                   # (schema, quote verification, error handling, fallback to rules)
DE_GEMINI_API_KEY=... DE_LIVE_LLM=1 pytest -q tests/test_llm_live.py -s   # real API smoke test
ruff check . && ruff format --check .
docker build -t deadline-extractor .
```

CI (`.github/workflows/ci.yml`) runs ruff + pytest on Python 3.11/3.12, builds the Docker image
and smoke-tests the running container.

## Limitations

- **`rules` engine is English only** and pattern-based: unusual wording, tables of dates, or
  deadlines spread across several sentences are missed; action titles are heuristic rewrites of
  the sentence and the reference event (“receipt”, “signing”, …) is keyword-matched. Unknown
  events are flagged `Needs input` rather than guessed.
- **`llm` engine** sends the document text (first 60k characters) to Google's API — check that
  this is acceptable for your documents. Output is constrained by a JSON schema and verified
  against the source, but the model can still miss a clause or pick a wrong reference event;
  the UI exists so that a human confirms every date. Non-English documents are supported by the
  LLM engine only. Default model is `gemini-3.8-flash` with `thinkingLevel: low` (structured
  extraction against a fixed schema doesn't need deep reasoning); Gemini model availability
  shifts over time — if `DE_GEMINI_MODEL` starts returning 404, check
  https://ai.google.dev/gemini-api/docs/models for the current model list.
- **No OCR.** Scanned PDFs without a text layer are rejected.
- **No jurisdiction-specific day counting** (public holidays, “clear days”, court rules).
- Dates like `03/04/2026` are read day-first.
- Sessions are kept in process memory (max 200); restarting the server drops them. No auth —
  run it locally or behind your own gateway.
- The UI loads HTMX from unpkg (with an SRI hash); the browser needs internet access for the
  interactive UI. The JSON API works fully offline.
- The `.ics` export contains only deadlines you explicitly confirmed.

## Project layout

```text
app/
  main.py            FastAPI routes (HTMX UI + JSON API)
  models.py          Pydantic domain models
  parsers/           PDF / DOCX / text → plain text
  extraction/        DeadlineExtractor protocol, RuleBasedExtractor, GeminiExtractor (+ fallback)
  date_engine.py     deterministic date arithmetic
  ics.py             iCalendar export
  templates/, static/
tests/
examples/            synthetic documents + generator
Dockerfile, docker-compose.yml, .env.example, .github/workflows/ci.yml, LICENSE (MIT)
```
