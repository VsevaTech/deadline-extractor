from datetime import date

import pytest

from app import date_engine
from app.extraction import RuleBasedExtractor
from app.extraction.rules import parse_absolute_date, split_sentences
from app.models import DeadlineKind, Direction, Status, Unit

ex = RuleBasedExtractor()


def one(text: str):
    found = ex.extract(text)
    assert len(found) == 1, [d.source_quote for d in found]
    return found[0]


def test_spec_example_within_n_days_from_receipt():
    d = one("Documents must be submitted within 14 days from receipt.")
    assert d.kind == DeadlineKind.RELATIVE
    assert d.offset_value == 14 and d.offset_unit == Unit.DAYS
    assert d.anchor == "receipt" and d.direction == Direction.AFTER
    assert d.source_quote == "within 14 days from receipt"
    assert d.action == "Submit documents"
    computed = date_engine.compute(d, {"receipt": date(2026, 9, 14)})
    assert computed.computed_date == date(2026, 9, 28)
    assert computed.status == Status.COMPUTED


def test_missing_reference_date_is_needs_input():
    d = date_engine.compute(one("Documents must be submitted within 14 days from receipt."), {})
    assert d.status == Status.NEEDS_INPUT
    assert d.computed_date is None
    assert "receipt" in d.note


def test_unknown_anchor_is_needs_input():
    d = one("The Tenant shall vacate the premises within 2 months following termination.")
    assert d.anchor is None
    assert "termination" in d.note
    assert date_engine.compute(d, {"receipt": date(2026, 1, 1)}).status == Status.NEEDS_INPUT


@pytest.mark.parametrize(
    "text, expected",
    [
        ("The fee is payable no later than 30 September 2026.", date(2026, 9, 30)),
        ("Reports must be filed by October 15th, 2026.", date(2026, 10, 15)),
        ("Payment is due on 2026-10-01.", date(2026, 10, 1)),
        ("The offer expires on 31.12.2026.", date(2026, 12, 31)),
        ("Respond on or before 5th of March 2027.", date(2027, 3, 5)),
        ("Delivery deadline is Sept. 3, 2026.", date(2026, 9, 3)),
    ],
)
def test_absolute_dates(text, expected):
    d = one(text)
    assert d.kind == DeadlineKind.ABSOLUTE
    assert d.absolute_date == expected
    assert date_engine.compute(d, {}).computed_date == expected


def test_absolute_date_needs_deadline_trigger():
    # Dates without deadline language are not obligations.
    assert ex.extract("This Agreement is dated 1 January 2026 between the parties.") == []


def test_invalid_absolute_date_flagged():
    d = one("Payment is due on 31.02.2026.")
    assert d.absolute_date is None
    assert date_engine.compute(d, {}).status == Status.NEEDS_INPUT


def test_notice_period_written_form():
    d = one("Either party may terminate this Agreement by giving thirty (30) days' written notice.")
    assert d.kind == DeadlineKind.NOTICE
    assert d.offset_value == 30 and d.offset_unit == Unit.DAYS
    assert d.anchor == "notice"
    assert d.action == "Terminate this Agreement by giving notice"
    assert date_engine.compute(d, {"notice": date(2026, 9, 14)}).computed_date == date(2026, 10, 14)


def test_notice_period_of_form():
    d = one("The Landlord must provide a notice period of at least 2 months.")
    assert d.kind == DeadlineKind.NOTICE
    assert d.offset_value == 2 and d.offset_unit == Unit.MONTHS


def test_before_anchor_direction():
    d = one("Any renewal request must be made at least 60 days prior to the expiry of the term.")
    assert d.direction == Direction.BEFORE and d.anchor == "expiry"
    assert date_engine.compute(d, {"expiry": date(2026, 12, 31)}).computed_date == date(2026, 11, 1)


def test_business_days_unit():
    d = one("The Supplier shall deliver the goods within 5 business days of the effective date.")
    assert d.offset_unit == Unit.BUSINESS_DAYS and d.anchor == "effective"
    # Fri 18 Sep 2026 + 5 business days = Fri 25 Sep 2026
    assert date_engine.compute(d, {"effective": date(2026, 9, 18)}).computed_date == date(
        2026, 9, 25
    )


def test_number_words():
    d = one("Objections must be raised within ten days of receipt.")
    assert d.offset_value == 10


def test_multiple_deadlines_in_document():
    text = """1. Documents must be submitted within 14 days from receipt.
2. The Buyer shall pay each invoice no later than 30 September 2026.
3. Either party may terminate this Agreement by giving 30 days' written notice."""
    found = ex.extract(text)
    assert [d.kind for d in found] == [
        DeadlineKind.RELATIVE,
        DeadlineKind.ABSOLUTE,
        DeadlineKind.NOTICE,
    ]
    computed = date_engine.compute_all(
        found, {"receipt": date(2026, 9, 14), "notice": date(2026, 9, 14)}
    )
    assert [d.computed_date for d in computed] == [
        date(2026, 9, 28),
        date(2026, 9, 30),
        date(2026, 10, 14),
    ]


def test_two_deadlines_in_one_sentence():
    found = ex.extract(
        "The report is due within 10 days of receipt and the fee must be paid "
        "within 30 days of the invoice."
    )
    assert len(found) == 2
    assert {d.anchor for d in found} == {"receipt", "invoice"}


def test_document_without_deadlines():
    assert ex.extract("This document describes the general scope of services. Nothing more.") == []


def test_sentence_splitting_handles_numbered_clauses():
    parts = split_sentences("1. First clause.\n2. Second clause\n3. Third")
    assert len(parts) == 3


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-02-29", None),  # not a leap year
        ("29 February 2028", date(2028, 2, 29)),
        ("1st of May 2027", date(2027, 5, 1)),
        ("13/01/2027", date(2027, 1, 13)),
    ],
)
def test_parse_absolute_date(raw, expected):
    assert parse_absolute_date(raw) == expected
