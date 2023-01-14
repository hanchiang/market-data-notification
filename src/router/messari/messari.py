from fastapi import APIRouter
from src.dependencies import Dependencies

router = APIRouter(prefix="/messari")

@router.get("/asset-metrics")
async def get_values(symbol='BTC'):
  messari_service = Dependencies.get_messari_service()
  res = await messari_service.get_asset_metrics(symbol)
  return {"data": res}

