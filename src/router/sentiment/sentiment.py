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

