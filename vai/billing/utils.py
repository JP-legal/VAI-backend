import math

from datetime import datetime, timezone

def minutes_to_seconds(minutes: int) -> int:
    return int(minutes) * 60

def ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def seconds_to_billable_minutes(seconds: int) -> int:
    if seconds <= 0:
        return 0
    return ceil_div(seconds, 60)
