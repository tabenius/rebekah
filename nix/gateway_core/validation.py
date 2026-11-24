"""Pure bounded projection validators; no routing, I/O or authentication."""
from datetime import datetime


def agenda_text(value, maximum=12000):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("invalid agenda text")
    return value[:maximum]


def agenda_time(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid agenda time")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("agenda time requires zone")
    return stamp


def agenda_count(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100000:
        raise ValueError("invalid agenda count")
    return value
