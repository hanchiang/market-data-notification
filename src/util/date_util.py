import datetime
import pytz

ny_tz = pytz.timezone('America/New_York')

def get_current_datetime():
    now = datetime.datetime.now()
    return now.astimezone(tz=ny_tz)

# return the most recent non-weekend or today
# TODO: exclude public holidays
def get_most_recent_non_weekend_or_today(date: datetime.datetime):
    # Monday == 0, Sunday == 6
    day_of_week = date.weekday()
    days_to_subtract = 0
    if day_of_week == 5:
        days_to_subtract = 1
    elif day_of_week == 6:
        days_to_subtract = 2

    return date - datetime.timedelta(days=days_to_subtract)
