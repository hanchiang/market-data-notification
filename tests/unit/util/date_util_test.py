import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.util import date_util
from src.util.date_util import ny_tz


class TestDateUtil:
    @staticmethod
    def build_utc_datetime(
        year: int,
        month: int,
        day: int,
        hour: int = 0,
        minute: int = 0,
        second: int = 0,
        microsecond: int = 0,
    ) -> datetime.datetime:
        return datetime.datetime(
            year,
            month,
            day,
            hour,
            minute,
            second,
            microsecond,
            tzinfo=datetime.timezone.utc,
        )

    @pytest.fixture
    def utc_now(self) -> datetime.datetime:
        return self.build_utc_datetime(2023, 5, 29, 21, 0, 0, 0)

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
                build_utc_datetime.__func__(2023, 5, 29, 21, 0, 0, 0),  # Memorial Day
                build_utc_datetime.__func__(2023, 5, 26, 21, 0, 0, 0),
        ),
        (
                build_utc_datetime.__func__(2023, 5, 28, 21, 0, 0, 0),  # sunday
                build_utc_datetime.__func__(2023, 5, 26, 21, 0, 0, 0),
        ),
        (
                build_utc_datetime.__func__(2023, 5, 27, 21, 0, 0, 0),  # saturday
                build_utc_datetime.__func__(2023, 5, 26, 21, 0, 0, 0),
        ),
        (
                build_utc_datetime.__func__(2023, 5, 26, 21, 0, 0, 0),  # friday
                build_utc_datetime.__func__(2023, 5, 26, 21, 0, 0, 0),
        ),
        (
                build_utc_datetime.__func__(2025, 1, 20, 21, 0, 0, 0),  # MLK Day
                build_utc_datetime.__func__(2025, 1, 17, 21, 0, 0, 0),
        )
    ])
    def test_get_most_recent_non_weekend_or_today(self, input, expected):
        res = date_util.get_most_recent_non_weekend_or_today(date=input)
        assert res == expected

    def test_get_most_recent_non_weekend_or_today_uses_shared_library_helper(self, monkeypatch):
        shared_helper = Mock(return_value=datetime.date(2025, 1, 17))
        monkeypatch.setattr(
            date_util,
            'market_data_library_date_util',
            SimpleNamespace(get_trading_day_at_or_before=shared_helper),
        )
        input_date = self.build_utc_datetime(2025, 1, 20, 21, 0, 0, 0)

        res = date_util.get_most_recent_non_weekend_or_today(date=input_date)

        shared_helper.assert_called_once_with(input_date)
        assert res == self.build_utc_datetime(2025, 1, 17, 21, 0, 0, 0)

    def test_get_most_recent_non_weekend_or_today_falls_back_when_library_helper_missing(self, monkeypatch):
        monkeypatch.setattr(
            date_util,
            'market_data_library_date_util',
            SimpleNamespace(),
        )
        input_date = self.build_utc_datetime(2025, 1, 20, 21, 0, 0, 0)

        res = date_util.get_most_recent_non_weekend_or_today(date=input_date)

        assert res == self.build_utc_datetime(2025, 1, 17, 21, 0, 0, 0)

    @pytest.mark.parametrize('input, expected', [
        (1691107200000, build_utc_datetime.__func__(2023, 8, 4, 0, 0, 0, 0))
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
        ('4 Aug, 2023', '%d %b, %Y', build_utc_datetime.__func__(2023, 8, 4, 0, 0, 0, 0),
         )
    ])
    def test_parse(self, date, format_string, expected):
        res = date_util.parse(dt=date, format=format_string)
        assert res == expected
