"""Time formatting shared by every subdomain. All times are naive local wall-clock datetimes."""

from __future__ import annotations

from datetime import datetime


def iso(moment: datetime) -> str:
    """Seconds-precision ISO text, the format stored everywhere."""
    return moment.isoformat(timespec="seconds")


def epoch_ms(moment: datetime) -> int:
    """Milliseconds since the epoch, the format the QML screens use."""
    return int(moment.timestamp() * 1000)
