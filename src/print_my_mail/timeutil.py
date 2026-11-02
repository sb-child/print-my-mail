"""统一使用 UTC+8 时间。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC8 = timezone(timedelta(hours=8))


def now() -> datetime:
    """当前 UTC+8 时间。"""
    return datetime.now(UTC8)


def to_utc8(value: datetime) -> datetime:
    """把任意 datetime 转成 UTC+8；无时区的时间按 UTC+8 处理。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC8)
    return value.astimezone(UTC8)


def iso_to_utc8(value: str) -> str:
    """把 ISO 8601 时间字符串转成 UTC+8；无法解析时原样返回。"""
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return to_utc8(parsed).isoformat(timespec="seconds")

