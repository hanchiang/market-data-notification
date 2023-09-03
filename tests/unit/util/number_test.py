import decimal

import pytest

from src.util.number import friendly_number, count_leading_decimal_zeros, format_precision_without_trailing_zero


class TestNumberUtil:
    @pytest.mark.parametrize('num, decimal_places, expected', [
        (0.1, 0, '0.1'),
        (0.123456, 0, '0.1235'),
        (0.01, 0, '0.01'),
        (0.001, 0, '0.001'),
        (0.0001, 0, '0.0001'),
        (0.00001, 0, '0.00001'),
        (0.0000123456, 0, '0.00001235'),
        (1.123, 2, '1.123'),
        (1.00123, 3, '1.00123'),
        (1000.00123, 3, '1 K'),
        (1000, 3, '1 K'),
    ])
    def test_friendly_number(self, num, decimal_places, expected):
        res = friendly_number(num=num, decimal_places=decimal_places)
        assert res == expected

    @pytest.mark.parametrize('num, expected', [
        (decimal.Decimal('0'), 0),
        (decimal.Decimal('1.0'), 0),
        (decimal.Decimal('0.1'), 0),
        (decimal.Decimal('0.123456'), 0),
        (decimal.Decimal('0.01'), 1),
        (decimal.Decimal('0.001'), 2),
        (decimal.Decimal('0.0001'), 3),
        (decimal.Decimal('0.00001'), 4),
        (decimal.Decimal('0.0000123456'), 4),
    ])
    def test_count_decimal_place_and_decimal_zeros(self, num, expected):
        count_leading_decimal_zeros(num) == expected

    @pytest.mark.parametrize('num, decimal_places, expected', [
        (0.00100, 5, '0.001'),
        (1.1000, 4, '1.1'),
        (1.0000, 4, '1.0000'),
        (1.01000, 5, '1.01'),
    ])
    def test_format_precision_without_trailing_zero(self, num, decimal_places, expected):
        format_precision_without_trailing_zero(num, decimal_places) == expected