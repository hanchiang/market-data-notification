from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from market_data_library.types import cryptoquant_type
from src.service.crypto.cryptoquant import CryptoQuantService


class TestCryptoQuantService:
    @pytest.mark.asyncio
    async def test_get_price_ohlcv_requires_configured_library_service(self, monkeypatch) -> None:
        monkeypatch.setattr(
            'src.service.crypto.cryptoquant.get_crypto_api',
            lambda: SimpleNamespace(cryptoquant=SimpleNamespace(cryptoquant_service=None)),
        )
        service = CryptoQuantService()

        with pytest.raises(RuntimeError, match='CRYPTOQUANT_API_TOKEN is not configured'):
            await service.get_price_ohlcv()

    @pytest.mark.asyncio
    async def test_get_price_ohlcv(self, monkeypatch) -> None:
        library_service = Mock()
        library_service.get_price_ohlcv = AsyncMock(
            return_value=cryptoquant_type.PriceOhlcvResponse(
                status=cryptoquant_type.CryptoQuantStatus(code=200, message='success'),
                result=cryptoquant_type.PriceOhlcvResult(
                    window='day',
                    data=[
                        cryptoquant_type.PriceOhlcvPoint(
                            date='2026-03-27',
                            open=68803.2773797,
                            high=69136.74115283,
                            low=65494.92803148,
                            close=66384.33024908,
                            volume=51232.83737615211,
                        )
                    ],
                ),
            )
        )
        monkeypatch.setattr(
            'src.service.crypto.cryptoquant.get_crypto_api',
            lambda: SimpleNamespace(
                cryptoquant=SimpleNamespace(cryptoquant_service=library_service)
            ),
        )
        service = CryptoQuantService()

        res = await service.get_price_ohlcv(symbol='BTC', window='day', limit=2)

        assert res.status.code == 200
        assert res.result.window == 'day'
        assert res.result.data[0].close == 66384.33024908
