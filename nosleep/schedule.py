"""Pure schedule logic. No Qt here so it stays unit-testable.

All comparisons are done on local wall-clock ``time`` values, never on epoch
seconds, so DST shifts don't move the window around.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


def in_window(now: time, start: time, end: time) -> bool:
    """True if ``now`` falls inside [start, end).

    ``start == end`` means the window covers the whole day (always true).
    ``start > end`` means the window crosses midnight (e.g. 21:00 -> 02:00, or
    09:00 -> 00:00 where midnight is stored as 00:00).
    """
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def _combine(day: date, t: time, like: datetime) -> datetime:
    return datetime.combine(day, t, tzinfo=like.tzinfo)


def next_transition(now: datetime, start: time, end: time) -> datetime | None:
    """Next instant at which ``in_window`` flips, or None if it never does.

    The result carries the same tzinfo as ``now`` (which may be naive).
    """
    if start == end:
        return None
    target = end if in_window(now.time(), start, end) else start
    candidate = _combine(now.date(), target, now)
    if candidate <= now:
        candidate = _combine(now.date() + timedelta(days=1), target, now)
    return candidate


@dataclass(frozen=True)
class Override:
    """A manual override: force ``awake`` until ``expires_at`` (None = forever)."""

    awake: bool
    expires_at: datetime | None = None

    def is_active(self, now: datetime) -> bool:
        return self.expires_at is None or now < self.expires_at


@dataclass(frozen=True)
class State:
    awake: bool
    source: str  # "schedule" | "override" | "disabled" | "always"
    next_change: datetime | None


def desired_state(
    now: datetime,
    start: time,
    end: time,
    enabled: bool,
    override: Override | None = None,
) -> State:
    """Combine the schedule with an optional manual override."""
    if override is not None and override.is_active(now):
        return State(override.awake, "override", override.expires_at)
    if not enabled:
        return State(False, "disabled", None)
    if start == end:
        return State(True, "always", None)
    return State(in_window(now.time(), start, end), "schedule", next_transition(now, start, end))


def fmt_time(t: time) -> str:
    return t.strftime("%H:%M")


def fmt_window(start: time, end: time) -> str:
    return f"{fmt_time(start)}–{fmt_time(end)}"


def fmt_when(when: datetime, now: datetime) -> str:
    """'00:00' if today, '09:00 tomorrow' otherwise."""
    if when.date() == now.date():
        return fmt_time(when.time())
    if when.date() == now.date() + timedelta(days=1):
        return f"{fmt_time(when.time())} tomorrow"
    return when.strftime("%a %H:%M")
