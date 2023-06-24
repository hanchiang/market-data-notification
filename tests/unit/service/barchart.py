from unittest.mock import AsyncMock

import pytest

from src.service.barchart import BarchartService
from src.third_party_service.barchart import ThirdPartyBarchartService


class TestBarchartService:
    @pytest.mark.asyncio
    async def test_get_stock_price(self):
        service = BarchartService(third_party_service=ThirdPartyBarchartService())
        service.third_party_service.get_stock_price = AsyncMock(return_value={'data': '''SPY,2023-06-09,429.96,431.99,428.87,429.9,85647200
SPY,2023-06-08,426.62,429.6,425.82,429.13,61952800
'''})
        res = await service.get_stock_price(symbol='SPY')

        assert res == {
            'data': [
                {'symbol': 'SPY', 'date': '2023-06-09', 'open': 429.96, 'high': 431.99, 'low': 428.87, 'close': 429.9, 'volume': 85647200.0},
                {'symbol': 'SPY', 'date': '2023-06-08', 'open': 426.62, 'high': 429.6, 'low': 425.82, 'close': 429.13, 'volume': 61952800.0}
            ]
        }
