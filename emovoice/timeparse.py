"""Natural-language time parsing for reminders and timers.

    parse_reminder("remind me to call mom at 5:30 p.m.", now) -> ("call mom", datetime)
    parse_minutes("45 minutes") -> 45.0
    say_time(dt, now) -> "at 5:30 PM" / "tomorrow at 9 AM" / "in 10 minutes"

Speech-to-text output is messy, so spoken numbers ("in five minutes"), dotted
am/pm ("p.m."), spaced times ("5 30 pm") and time-before-message ordering are all
accepted.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_PART_DEFAULTS = {"morning": (9, 0), "afternoon": (15, 0), "evening": (18, 0), "night": (20, 0)}
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

_UNIT = r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)"
_REL = re.compile(
    rf"\b(?:in|after|within)\s+(\d+(?:\.\d+)?)\s*{_UNIT}(?:\s+(?:and\s+)?(\d+)\s*(minutes?|mins?|seconds?|secs?))?\b"
)
_DAY = re.compile(r"\b(?:on\s+)?(?:(?:this|next)\s+)?(today|tonight|tomorrow|" + "|".join(_WEEKDAYS) + r")\b")
_PART = re.compile(r"\b(?:this\s+|in\s+the\s+|at\s+)?(morning|afternoon|evening|night)\b")
_CLOCK_SPACED = re.compile(r"\b(?:at|by|around)\s+(\d{1,2})\s+([0-5]\d)(?:\s*(am|pm))?(?![\d:.])")
_CLOCK = re.compile(
    r"(?P<prep>\b(?:at|by|around)\s+)?"
    r"(?<![\d:.])(?P<h>\d{1,2})(?:[:.](?P<m>\d{2})|\s(?P<m2>[0-5]\d)(?=\s*(?:am|pm)\b))?"
    r"(?:\s*(?P<ampm>am|pm))?(?![\d:])\b"
)


def words_to_numbers(text: str) -> str:
    """'twenty five' -> '25', 'five thirty' -> '5 30'. Other words are untouched."""
    tokens = text.split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _TENS:
            value = _TENS[tok]
            if i + 1 < len(tokens) and tokens[i + 1] in _UNITS and 0 < _UNITS[tokens[i + 1]] < 10:
                value += _UNITS[tokens[i + 1]]
                i += 1
            out.append(str(value))
        elif tok in _UNITS:
            out.append(str(_UNITS[tok]))
        else:
            out.append(tok)
        i += 1
    return " ".join(out)


def normalize(text: str) -> str:
    s = text.lower().replace("’", "'")
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", s)          # twenty-five
    s = re.sub(r"[,!?;\"]", " ", s)
    s = words_to_numbers(" ".join(s.split()))
    s = re.sub(r"\bo'?\s?clock\b", "", s)
    s = re.sub(r"(?<=\d)\s*([ap])\.?\s?m\b\.?", r" \1m", s)  # 5pm, 5 p.m., 5 p m
    # durations
    s = re.sub(r"\b(?:an?\s+)?hour\s+and\s+a\s+half\b", "90 minutes", s)
    s = re.sub(r"\b(\d+)\s+and\s+a\s+half\s+(hours?|minutes?)\b", r"\1.5 \2", s)
    s = re.sub(r"\bhalf\s+(?:an\s+)?hour\b", "30 minutes", s)
    s = re.sub(r"\b(?:a\s+)?quarter\s+(?:of\s+an\s+)?hour\b", "15 minutes", s)
    s = re.sub(r"\ba\s+couple\s+(?:of\s+)?(minutes|hours|days)\b", r"2 \1", s)
    s = re.sub(r"\ba\s+few\s+(minutes|hours|days)\b", r"3 \1", s)
    s = re.sub(r"\ban?\s+(second|minute|hour|day|week)\b", r"1 \1", s)
    # clock phrases
    s = re.sub(r"\bhalf\s+past\s+(\d{1,2})\b", r"\1:30", s)
    s = re.sub(r"\bquarter\s+past\s+(\d{1,2})\b", r"\1:15", s)
    s = re.sub(r"\b(?:a\s+)?quarter\s+to\s+(\d{1,2})\b",
               lambda m: f"{(int(m.group(1)) - 1) or 12}:45", s)
    s = re.sub(r"\b(?:12\s+)?(?:noon|midday)\b", "12:00 pm", s)
    s = re.sub(r"\b(?:12\s+)?midnight\b", "12:00 am", s)
    return " ".join(s.split())


def _unit_seconds(unit: str) -> int:
    return _UNIT_SECONDS[unit[0]] if not unit.startswith("mi") else 60


def _find_time(s: str, now: datetime):
    """Return (due, spans) for the time expression in normalized text s, or None."""
    rel = _REL.search(s)
    if rel:
        seconds = float(rel.group(1)) * _unit_seconds(rel.group(2))
        if rel.group(3):
            seconds += int(rel.group(3)) * _unit_seconds(rel.group(4))
        if seconds <= 0:
            return None
        return now + timedelta(seconds=seconds), [rel.span()]

    spans = []
    day = _DAY.search(s)
    part_name = None
    day_offset = 0
    explicit_day = False
    if day:
        spans.append(day.span())
        word = day.group(1)
        explicit_day = word != "today"
        if word == "tomorrow":
            day_offset = 1
        elif word == "tonight":
            part_name = "night"
        elif word in _WEEKDAYS:
            day_offset = (_WEEKDAYS.index(word) - now.weekday()) % 7 or 7

    part = _PART.search(s)
    if part and not _overlaps(part.span(), spans):
        spans.append(part.span())
        part_name = part.group(1)

    hour = minute = None
    ampm = None
    m = _CLOCK_SPACED.search(s)
    if m and not _overlaps(m.span(), spans):
        hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
        spans.append(m.span())
    else:
        for m in _CLOCK.finditer(s):
            if _overlaps(m.span(), spans):
                continue
            if not (m.group("prep") or m.group("m") or m.group("m2") or m.group("ampm")):
                continue  # a bare number ("buy 2 apples") is not a time
            h = int(m.group("h"))
            mm = int(m.group("m") or m.group("m2") or 0)
            if mm > 59 or h > 23 or (m.group("ampm") and not 1 <= h <= 12):
                continue
            hour, minute, ampm = h, mm, m.group("ampm")
            leading_zero = m.group("h").startswith("0") and len(m.group("h")) == 2
            if leading_zero and not ampm:
                ampm = "24h"
            spans.append(m.span())
            break

    if hour is None and part_name is None and not (day and day.group(1) != "today"):
        return None

    base = now.date() + timedelta(days=day_offset)

    if hour is None:
        hour, minute = _PART_DEFAULTS.get(part_name, (9, 0))
        due = _at(base, hour, minute)
        if due <= now:
            due = max(due, now + timedelta(hours=1)) if day_offset == 0 and part_name else due + timedelta(days=1)
        return due, spans

    if ampm == "am":
        hour = hour % 12
    elif ampm == "pm":
        hour = hour % 12 + 12
    elif ampm is None and 1 <= hour <= 12:
        if part_name:
            if part_name == "morning":
                hour = hour % 12
            elif hour < 12:
                hour += 12
        elif day_offset or explicit_day:
            hour = hour + 12 if hour <= 6 else hour  # "tomorrow at 3" -> 3 PM, "tomorrow at 9" -> 9 AM
        else:
            # "at 3": the next occurrence of 3:00 or 15:00, preferring waking hours (7:00-22:59)
            candidates = sorted(
                c for c in (
                    _at(d, h, minute)
                    for d in (now.date(), now.date() + timedelta(days=1))
                    for h in {hour % 12, hour % 12 + 12}
                ) if c > now
            )
            awake = [c for c in candidates if 7 <= c.hour <= 22]
            return (awake or candidates)[0], spans

    due = _at(base, hour, minute)
    if due <= now and day_offset == 0:
        due += timedelta(days=1)
    return due, spans


def _at(d: date, hour: int, minute: int) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute)


def _overlaps(span, spans) -> bool:
    return any(span[0] < e and s < span[1] for s, e in spans)


def _remove_spans(s: str, spans) -> str:
    for start, end in sorted(spans, reverse=True):
        s = s[:start] + " " + s[end:]
    return " ".join(s.split())


_LEAD = re.compile(
    r"^(?:(?:please|hey|ok|okay|can you|could you|would you|will you)\s+)*"
    r"(?:remind\s+me|set\s+(?:a\s+|an\s+)?reminder|add\s+(?:a\s+)?reminder|reminder)\b\s*"
)
_EDGE_WORDS = r"(?:to|that|about|of|for|at|on|by|in|and|then|please|me)"


def clean_message(s: str) -> str:
    """Strip the 'remind me ... to' scaffolding from normalized text."""
    s = " ".join(s.split())
    lead = _LEAD.match(s)
    if lead:
        s = s[lead.end():]
    else:  # time came first: "at 5 pm remind me to call mom"
        s = re.sub(r"\b(?:remind\s+me|set\s+(?:a\s+|an\s+)?reminder)\b", " ", s, count=1)
    s = " ".join(s.split())
    previous = None
    while previous != s:
        previous = s
        s = re.sub(rf"^{_EDGE_WORDS}\b\s*", "", s)
        s = re.sub(rf"\s*\b(?:to|at|on|by|in|and|for|then|please)$", "", s)
        s = s.strip(" .:-")
    return s


def reminder_message(text: str) -> str:
    return clean_message(normalize(text))


def parse_reminder(text: str, now: datetime) -> tuple[str, datetime] | None:
    s = normalize(text)
    found = _find_time(s, now)
    if not found:
        return None
    due, spans = found
    return clean_message(_remove_spans(s, spans)), due


def parse_time(text: str, now: datetime) -> datetime | None:
    """Just the time part of an answer like 'at 5 pm' or 'in ten minutes'."""
    found = _find_time(normalize(text), now)
    return found[0] if found else None


def parse_minutes(text: str) -> float | None:
    """'45 minutes' -> 45, 'an hour and a half' -> 90, 'twenty' -> 20."""
    if not text:
        return None
    s = normalize(text)
    total = 0.0
    found = False
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", s):
        value, unit = float(m.group(1)), m.group(2)
        total += value * (60 if unit.startswith("h") else 1 / 60 if unit.startswith("s") else 1)
        found = True
    if found:
        return total
    m = re.search(r"\b(\d+(?:\.\d+)?)\b", s)
    return float(m.group(1)) if m else None


def format_clock(dt: datetime) -> str:
    """'5:30 PM', '9 AM', 'noon'."""
    if dt.minute == 0 and dt.hour in (0, 12):
        return "midnight" if dt.hour == 0 else "noon"
    hour = dt.hour % 12 or 12
    suffix = "AM" if dt.hour < 12 else "PM"
    return f"{hour} {suffix}" if dt.minute == 0 else f"{hour}:{dt.minute:02d} {suffix}"


def say_time(dt: datetime, now: datetime) -> str:
    """A phrase that fits after 'I'll remind you': 'in 10 minutes', 'at 5 PM', 'tomorrow at 9 AM'."""
    seconds = (dt - now).total_seconds()
    if 0 < seconds < 3600:
        minutes = round(seconds / 60)
        if minutes < 1:
            secs = max(1, round(seconds))
            return f"in {secs} second{'s' if secs != 1 else ''}"
        return f"in {minutes} minute{'s' if minutes != 1 else ''}"
    clock = format_clock(dt)
    days = (dt.date() - now.date()).days
    if days == 0:
        return f"at {clock}"
    if days == 1:
        return f"tomorrow at {clock}"
    if 1 < days < 7:
        return f"on {dt:%A} at {clock}"
    return f"on {dt:%B} {dt.day} at {clock}"
