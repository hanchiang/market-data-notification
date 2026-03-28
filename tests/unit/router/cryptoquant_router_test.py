from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from market_data_library.util.exception import CryptoQuantApiError

from src.router.cryptoquant.cryptoquant import get_price_ohlcv


class TestCryptoQuantRouter:
    @pytest.mark.asyncio
    async def test_get_price_ohlcv_returns_service_data(self, monkeypatch) -> None:
        service = Mock()
        service.get_price_ohlcv = AsyncMock(return_value={"status": {"code": 200}})
        monkeypatch.setattr(
            'src.router.cryptoquant.cryptoquant.Dependencies.get_cryptoquant_api_service',
            lambda: service,
        )

        res = await get_price_ohlcv(symbol='BTC', window='day', limit=2)

        assert res == {"data": {"status": {"code": 200}}}
        service.get_price_ohlcv.assert_awaited_once_with(
            symbol='BTC',
            window='day',
            limit=2,
        )

    @pytest.mark.asyncio
    async def test_get_price_ohlcv_raises_503_without_service(self, monkeypatch) -> None:
        monkeypatch.setattr(
            'src.router.cryptoquant.cryptoquant.Dependencies.get_cryptoquant_api_service',
            lambda: None,
        )

        with pytest.raises(HTTPException) as exc:
            await get_price_ohlcv()

        assert exc.value.status_code == 503
        assert exc.value.detail == 'CryptoQuant service is unavailable'

    @pytest.mark.asyncio
    async def test_get_price_ohlcv_maps_runtime_error_to_503(self, monkeypatch) -> None:
        service = Mock()
        service.get_price_ohlcv = AsyncMock(side_effect=RuntimeError('missing token'))
        monkeypatch.setattr(
            'src.router.cryptoquant.cryptoquant.Dependencies.get_cryptoquant_api_service',
            lambda: service,
        )

        with pytest.raises(HTTPException) as exc:
            await get_price_ohlcv()

        assert exc.value.status_code == 503
        assert exc.value.detail == 'missing token'

    @pytest.mark.asyncio
    async def test_get_price_ohlcv_maps_library_error_to_502(self, monkeypatch) -> None:
        service = Mock()
        service.get_price_ohlcv = AsyncMock(
            side_effect=CryptoQuantApiError(
                message='bad upstream response',
                endpoint='btc/market-data/price-ohlcv',
            )
        )
        monkeypatch.setattr(
            'src.router.cryptoquant.cryptoquant.Dependencies.get_cryptoquant_api_service',
            lambda: service,
        )

        with pytest.raises(HTTPException) as exc:
            await get_price_ohlcv()

        assert exc.value.status_code == 502
        assert 'bad upstream response' in exc.value.detail
