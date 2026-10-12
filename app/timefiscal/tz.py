import zoneinfo
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class TimezoneInstant(BaseModel):
    """
    Represents an absolute instant in time (stored in UTC)
    along with its original source timezone.
    """
    utc_instant: datetime
    source_timezone: str

    @property
    def local_datetime(self) -> datetime:
        """Project the UTC instant into the local source timezone."""
        tz = zoneinfo.ZoneInfo(self.source_timezone)
        return self.utc_instant.astimezone(tz)

    @property
    def utc_offset_minutes(self) -> int:
        """Offset in minutes from UTC at this exact instant in the source timezone."""
        tz = zoneinfo.ZoneInfo(self.source_timezone)
        offset = self.utc_instant.astimezone(tz).utcoffset()
        return int(offset.total_seconds() / 60) if offset else 0

    def to_dict(self) -> dict:
        return {
            "utc_instant": self.utc_instant.isoformat(),
            "source_timezone": self.source_timezone,
            "local_datetime": self.local_datetime.isoformat(),
            "utc_offset_minutes": self.utc_offset_minutes,
        }


def make_instant(
    dt: datetime,
    source_tz: str = "UTC",
    is_dst: Optional[bool] = None
) -> TimezoneInstant:
    """
    Construct a TimezoneInstant ensuring proper timezone awareness.
    If dt is naive, it is localized using source_tz.
    If dt is aware, it is converted to UTC and tagged with source_tz.
    """
    tz = zoneinfo.ZoneInfo(source_tz)

    if dt.tzinfo is None:
        # Naive datetime: localize to source_tz first, then convert to UTC
        local_aware = dt.replace(tzinfo=tz)
        utc_dt = local_aware.astimezone(timezone.utc)
    else:
        # Already aware: convert to UTC
        utc_dt = dt.astimezone(timezone.utc)

    return TimezoneInstant(utc_instant=utc_dt, source_timezone=source_tz)


def parse_instant_iso(iso_str: str, source_tz: Optional[str] = None) -> TimezoneInstant:
    """Parse an ISO 8601 string into TimezoneInstant."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        tz_name = source_tz or "UTC"
        return make_instant(dt, tz_name)

    # If timezone is present in ISO string and source_tz is provided, use source_tz
    return TimezoneInstant(
        utc_instant=dt.astimezone(timezone.utc),
        source_timezone=source_tz or "UTC"
    )
