"""Export confirmed deadlines as an iCalendar file (all-day events)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from icalendar import Alarm, Calendar, Event

from app.models import Deadline


def build_calendar(deadlines: list[Deadline], title: str = "Deadlines") -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Deadline Extractor//deadline-extractor//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", title)
    now = datetime.now(UTC)
    for d in deadlines:
        if d.computed_date is None:
            continue
        ev = Event()
        ev.add("uid", f"{d.id}@deadline-extractor")
        ev.add("dtstamp", now)
        ev.add("summary", d.action)
        ev.add("dtstart", d.computed_date)
        ev.add("dtend", d.computed_date + timedelta(days=1))
        description = f'Source: "{d.source_quote}"\nRule: {d.describe_rule()}'
        if d.context and d.context != d.source_quote:
            description += f"\nContext: {d.context}"
        if d.note:
            description += f"\nNote: {d.note}"
        ev.add("description", description)
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", f"Deadline tomorrow: {d.action}")
        alarm.add("trigger", timedelta(days=-1))
        ev.add_component(alarm)
        cal.add_component(ev)
    return cal.to_ical()
