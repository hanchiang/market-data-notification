from unittest.mock import AsyncMock, Mock

import pytest
from market_data_library.core.crypto.coinalyze.type import (
    CoinalyzeFutureMarket,
    CoinalyzeHistoryPoint,
    CoinalyzeHistorySeries,
)

from src.service.crypto.coinalyze import CoinalyzeService


class TestCoinalyzeService:
    def test_is_configured_returns_false_when_api_key_absent(self) -> None:
        service = CoinalyzeService(use_configured_service=False)

        assert service.is_configured() is False

    @pytest.mark.asyncio
    async def test_get_future_markets_requires_configured_library_service(
        self,
    ) -> None:
        service = CoinalyzeService(use_configured_service=False)

        with pytest.raises(RuntimeError, match='COINALYZE_API_KEY is not configured'):
            await service.get_future_markets()

    @pytest.mark.asyncio
    async def test_delegates_to_library_service(self) -> None:
        library_service = Mock()
        library_service.get_future_markets = AsyncMock(
            return_value=[
                CoinalyzeFutureMarket(
                    symbol='BTCUSDT_PERP.A',
                    exchange='BINANCE',
                    base_asset='BTC',
                    quote_asset='USDT',
                    is_perpetual=True,
                )
            ]
        )
        library_service.get_open_interest_history = AsyncMock(
            return_value=[
                CoinalyzeHistorySeries(
                    symbol='BTCUSDT_PERP.A',
                    history=[CoinalyzeHistoryPoint(t=1711929600, c=100.0)],
                )
            ]
        )
        library_service.get_funding_rate_history = AsyncMock(
            return_value=[
                CoinalyzeHistorySeries(
                    symbol='BTCUSDT_PERP.A',
                    history=[CoinalyzeHistoryPoint(t=1711929600, c=0.01)],
                )
            ]
        )
        service = CoinalyzeService(coinalyze_service=library_service)

        markets = await service.get_future_markets()
        open_interest = await service.get_open_interest_history(
            symbols=['BTCUSDT_PERP.A'],
            interval='1hour',
            from_timestamp_seconds=1711929600,
            to_timestamp_seconds=1711933200,
            convert_to_usd=True,
        )
        funding = await service.get_funding_rate_history(
            symbols=['BTCUSDT_PERP.A'],
            interval='1hour',
            from_timestamp_seconds=1711929600,
            to_timestamp_seconds=1711933200,
        )

        assert service.is_configured() is True
        assert markets[0].symbol == 'BTCUSDT_PERP.A'
        assert open_interest[0].history[0].c == 100.0
        assert funding[0].history[0].c == 0.01
        library_service.get_open_interest_history.assert_awaited_once_with(
            symbols=['BTCUSDT_PERP.A'],
            interval='1hour',
            from_timestamp_seconds=1711929600,
            to_timestamp_seconds=1711933200,
            convert_to_usd=True,
        )
