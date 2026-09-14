"""Deterministic, regex-based deadline extractor for English contract / notice language."""

from __future__ import annotations

import re
from datetime import date

from app.models import Deadline, DeadlineKind, Direction, Unit

# --------------------------------------------------------------------------- vocab

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "twenty-one": 21,
    "twenty-eight": 28,
    "thirty": 30,
    "forty-five": 45,
    "sixty": 60,
    "ninety": 90,
    "one hundred twenty": 120,
    "one hundred and twenty": 120,
    "one hundred eighty": 180,
    "one hundred and eighty": 180,
}
NUM = r"(?P<num>\d{1,3}|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")"
UNIT = r"(?P<unit>(?:calendar\s+|business\s+|working\s+)?(?:days?|weeks?|months?|years?))"

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for m in names
}
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

# Anchor phrase -> canonical anchor key
ANCHOR_PATTERNS: list[tuple[str, str]] = [
    (r"receipt|receiv(?:ing|ed)", "receipt"),
    (r"invoice|invoicing", "invoice"),
    (r"deliver(?:y|ed)|acceptance", "delivery"),
    (r"notice|notification|being notified", "notice"),
    (
        r"expir(?:y|ation)|end of (?:the )?(?:initial |current )?term|renewal date"
        r"|termination date",
        "expiry",
    ),
    (r"effective date|commencement|start date", "effective"),
    (r"sign(?:ing|ature|ed)|execut(?:ion|ed)|date of this agreement|date hereof|hereof", "signing"),
    (r"the date of this letter|today", "today"),
]

DEADLINE_TRIGGERS = (
    r"(?:by|before|until|no later than|not later than|on or before|on or prior to|prior to|"
    r"due(?: on| by)?|deadline(?: is| of)?|expires? on|expir(?:es|ing) on|terminates? on|"
    r"at the latest|payable on|effective (?:from|as of))"
)

# passive participle -> imperative verb for action titles
VERB_MAP = {
    "submitted": "Submit",
    "paid": "Pay",
    "delivered": "Deliver",
    "provided": "Provide",
    "returned": "Return",
    "signed": "Sign",
    "completed": "Complete",
    "notified": "Notify",
    "made": "Make",
    "given": "Give",
    "sent": "Send",
    "filed": "File",
    "supplied": "Supply",
    "received": "Ensure receipt of",
    "settled": "Settle",
    "executed": "Execute",
    "remedied": "Remedy",
    "cured": "Cure",
    "issued": "Issue",
    "renewed": "Renew",
    "terminated": "Terminate",
    "vacated": "Vacate",
    "removed": "Remove",
    "installed": "Install",
    "reported": "Report",
    "confirmed": "Confirm",
    "replaced": "Replace",
    "repaired": "Repair",
    "responded": "Respond",
    "answered": "Answer",
    "raised": "Raise",
    "registered": "Register",
}

SENTENCE_SPLIT = re.compile(
    r"(?:(?<=[A-Za-z)\"'”][.;!?])|(?<=\d{4}[.;!?]))\s+(?=[A-Z(\"'“])"  # end of sentence
    r"|\n{2,}"  # paragraph break
    r"|\n(?=\s*(?:\d+[.)]|[a-z][.)]|[-•*]))"  # next list item
)

# --------------------------------------------------------------------------- patterns

RELATIVE_RE = re.compile(
    r"(?P<quote>(?:within|no later than|not later than|at the latest|in|after|following|"
    r"at least|not less than|a minimum of|a period of)\s+"
    rf"{NUM}\s*(?:\(\d+\)\s*)?{UNIT}\s+"
    r"(?P<dir>of|from|after|following|before|prior to|preceding|in advance of)\s+"
    r"(?:the\s+)?(?:date\s+of\s+)?(?:the\s+)?(?P<anchor>[\w'’\s]{3,45}?))(?=[,.;:)]|\s+(?:and|or|by|to|in|at|for|unless|which|that)\b|$)",
    re.IGNORECASE,
)

NOTICE_RE = re.compile(
    rf"(?P<quote>(?:{NUM}\s*(?:\(\d+\)\s*)?{UNIT}['’]?\s*(?:prior\s+|advance\s+)?(?:written\s+)?notice)"
    rf"|(?:notice\s+(?:period\s+)?of\s+(?:at\s+least\s+|not\s+less\s+than\s+)?(?P<num2>\d{{1,3}}|{'|'.join(NUMBER_WORDS)})\s*(?:\(\d+\)\s*)?(?P<unit2>(?:calendar\s+|business\s+|working\s+)?(?:days?|weeks?|months?))))",
    re.IGNORECASE,
)

ABS_DATE_RE = re.compile(
    rf"(?P<quote>(?:{DEADLINE_TRIGGERS})\s+(?:the\s+)?"
    r"(?P<date>"
    rf"(?:\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{MONTH_RE})\.?,?\s+\d{{4}})"  # 30 September 2026
    rf"|(?:(?:{MONTH_RE})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}})"  # September 30, 2026
    r"|(?:\d{4}-\d{2}-\d{2})"  # 2026-09-30
    r"|(?:\d{1,2}[./]\d{1,2}[./]\d{4})"  # 30.09.2026 / 30/09/2026 (day first)
    r"))",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- helpers


def _num(token: str) -> int:
    token = token.lower().strip()
    return int(token) if token.isdigit() else NUMBER_WORDS[token]


def _unit(token: str) -> Unit:
    t = token.lower()
    if "business" in t or "working" in t:
        return Unit.BUSINESS_DAYS
    if t.rstrip("s").endswith("day"):
        return Unit.DAYS
    if "week" in t:
        return Unit.WEEKS
    if "month" in t:
        return Unit.MONTHS
    return Unit.YEARS


def _anchor(phrase: str) -> str | None:
    p = phrase.lower()
    for pattern, key in ANCHOR_PATTERNS:
        if re.search(pattern, p):
            return key
    return None


def parse_absolute_date(raw: str) -> date | None:
    raw = raw.strip().rstrip(".,")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        y, mo, d = map(int, m.groups())
        return _safe_date(y, mo, d)
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", raw)
    if m:
        d, mo, y = map(int, m.groups())
        return _safe_date(y, mo, d)
    m = re.fullmatch(
        rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({MONTH_RE})\.?,?\s+(\d{{4}})", raw, re.I
    )
    if m:
        return _safe_date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
    m = re.fullmatch(rf"({MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", raw, re.I)
    if m:
        return _safe_date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
    return None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def split_sentences(text: str) -> list[str]:
    parts = SENTENCE_SPLIT.split(text)
    return [re.sub(r"\s+", " ", p).strip() for p in parts if p and p.strip()]


LEADING_CLAUSE = re.compile(
    r"^(?:upon|on|after|following|should|if|where|in case|in the event|unless|subject to)\b"
    r"[^,]{1,80},\s*",
    re.I,
)


def derive_action(sentence: str, quote: str, replacement: str = " ") -> str:
    """Best-effort imperative title. Falls back to a trimmed sentence."""
    lead = r"(?:at\s+least|not\s+less\s+than|a\s+minimum\s+of)\s+"
    if replacement.strip() == "":
        lead = rf"(?:(?:by|upon)\s+(?:giving|providing|serving|sending)\s+|{lead})"
    s = re.sub(rf"(?:{lead})?" + re.escape(quote), replacement, sentence, count=1)
    s = re.sub(r"^\s*(?:\d+[.)]|[a-z][.)]|[-•*])\s*", "", s)  # list markers
    s = LEADING_CLAUSE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" ,.;:")

    # passive: "<Object> must/shall be <participle> ..."
    m = re.match(
        r"^(?:the\s+|all\s+|any\s+)?(?P<obj>.+?)\s+(?:must|shall|should|will|is to|are to|"
        r"is required to|are required to|needs? to)\s+be\s+(?P<verb>\w+)\b(?P<rest>.*)$",
        s,
        re.I,
    )
    if m and m.group("verb").lower() in VERB_MAP:
        obj = _strip_subject_noise(m.group("obj"))
        rest = _trim_rest(m.group("rest"))
        return _cap(f"{VERB_MAP[m.group('verb').lower()]} {obj}{rest}")

    # active: "<Subject> must/shall <verb phrase>"
    m = re.match(
        r"^(?:the\s+)?(?P<subj>(?:[A-Z][\w\s]{0,30}?|you|we|they|it))\s+"
        r"(?:must|shall|should|will|is to|are to|agrees? to|undertakes? to|is required to|"
        r"are required to|may)\s+(?P<vp>.+)$",
        s,
    )
    if m:
        return _cap(_trim_rest(m.group("vp"), lead=False))

    # "Either party may terminate ... by giving" style already handled; generic fallback
    return _cap(_trim_rest(s, lead=False))


TRAILING_NOISE = re.compile(
    r"(?:\s+(?:by|upon|on|with|of|to|from|at|in|for|giving|is|are|be|shall|must|will|"
    r"shall be|must be|will be|is due|are due|due))+$",
    re.I,
)


def _strip_subject_noise(obj: str) -> str:
    obj = re.sub(r"^(?:the|all|any|such)\s+", "", obj.strip(), flags=re.I)
    # "Documents" at sentence start -> "documents" (keep acronyms / proper nouns like "VAT")
    if len(obj) > 1 and obj[0].isupper() and obj[1:].split(" ")[0].islower():
        obj = obj[0].lower() + obj[1:]
    return obj


def _trim_rest(rest: str, lead: bool = True) -> str:
    rest = rest.strip(" ,.;:")
    rest = TRAILING_NOISE.sub("", rest).strip(" ,.;:")
    rest = re.sub(r"\s+", " ", rest)
    if len(rest) > 70:
        rest = rest[:67].rsplit(" ", 1)[0] + "…"
    return (" " + rest if rest and lead else rest) if lead else rest


def _cap(s: str) -> str:
    s = s.strip(" ,.;:")
    return s[:1].upper() + s[1:] if s else "Deadline"


# --------------------------------------------------------------------------- extractor


class RuleBasedExtractor:
    def extract(self, text: str) -> list[Deadline]:
        found: list[Deadline] = []
        for sentence in split_sentences(text):
            found.extend(self._extract_sentence(sentence))
        return found

    def _extract_sentence(self, sentence: str) -> list[Deadline]:
        results: list[Deadline] = []
        used: list[tuple[int, int]] = []

        def overlaps(span: tuple[int, int]) -> bool:
            return any(a < span[1] and span[0] < b for a, b in used)

        for m in NOTICE_RE.finditer(sentence):
            if overlaps(m.span()):
                continue
            used.append(m.span())
            num = m.group("num") or m.group("num2")
            unit = m.group("unit") or m.group("unit2")
            quote = m.group("quote")
            results.append(
                Deadline(
                    action=derive_action(sentence, quote, replacement=" notice "),
                    source_quote=quote,
                    context=sentence,
                    kind=DeadlineKind.NOTICE,
                    offset_value=_num(num),
                    offset_unit=_unit(unit),
                    direction=Direction.AFTER,
                    anchor="notice",
                    note="Earliest date the notice takes effect, counted from the day notice "
                    "is given.",
                )
            )

        for m in RELATIVE_RE.finditer(sentence):
            if overlaps(m.span()):
                continue
            used.append(m.span())
            quote = m.group("quote").rstrip(" ,.;:")
            direction = (
                Direction.BEFORE
                if m.group("dir").lower() in {"before", "prior to", "preceding", "in advance of"}
                else Direction.AFTER
            )
            anchor = _anchor(m.group("anchor"))
            note = (
                ""
                if anchor
                else (
                    f"Reference event not recognised: “{m.group('anchor').strip()}”. "
                    "Set it manually."
                )
            )
            results.append(
                Deadline(
                    action=derive_action(sentence, quote),
                    source_quote=quote,
                    context=sentence,
                    kind=DeadlineKind.RELATIVE,
                    offset_value=_num(m.group("num")),
                    offset_unit=_unit(m.group("unit")),
                    direction=direction,
                    anchor=anchor,
                    note=note,
                )
            )

        for m in ABS_DATE_RE.finditer(sentence):
            if overlaps(m.span()):
                continue
            parsed = parse_absolute_date(m.group("date"))
            used.append(m.span())
            quote = m.group("quote")
            results.append(
                Deadline(
                    action=derive_action(sentence, quote),
                    source_quote=quote,
                    context=sentence,
                    kind=DeadlineKind.ABSOLUTE,
                    absolute_date=parsed,
                    note="" if parsed else "Date could not be parsed. Enter it manually.",
                )
            )
        return results
