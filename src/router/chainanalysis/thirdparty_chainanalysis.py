from fastapi import APIRouter
from src.dependencies import Dependencies

router = APIRouter(prefix="/thirdparty/chainanalysis")

@router.get("/trade-intensity")
async def get_current(symbol='BTC'):
  thirdparty_service = Dependencies.get_thirdparty_chainanalysis_service()
  res = await thirdparty_service.get_trade_intensity(symbol=symbol)
  return {"data": res}
