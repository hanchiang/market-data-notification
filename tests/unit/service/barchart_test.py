from unittest.mock import AsyncMock, Mock

import pytest

from src.service.barchart import BarchartService
from market_data_library.types import barchart_type


class TestBarchartService:
    @pytest.mark.asyncio
    async def test_get_stock_price(self):
        third_party_service = Mock()
        third_party_service.get_stock_price = AsyncMock(
            return_value=[
                barchart_type.StockPrice(symbol='SPY', date='2023-06-09', open_price=429.96, high_price=431.99, low_price=428.87, close_price=429.9, volume=85647200.0),
                barchart_type.StockPrice(symbol='SPY', date='2023-06-08', open_price=426.62, high_price=429.6, low_price=425.82, close_price=429.13, volume=61952800.0),
            ]
        )
        service = BarchartService(third_party_service=third_party_service)
        res = await service.get_stock_price(symbol='SPY')

        assert res == [
            barchart_type.StockPrice(symbol='SPY', date='2023-06-09', open_price=429.96, high_price=431.99, low_price=428.87, close_price=429.9, volume=85647200.0),
            barchart_type.StockPrice(symbol='SPY', date='2023-06-08', open_price=426.62, high_price=429.6, low_price=425.82, close_price=429.13, volume=61952800.0),
        ]
