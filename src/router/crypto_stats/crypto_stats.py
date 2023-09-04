from fastapi import APIRouter
from src.dependencies import Dependencies

router = APIRouter(prefix="/crypto_stats")

@router.get("/topsectors")
async def get_top_sectors_24h(sort_by='avg_price_change', sort_direction='desc', limit = 10):
  service = Dependencies.get_crypto_stats_service()
  res = await service.get_sectors_24h_change(sort_by=sort_by, sort_direction=sort_direction, limit=limit)
  return {"data": res}
