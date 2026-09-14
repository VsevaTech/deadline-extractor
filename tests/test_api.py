import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
client = TestClient(app)


def _session_id(html: str) -> str:
    m = re.search(r'href="/s/([0-9a-f]+)"', html)
    assert m, html[:500]
    return m.group(1)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_index_renders():
    r = client.get("/")
    assert r.status_code == 200
    assert "Deadline Extractor" in r.text


def test_api_extract_spec_example():
    r = client.post(
        "/api/extract",
        json={
            "text": "Documents must be submitted within 14 days from receipt.",
            "anchors": {"receipt": "2026-09-14"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["required_anchors"] == ["receipt"]
    d = body["deadlines"][0]
    assert d["action"] == "Submit documents"
    assert d["computed_date"] == "2026-09-28"
    assert d["source_quote"] == "within 14 days from receipt"
    assert d["status"] == "computed"


def test_api_extract_without_anchor_is_needs_input():
    r = client.post("/api/extract", json={"text": "Pay within 30 days of the invoice date."})
    d = r.json()["deadlines"][0]
    assert d["status"] == "needs_input" and d["computed_date"] is None


def test_api_extract_empty_text():
    assert client.post("/api/extract", json={"text": "   "}).status_code == 400


@pytest.mark.parametrize(
    "name, expected",
    [
        ("regulatory_notice.pdf", 3),
        ("commercial_lease.docx", 6),
        ("service_agreement.txt", 6),
        ("meeting_minutes_no_deadlines.docx", 0),
    ],
)
def test_api_extract_example_files(name, expected):
    with (EXAMPLES / name).open("rb") as fh:
        r = client.post("/api/extract-file", files={"file": (name, fh)})
    assert r.status_code == 200, r.text
    assert len(r.json()["deadlines"]) == expected


def test_invalid_file_extension():
    r = client.post("/api/extract-file", files={"file": ("photo.png", b"\x89PNG....")})
    assert r.status_code == 400
    assert "Unsupported" in r.json()["detail"]


def test_corrupt_pdf():
    r = client.post("/api/extract-file", files={"file": ("broken.pdf", b"not really a pdf")})
    assert r.status_code == 400


def test_corrupt_docx():
    r = client.post("/api/extract-file", files={"file": ("broken.docx", b"garbage")})
    assert r.status_code == 400


def test_ui_flow_upload_anchor_confirm_export():
    # 1. upload document -> 3 deadlines found
    with (EXAMPLES / "regulatory_notice.pdf").open("rb") as fh:
        r = client.post("/extract", files={"file": ("regulatory_notice.pdf", fh)})
    assert r.status_code == 200
    assert "3 deadlines found" in r.text
    sid = _session_id(r.text)
    assert "needs input" in r.text

    # 2. enter receipt + notice dates -> all computed
    r = client.post(
        f"/s/{sid}/anchors", data={"anchor_receipt": "2026-09-14", "anchor_notice": "2026-09-14"}
    )
    assert "28 Sep 2026" in r.text  # within 14 days from receipt
    assert "30 Sep 2026" in r.text  # absolute
    assert "21 Sep 2026" in r.text  # 5 business days after Mon 14 Sep
    assert "0 need input" in r.text

    # export refused before confirmation
    assert client.get(f"/s/{sid}/calendar.ics").status_code == 400

    # 3. confirm every deadline
    ids = re.findall(r'id="d-([0-9a-f]+)"', r.text)
    assert len(ids) == 3
    for did in ids:
        r = client.post(f"/s/{sid}/d/{did}/confirm")
    assert "3 confirmed" in r.text

    # 4. download calendar.ics
    r = client.get(f"/s/{sid}/calendar.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    ics = r.text
    assert ics.count("BEGIN:VEVENT") == 3
    assert "DTSTART;VALUE=DATE:20260928" in ics
    assert "Submit documents" in ics
    assert "within 14 days from receipt" in ics


def test_ui_edit_deadline_manually():
    r = client.post(
        "/extract", data={"text": "Vacate the premises within 2 months following termination."}
    )
    sid = _session_id(r.text)
    did = re.findall(r'id="d-([0-9a-f]+)"', r.text)[0]
    assert "needs input" in r.text
    # user converts it to an absolute date
    r = client.post(
        f"/s/{sid}/d/{did}",
        data={
            "action": "Vacate premises",
            "kind": "absolute",
            "absolute_date": "2026-12-01",
            "offset_value": "",
            "offset_unit": "",
            "direction": "after",
            "anchor": "",
        },
    )
    assert "01 Dec 2026" in r.text and "Vacate premises" in r.text
    # or picks the 'other' anchor and supplies its date
    r = client.post(
        f"/s/{sid}/d/{did}",
        data={
            "action": "Vacate premises",
            "kind": "relative",
            "absolute_date": "",
            "offset_value": "2",
            "offset_unit": "months",
            "direction": "after",
            "anchor": "other",
        },
    )
    r = client.post(f"/s/{sid}/anchors", data={"anchor_other": "2026-01-31"})
    assert "31 Mar 2026" in r.text


def test_ui_delete_and_empty_input():
    r = client.post("/extract", data={"text": "Pay by 1 October 2026."})
    sid = _session_id(r.text)
    did = re.findall(r'id="d-([0-9a-f]+)"', r.text)[0]
    r = client.post(f"/s/{sid}/d/{did}/delete")
    assert "0 deadlines found" in r.text
    assert client.post("/extract", data={"text": ""}).status_code == 400
    assert client.get("/s/doesnotexist").status_code == 404


def test_ui_example_picker():
    r = client.post("/extract", data={"example": "service_agreement.txt"})
    assert "6 deadlines found" in r.text
    assert client.post("/extract", data={"example": "../pyproject.toml"}).status_code == 404
