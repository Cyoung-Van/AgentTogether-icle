"""Local calendar boundaries for a local-first ICLE installation.

Persisted timestamps stay timezone-aware UTC. Human calendar periods (today,
week, month, daily limits) use the computer's current system timezone. A
legacy timestamp without an offset is interpreted as UTC, never as an
implicit local time.
"""

from __future__ import annotations

from datetime import date, datetime, tzinfo, timezone
from typing import Any


class LocalTimeError(ValueError):
    pass


def parse_timestamp(value: Any) -> datetime:
    """Parse an ISO timestamp and normalize legacy naive values to UTC."""
    if not isinstance(value, str) or not value.strip():
        raise LocalTimeError("timestamp must be a non-empty ISO string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LocalTimeError(f"invalid ISO timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def as_local_datetime(
    value: str | datetime | None = None,
    *,
    local_timezone: tzinfo | None = None,
) -> datetime:
    """Convert a timestamp to the requested or current system local timezone."""
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    else:
        parsed = parse_timestamp(value)
    # astimezone() without an argument deliberately asks the operating system
    # for its local timezone, including the offset applicable to this date.
    return parsed.astimezone(local_timezone) if local_timezone is not None else parsed.astimezone()


def local_date(
    value: str | datetime | None = None,
    *,
    local_timezone: tzinfo | None = None,
) -> date:
    return as_local_datetime(value, local_timezone=local_timezone).date()


def local_day_key(
    value: str | datetime | None = None,
    *,
    local_timezone: tzinfo | None = None,
) -> str:
    return local_date(value, local_timezone=local_timezone).isoformat()


def local_month_key(
    value: str | datetime | None = None,
    *,
    local_timezone: tzinfo | None = None,
) -> str:
    return as_local_datetime(value, local_timezone=local_timezone).strftime("%Y-%m")
