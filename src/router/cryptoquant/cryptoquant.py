from fastapi import APIRouter, HTTPException
from market_data_library.util.exception import CryptoQuantApiError

from src.dependencies import Dependencies

router = APIRouter(prefix="/cryptoquant")


@router.get("/price-ohlcv")
async def get_price_ohlcv(symbol: str = 'BTC', window: str = 'day', limit: int | None = None):
    service = Dependencies.get_cryptoquant_api_service()
    if service is None:
        raise HTTPException(status_code=503, detail='CryptoQuant service is unavailable')
    try:
        res = await service.get_price_ohlcv(symbol=symbol, window=window, limit=limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except CryptoQuantApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"data": res}
