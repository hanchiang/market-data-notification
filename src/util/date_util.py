import datetime
import pytz

ny_tz = pytz.timezone('America/New_York')

def get_current_datetime():
    now = datetime.datetime.now()
    return now.astimezone(tz=ny_tz)

def get_current_date():
    now = datetime.datetime.now().astimezone(tz=ny_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return now

def get_current_date_preserve_time():
    now = datetime.datetime.now().astimezone(tz=ny_tz)
    return now

def get_datetime_from_timestamp(timestamp: int, use_ny_tz=True) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(timestamp, tz=ny_tz if use_ny_tz else None)

def format(dt: datetime.datetime, format = '%Y-%m-%d') -> str:
    return dt.strftime(format)

def parse_timestamp(unix_ts: float, tz: datetime.timezone = datetime.timezone.utc) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(unix_ts, tz=tz)

def parse(dt: str, format: str, tz: datetime.timezone=datetime.timezone.utc) -> datetime.datetime:
    return datetime.datetime.strptime(dt, format).replace(tzinfo=tz)

# return the most recent non-weekend or today
# TODO: exclude public holidays
def get_most_recent_non_weekend_or_today(date: datetime.datetime) -> datetime.datetime:
    # Monday == 0, Sunday == 6
    day_of_week = date.weekday()
    days_to_subtract = 0
    if day_of_week == 5:
        days_to_subtract = 1
    elif day_of_week == 6:
        days_to_subtract = 2

    return date - datetime.timedelta(days=days_to_subtract)
