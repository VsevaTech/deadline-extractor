from datetime import date

import pytest

from app.date_engine import add_business_days, compute, shift
from app.models import Deadline, DeadlineKind, Direction, Status, Unit


def rel(value, unit, direction=Direction.AFTER, anchor="receipt", **kw):
    return Deadline(
        action="x",
        source_quote="q",
        kind=DeadlineKind.RELATIVE,
        offset_value=value,
        offset_unit=unit,
        direction=direction,
        anchor=anchor,
        **kw,
    )


@pytest.mark.parametrize(
    "start, value, unit, direction, expected",
    [
        (date(2026, 9, 14), 14, Unit.DAYS, Direction.AFTER, date(2026, 9, 28)),
        (date(2026, 12, 25), 10, Unit.DAYS, Direction.AFTER, date(2027, 1, 4)),  # year boundary
        (date(2026, 9, 14), 2, Unit.WEEKS, Direction.AFTER, date(2026, 9, 28)),
        (date(2026, 1, 31), 1, Unit.MONTHS, Direction.AFTER, date(2026, 2, 28)),  # month-end clamp
        (date(2028, 1, 31), 1, Unit.MONTHS, Direction.AFTER, date(2028, 2, 29)),  # leap year
        (date(2026, 3, 31), 1, Unit.MONTHS, Direction.BEFORE, date(2026, 2, 28)),
        (date(2028, 2, 29), 1, Unit.YEARS, Direction.AFTER, date(2029, 2, 28)),
        (date(2026, 12, 31), 60, Unit.DAYS, Direction.BEFORE, date(2026, 11, 1)),
        (date(2026, 9, 18), 5, Unit.BUSINESS_DAYS, Direction.AFTER, date(2026, 9, 25)),  # Fri->Fri
        (date(2026, 9, 12), 1, Unit.BUSINESS_DAYS, Direction.AFTER, date(2026, 9, 14)),  # Sat->Mon
        (date(2026, 9, 14), 1, Unit.BUSINESS_DAYS, Direction.BEFORE, date(2026, 9, 11)),  # Mon->Fri
        (date(2026, 9, 14), 0, Unit.DAYS, Direction.AFTER, date(2026, 9, 14)),
    ],
)
def test_shift(start, value, unit, direction, expected):
    assert shift(start, value, unit, direction) == expected


def test_add_business_days_never_lands_on_weekend():
    for n in range(1, 30):
        assert add_business_days(date(2026, 9, 14), n).weekday() < 5


def test_compute_relative_ok():
    d = compute(rel(14, Unit.DAYS), {"receipt": date(2026, 9, 14)})
    assert d.computed_date == date(2026, 9, 28) and d.status == Status.COMPUTED


def test_compute_missing_anchor():
    d = compute(rel(14, Unit.DAYS), {})
    assert d.status == Status.NEEDS_INPUT and d.computed_date is None


def test_compute_missing_offset():
    d = compute(rel(None, None), {"receipt": date(2026, 9, 14)})
    assert d.status == Status.NEEDS_INPUT


def test_weekend_note():
    d = compute(rel(5, Unit.DAYS), {"receipt": date(2026, 9, 14)})  # Sat 19 Sep
    assert d.computed_date == date(2026, 9, 19)
    assert "Saturday" in d.note


def test_confirmed_status_kept_only_when_computable():
    d = compute(rel(14, Unit.DAYS, confirmed=True), {"receipt": date(2026, 9, 14)})
    assert d.status == Status.CONFIRMED
    d2 = compute(rel(14, Unit.DAYS, confirmed=True), {})
    assert d2.status == Status.NEEDS_INPUT and d2.confirmed is False


def test_absolute_without_date():
    d = Deadline(action="x", source_quote="q", kind=DeadlineKind.ABSOLUTE)
    assert compute(d, {}).status == Status.NEEDS_INPUT
