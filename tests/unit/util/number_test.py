import decimal

import pytest

from src.util.number import friendly_number, count_decimal_place_and_decimal_zeros


class TestNumberUtil:
    @pytest.mark.parametrize('num, decimal_places, expected', [
        (0.1, 0, '0.1'),
        (0.123456, 0, '0.12346'),
        (0.01, 0, '0.01'),
        (0.001, 0, '0.001'),
        (0.0001, 0, '0.0001'),
        (0.00001, 0, '0.00001'),
        (0.0000123456, 0, '0.000012346'),
        (1.123, 2, '1.123'),
        (1.00123, 3, '1.00123'),
        (1000.00123, 3, '1 K'),
        (1000, 3, '1 K'),
    ])
    def test_friendly_number(self, num, decimal_places, expected):
        res = friendly_number(num=num, decimal_places=decimal_places)
        assert res == expected

    @pytest.mark.parametrize('num, expected', [
        (decimal.Decimal('0.1'), (1, 0)),
        (decimal.Decimal('0.123456'), (6, 0)),
        (decimal.Decimal('0.01'), (2, 1)),
        (decimal.Decimal('0.001'), (3, 2)),
        (decimal.Decimal('0.0001'), (4, 3)),
        (decimal.Decimal('0.00001'), (5, 4)),
        (decimal.Decimal('0.0000123456'), (10, 4)),
    ])
    def test_count_decimal_place_and_decimal_zeros(self, num, expected):
        count_decimal_place_and_decimal_zeros(num) == expected