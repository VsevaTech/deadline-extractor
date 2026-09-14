"""FastAPI application: HTMX UI + small JSON API."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app import date_engine, ics
from app.extraction import extract_detailed, get_extractor
from app.models import ANCHOR_LABELS, Deadline, DeadlineKind, Direction, ExtractionResult, Unit
from app.parsers import EmptyDocumentError, UnsupportedFileError, parse_text, parse_upload
from app.settings import settings

BASE_DIR = Path(__file__).parent
app = FastAPI(title="Deadline Extractor", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["ANCHOR_LABELS"] = ANCHOR_LABELS
templates.env.globals["UNITS"] = list(Unit)
templates.env.globals["KINDS"] = list(DeadlineKind)
templates.env.globals["DIRECTIONS"] = list(Direction)


class Session(BaseModel):
    id: str
    title: str
    text: str
    deadlines: list[Deadline]
    anchors: dict[str, date] = {}
    engine: str = "rules"
    warning: str = ""

    def required_anchors(self) -> list[str]:
        keys = [d.anchor for d in self.deadlines if d.anchor]
        return sorted(set(keys), key=list(ANCHOR_LABELS).index)

    def recompute(self) -> None:
        self.deadlines = date_engine.compute_all(self.deadlines, self.anchors)


SESSIONS: dict[str, Session] = {}
MAX_SESSIONS = 200


def _store(session: Session) -> None:
    if len(SESSIONS) >= MAX_SESSIONS:
        SESSIONS.pop(next(iter(SESSIONS)))
    SESSIONS[session.id] = session


def _get(session_id: str) -> Session:
    try:
        return SESSIONS[session_id]
    except KeyError as exc:
        raise HTTPException(404, "Session not found (server restarted?)") from exc


def run_extraction(text: str) -> ExtractionResult:
    found, engine, warning = extract_detailed(get_extractor(), text)
    deadlines = date_engine.compute_all(found, {})
    anchors = sorted({d.anchor for d in deadlines if d.anchor}, key=list(ANCHOR_LABELS).index)
    return ExtractionResult(
        text_length=len(text),
        deadlines=deadlines,
        required_anchors=anchors,
        engine=engine,
        warning=warning,
    )


# ------------------------------------------------------------------ UI


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"examples": settings.example_files()})


@app.post("/extract", response_class=HTMLResponse)
async def extract(
    request: Request,
    file: UploadFile | None = None,
    text: Annotated[str, Form()] = "",
    example: Annotated[str, Form()] = "",
):
    try:
        if example:
            path = settings.example_path(example)
            source_text, title = parse_upload(path.name, path.read_bytes()), path.name
        elif file is not None and file.filename:
            source_text, title = parse_upload(file.filename, await file.read()), file.filename
        elif text.strip():
            source_text, title = parse_text(text.encode()), "Pasted text"
        else:
            raise UnsupportedFileError("Upload a file or paste some text.")
    except (UnsupportedFileError, EmptyDocumentError) as exc:
        return templates.TemplateResponse(
            request, "partials/error.html", {"message": str(exc)}, status_code=400
        )
    found, engine, warning = extract_detailed(get_extractor(), source_text)
    session = Session(
        id=uuid.uuid4().hex[:10],
        title=title,
        text=source_text,
        deadlines=found,
        engine=engine,
        warning=warning,
    )
    session.recompute()
    _store(session)
    return templates.TemplateResponse(request, "partials/results.html", {"s": session})


@app.get("/s/{session_id}", response_class=HTMLResponse)
async def session_page(request: Request, session_id: str):
    return templates.TemplateResponse(
        request, "index.html", {"s": _get(session_id), "examples": settings.example_files()}
    )


@app.post("/s/{session_id}/anchors", response_class=HTMLResponse)
async def set_anchors(request: Request, session_id: str):
    s = _get(session_id)
    form = await request.form()
    anchors: dict[str, date] = {}
    for key in ANCHOR_LABELS:
        raw = str(form.get(f"anchor_{key}", "")).strip()
        if raw:
            try:
                anchors[key] = date.fromisoformat(raw)
            except ValueError:
                pass
    s.anchors = anchors
    s.recompute()
    return templates.TemplateResponse(request, "partials/results.html", {"s": s})


@app.post("/s/{session_id}/d/{deadline_id}", response_class=HTMLResponse)
async def edit_deadline(request: Request, session_id: str, deadline_id: str):
    s = _get(session_id)
    form = await request.form()
    for i, d in enumerate(s.deadlines):
        if d.id != deadline_id:
            continue
        data = d.model_dump()
        data["action"] = str(form.get("action", d.action)).strip() or d.action
        data["kind"] = form.get("kind", d.kind)
        abs_raw = str(form.get("absolute_date", "")).strip()
        data["absolute_date"] = date.fromisoformat(abs_raw) if abs_raw else None
        off_raw = str(form.get("offset_value", "")).strip()
        data["offset_value"] = int(off_raw) if off_raw.isdigit() else None
        data["offset_unit"] = form.get("offset_unit") or None
        data["direction"] = form.get("direction", d.direction)
        data["anchor"] = form.get("anchor") or None
        data["confirmed"] = False
        try:
            s.deadlines[i] = Deadline.model_validate(data)
        except ValueError as exc:
            return templates.TemplateResponse(
                request, "partials/error.html", {"message": str(exc)}, status_code=400
            )
        break
    s.recompute()
    return templates.TemplateResponse(request, "partials/results.html", {"s": s})


@app.post("/s/{session_id}/d/{deadline_id}/confirm", response_class=HTMLResponse)
async def confirm_deadline(request: Request, session_id: str, deadline_id: str):
    s = _get(session_id)
    for d in s.deadlines:
        if d.id == deadline_id and d.computed_date is not None:
            d.confirmed = not d.confirmed
    s.recompute()
    return templates.TemplateResponse(request, "partials/results.html", {"s": s})


@app.post("/s/{session_id}/d/{deadline_id}/delete", response_class=HTMLResponse)
async def delete_deadline(request: Request, session_id: str, deadline_id: str):
    s = _get(session_id)
    s.deadlines = [d for d in s.deadlines if d.id != deadline_id]
    s.recompute()
    return templates.TemplateResponse(request, "partials/results.html", {"s": s})


@app.get("/s/{session_id}/calendar.ics")
async def calendar(session_id: str, all: bool = False):  # noqa: A002
    s = _get(session_id)
    chosen = [d for d in s.deadlines if d.computed_date and (all or d.confirmed)]
    if not chosen:
        raise HTTPException(400, "No confirmed deadlines to export")
    body = ics.build_calendar(chosen, title=f"Deadlines — {s.title}")
    return Response(
        body,
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="calendar.ics"'},
    )


# ------------------------------------------------------------------ JSON API


class ExtractRequest(BaseModel):
    text: str
    anchors: dict[str, date] = {}


@app.post("/api/extract", response_model=ExtractionResult)
async def api_extract(payload: ExtractRequest):
    try:
        text = parse_text(payload.text.encode())
    except EmptyDocumentError as exc:
        raise HTTPException(400, str(exc)) from exc
    result = run_extraction(text)
    result.deadlines = date_engine.compute_all(result.deadlines, payload.anchors)
    return result


@app.post("/api/extract-file", response_model=ExtractionResult)
async def api_extract_file(file: UploadFile):
    try:
        text = parse_upload(file.filename or "", await file.read())
    except (UnsupportedFileError, EmptyDocumentError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return run_extraction(text)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return RedirectResponse("/static/favicon.svg")
