from market_data_library.core.crypto.coinalyze.coinalyze import (
    CoinalyzeService as MarketDataCoinalyzeService,
)
from market_data_library.core.crypto.coinalyze.type import (
    CoinalyzeFutureMarket,
    CoinalyzeHistorySeries,
)

from src.data_source.market_data_library import get_crypto_api


class CoinalyzeService:
    def __init__(
        self,
        coinalyze_service: MarketDataCoinalyzeService | None = None,
        *,
        use_configured_service: bool = True,
    ) -> None:
        if coinalyze_service is not None:
            self.coinalyze_service = coinalyze_service
        elif use_configured_service:
            self.coinalyze_service = get_crypto_api().coinalyze.coinalyze_service
        else:
            self.coinalyze_service = None

    def is_configured(self) -> bool:
        return self.coinalyze_service is not None

    async def get_future_markets(self) -> list[CoinalyzeFutureMarket]:
        if self.coinalyze_service is None:
            raise RuntimeError('COINALYZE_API_KEY is not configured')
        return await self.coinalyze_service.get_future_markets()

    async def get_open_interest_history(
        self,
        symbols: list[str],
        interval: str,
        from_timestamp_seconds: int,
        to_timestamp_seconds: int,
        convert_to_usd: bool = True,
    ) -> list[CoinalyzeHistorySeries]:
        if self.coinalyze_service is None:
            raise RuntimeError('COINALYZE_API_KEY is not configured')
        return await self.coinalyze_service.get_open_interest_history(
            symbols=symbols,
            interval=interval,
            from_timestamp_seconds=from_timestamp_seconds,
            to_timestamp_seconds=to_timestamp_seconds,
            convert_to_usd=convert_to_usd,
        )

    async def get_funding_rate_history(
        self,
        symbols: list[str],
        interval: str,
        from_timestamp_seconds: int,
        to_timestamp_seconds: int,
    ) -> list[CoinalyzeHistorySeries]:
        if self.coinalyze_service is None:
            raise RuntimeError('COINALYZE_API_KEY is not configured')
        return await self.coinalyze_service.get_funding_rate_history(
            symbols=symbols,
            interval=interval,
            from_timestamp_seconds=from_timestamp_seconds,
            to_timestamp_seconds=to_timestamp_seconds,
        )
