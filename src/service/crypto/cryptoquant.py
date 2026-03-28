from market_data_library.types import cryptoquant_type
from market_data_library.util.exception import CryptoQuantApiError

from src.data_source.market_data_library import get_crypto_api


class CryptoQuantService:
    def __init__(self) -> None:
        self.cryptoquant_service = get_crypto_api().cryptoquant.cryptoquant_service

    async def get_price_ohlcv(
        self, symbol: str = 'BTC', window: str = 'day', limit: int | None = None
    ) -> cryptoquant_type.PriceOhlcvResponse:
        if self.cryptoquant_service is None:
            raise RuntimeError('CRYPTOQUANT_API_TOKEN is not configured')

        try:
            return await self.cryptoquant_service.get_price_ohlcv(
                symbol=symbol,
                window=window,
                limit=limit,
            )
        except CryptoQuantApiError:
            raise
