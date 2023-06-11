from fastapi import APIRouter
from src.dependencies import Dependencies

router = APIRouter(prefix="/thirdparty/barchart")

# TODO: remove
@router.get("/stock-price")
async def get_current(symbol='SPY'):
  thirdparty_service = Dependencies.get_thirdparty_barchart_service()
  res = await thirdparty_service.get_stock_price(symbol=symbol, num_days=30)
  return {"data": res}
