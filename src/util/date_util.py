import datetime
from typing import cast
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
from market_data_library.util import date_util as market_data_library_date_util

ny_tz = ZoneInfo("America/New_York")
XNYS_CALENDAR = xcals.get_calendar("XNYS")


def get_current_datetime():
    now = datetime.datetime.now()
    return now.astimezone(tz=ny_tz)


def get_current_date():
    now = datetime.datetime.now().astimezone(tz=ny_tz)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def get_current_date_preserve_time():
    now = datetime.datetime.now().astimezone(tz=ny_tz)
    return now


def get_datetime_from_timestamp(timestamp: int, use_ny_tz=True) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(timestamp, tz=ny_tz if use_ny_tz else None)


def format(dt: datetime.datetime, format="%Y-%m-%d") -> str:
    return dt.strftime(format)


def parse_timestamp(
    unix_ts: float,
    tz: datetime.timezone = datetime.timezone.utc,
) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(unix_ts, tz=tz)


def parse(
    dt: str,
    format: str,
    tz: datetime.timezone = datetime.timezone.utc,
) -> datetime.datetime:
    return datetime.datetime.strptime(dt, format).replace(tzinfo=tz)


def _fallback_get_trading_day_at_or_before(
    reference_date: datetime.date | datetime.datetime,
) -> datetime.date:
    normalized_date = (
        reference_date.date()
        if isinstance(reference_date, datetime.datetime)
        else reference_date
    )
    session = XNYS_CALENDAR.date_to_session(
        normalized_date.isoformat(),
        direction="previous",
    )
    return cast(datetime.date, session.date())


def _get_trading_day_at_or_before(
    reference_date: datetime.date | datetime.datetime,
) -> datetime.date:
    # Prefer the shared library helper so backend behavior tracks the published
    # library contract once that release is available. Keep the local fallback
    # for the current pinned git dependency until the library change is merged
    # and the backend dependency is advanced to include it.
    shared_helper = getattr(
        market_data_library_date_util,
        "get_trading_day_at_or_before",
        None,
    )
    if shared_helper is not None:
        return shared_helper(reference_date)

    # Keep the backend compatible with released library pins until they catch up.
    return _fallback_get_trading_day_at_or_before(reference_date)


def get_most_recent_non_weekend_or_today(date: datetime.datetime) -> datetime.datetime:
    target_date = _get_trading_day_at_or_before(date)
    return date.replace(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
    )
