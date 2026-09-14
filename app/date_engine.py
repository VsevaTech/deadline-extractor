"""Deterministic date arithmetic. No AI, no heuristics — pure calendar math.

Conventions (documented in README):
* "within N days from X" -> X + N calendar days (day of X is day 0).
* business days skip Saturday and Sunday only (no public-holiday calendar).
* months/years use calendar arithmetic with end-of-month clamping (31 Jan + 1 month = 28/29 Feb).
* "before/prior to X" -> X - N units.
* No weekend roll-forward is applied; a note is attached instead.
"""

from __future__ import annotations

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from app.models import Deadline, DeadlineKind, Direction, Status, Unit

WEEKEND = {5, 6}


def add_business_days(start: date, n: int) -> date:
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    current = start
    while remaining:
        current += timedelta(days=step)
        if current.weekday() not in WEEKEND:
            remaining -= 1
    return current


def shift(anchor: date, value: int, unit: Unit, direction: Direction) -> date:
    signed = value if direction == Direction.AFTER else -value
    if unit == Unit.DAYS:
        return anchor + timedelta(days=signed)
    if unit == Unit.WEEKS:
        return anchor + timedelta(weeks=signed)
    if unit == Unit.BUSINESS_DAYS:
        return add_business_days(anchor, signed)
    if unit == Unit.MONTHS:
        return anchor + relativedelta(months=signed)
    if unit == Unit.YEARS:
        return anchor + relativedelta(years=signed)
    raise ValueError(f"Unknown unit {unit}")  # pragma: no cover


def compute(deadline: Deadline, anchors: dict[str, date]) -> Deadline:
    """Return a copy of `deadline` with computed_date / status filled in."""
    d = deadline.model_copy()
    was_confirmed = d.confirmed
    if d.kind == DeadlineKind.ABSOLUTE:
        if d.absolute_date is None:
            d.computed_date, d.status = None, Status.NEEDS_INPUT
            d.note = d.note or "Date is missing."
        else:
            d.computed_date = d.absolute_date
            d.status = Status.COMPUTED
    else:
        if d.offset_value is None or d.offset_unit is None:
            d.computed_date, d.status = None, Status.NEEDS_INPUT
            d.note = d.note or "Offset is missing."
        elif not d.anchor or d.anchor not in anchors:
            d.computed_date, d.status = None, Status.NEEDS_INPUT
            if not d.anchor:
                d.note = d.note or "Reference date is unknown — choose one."
            else:
                d.note = f"Enter the “{d.anchor}” date to calculate."
        else:
            d.computed_date = shift(anchors[d.anchor], d.offset_value, d.offset_unit, d.direction)
            d.status = Status.COMPUTED
            d.note = _weekend_note(d.computed_date) or _default_note(d)
    if d.status == Status.COMPUTED and was_confirmed:
        d.status = Status.CONFIRMED
    else:
        d.confirmed = False
    return d


def _weekend_note(day: date) -> str:
    if day.weekday() in WEEKEND:
        return f"Falls on a {day.strftime('%A')} — check the contract's day-counting rule."
    return ""


def _default_note(d: Deadline) -> str:
    if d.kind == DeadlineKind.NOTICE:
        return "Earliest effective date if notice is given on the reference date."
    return ""


def compute_all(deadlines: list[Deadline], anchors: dict[str, date]) -> list[Deadline]:
    return [compute(d, anchors) for d in deadlines]
