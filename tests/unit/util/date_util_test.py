import datetime
from unittest.mock import patch

import pytest
import pytz

from src.util import date_util
from src.util.date_util import ny_tz


class TestDateUtil:

    @pytest.fixture
    def utc_now(self) -> datetime.datetime:
        return datetime.datetime.utcnow().replace(year=2023, month=5, day=29, hour=21, minute=0, second=0,
                                                  microsecond=0)

    @patch('src.util.date_util.datetime')
    def test_get_current_datetime(self, mock_datetime, utc_now):
        mock_datetime.datetime.now.return_value = utc_now
        res = date_util.get_current_datetime()
        assert res == utc_now.astimezone(tz=ny_tz)

    @patch('src.util.date_util.datetime')
    def test_get_current_date(self, mock_datetime, utc_now):
        mock_datetime.datetime.now.return_value = utc_now
        res = date_util.get_current_date()
        assert res == utc_now.astimezone(tz=ny_tz).replace(hour=0, minute=0, second=0, microsecond=0)

    def test_get_datetime_from_timestamp(self, utc_now):
        res = date_util.get_datetime_from_timestamp(timestamp=utc_now.timestamp())
        assert res == utc_now.astimezone(tz=ny_tz)

    def test_format(self, utc_now):
        res = date_util.format(utc_now)
        assert res == '2023-05-29'

    @pytest.mark.parametrize('input, expected', [
        (
                datetime.datetime.utcnow().replace(year=2023, month=5, day=29, hour=21, minute=0, second=0,
                                                   microsecond=0),  # monday
                datetime.datetime.utcnow().replace(year=2023, month=5, day=29, hour=21, minute=0, second=0,
                                                   microsecond=0),
        ),
        (
                datetime.datetime.utcnow().replace(year=2023, month=5, day=28, hour=21, minute=0, second=0,
                                                   microsecond=0),  # sunday
                datetime.datetime.utcnow().replace(year=2023, month=5, day=26, hour=21, minute=0, second=0,
                                                   microsecond=0),
        ),
        (
                datetime.datetime.utcnow().replace(year=2023, month=5, day=27, hour=21, minute=0, second=0,
                                                   microsecond=0),  # saturday
                datetime.datetime.utcnow().replace(year=2023, month=5, day=26, hour=21, minute=0, second=0,
                                                   microsecond=0),
        ),
        (
                datetime.datetime.utcnow().replace(year=2023, month=5, day=26, hour=21, minute=0, second=0,
                                                   microsecond=0),  # friday
                datetime.datetime.utcnow().replace(year=2023, month=5, day=26, hour=21, minute=0, second=0,
                                                   microsecond=0),
        )
    ])
    def test_get_most_recent_non_weekend_or_today(self, input, expected):
        res = date_util.get_most_recent_non_weekend_or_today(date=input)
        assert res == expected

    @pytest.mark.parametrize('input, expected', [
        (1691107200000, datetime.datetime.utcnow().replace(year=2023, month=8, day=4, hour=0, minute=0, second=0, tzinfo=datetime.timezone.utc))
    ])
    def test_parse_utc_timestamp(self, input, expected):
        res = date_util.parse_timestamp(input / 1000)
        assert res.year == expected.year
        assert res.month == expected.month
        assert res.day == expected.day
        assert res.hour == expected.hour
        assert res.minute == expected.minute
        assert res.second == expected.second
        assert res.tzinfo == expected.tzinfo

    @pytest.mark.parametrize('date, format_string, expected', [
        ('4 Aug, 2023', '%d %b, %Y', datetime.datetime.utcnow().replace(year=2023, month=8, day=4, hour=0, minute=0, second=0,
                                                   microsecond=0, tzinfo=datetime.timezone.utc),
         )
    ])
    def test_parse(self, date, format_string, expected):
        res = date_util.parse(dt=date, format=format_string)
        assert res == expected