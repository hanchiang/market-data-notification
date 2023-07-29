from fastapi import APIRouter
from src.dependencies import Dependencies

router = APIRouter(prefix="/sentiment")

@router.get("/crypto-fear-greed")
async def get_crypto_fear_greed(from_source=False, days=365):
  service = Dependencies.get_sentiment_service()
  res = await service.get_crypto_fear_greed_index(from_source=from_source, days=days)
  return {"data": res}

