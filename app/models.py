"""Domain models. Extraction produces `Deadline` objects; the date engine fills `computed_date`."""

from __future__ import annotations

import uuid
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class DeadlineKind(StrEnum):
    ABSOLUTE = "absolute"  # "by 30 September 2026"
    RELATIVE = "relative"  # "within 14 days from receipt"
    NOTICE = "notice"  # "30 days' written notice"


class Unit(StrEnum):
    DAYS = "days"
    BUSINESS_DAYS = "business_days"
    WEEKS = "weeks"
    MONTHS = "months"
    YEARS = "years"


class Direction(StrEnum):
    AFTER = "after"  # anchor + offset
    BEFORE = "before"  # anchor - offset ("at least 30 days prior to expiry")


class Status(StrEnum):
    COMPUTED = "computed"
    NEEDS_INPUT = "needs_input"
    CONFIRMED = "confirmed"


# Canonical anchor keys. The UI asks the user for these dates.
ANCHOR_LABELS: dict[str, str] = {
    "receipt": "Document received",
    "signing": "Agreement signed",
    "effective": "Effective date",
    "invoice": "Invoice date",
    "notice": "Notice given",
    "delivery": "Delivery date",
    "expiry": "Expiry / end of term",
    "today": "Today",
    "other": "Other reference event",
}


class Deadline(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    action: str
    source_quote: str  # exact phrase that triggered the deadline
    context: str = ""  # full sentence the quote was taken from
    kind: DeadlineKind
    # absolute
    absolute_date: date | None = None
    # relative / notice
    offset_value: int | None = None
    offset_unit: Unit | None = None
    direction: Direction = Direction.AFTER
    anchor: str | None = None  # key from ANCHOR_LABELS
    # result
    computed_date: date | None = None
    status: Status = Status.NEEDS_INPUT
    note: str = ""
    confirmed: bool = False

    def describe_rule(self) -> str:
        if self.kind == DeadlineKind.ABSOLUTE:
            return f"fixed date {self.absolute_date.isoformat() if self.absolute_date else '?'}"
        unit = (self.offset_unit or "?").replace("_", " ")
        anchor = ANCHOR_LABELS.get(self.anchor or "", self.anchor or "?")
        arrow = "+" if self.direction == Direction.AFTER else "-"
        return f"{anchor} {arrow} {self.offset_value} {unit}"


class ExtractionResult(BaseModel):
    text_length: int
    deadlines: list[Deadline]
    required_anchors: list[str]
