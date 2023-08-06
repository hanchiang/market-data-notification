from fastapi import APIRouter
from src.dependencies import Dependencies

router = APIRouter(prefix="/sentiment")

@router.get("/crypto-fear-greed")
async def get_crypto_fear_greed(from_source=False, days=365):
  service = Dependencies.get_crypto_sentiment_service()
  if from_source:
    res = await service.get_crypto_fear_greed_index_from_source(days=days)
  else:
    res = await service.get_crypto_fear_greed_index(days=days)
  return {"data": res}


@router.get("/stocks-fear-greed")
async def get_crypto_fear_greed(from_source=False):
  service = Dependencies.get_stocks_sentiment_service()
  if from_source:
    res = await service.get_stocks_fear_greed_index_from_source()
  else:
    res = await service.get_stocks_fear_greed_index()
  return {"data": res}
