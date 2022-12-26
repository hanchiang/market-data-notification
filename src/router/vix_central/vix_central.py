from fastapi import APIRouter
from src.dependencies import Dependencies

router = APIRouter(prefix="/vixcentral")

@router.get("/recent-values")
async def get_values():
  vix_central_service = Dependencies.get_vix_central_service()
  res = await vix_central_service.get_recent_values()
  return {"data": res}

