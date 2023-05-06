from fastapi import APIRouter
from src.dependencies import Dependencies

router = APIRouter(prefix="/chainanalysis")

@router.get("/fees")
async def get_fees(symbol='BTC'):
  service = Dependencies.get_chainanalysis_service()
  res = await service.get_fees(symbol=symbol)
  return {"data": res}
