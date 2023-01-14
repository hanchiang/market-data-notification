from fastapi import APIRouter
from src.dependencies import Dependencies

router = APIRouter(prefix="/thirdparty/messari")

@router.get("/asset-metrics")
async def get_current(symbol='BTC'):
  thirdparty_service = Dependencies.get_thirdparty_messari_service()
  res = await thirdparty_service.get_metrics(symbol)
  return {"data": res}
