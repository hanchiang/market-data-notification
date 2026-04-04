from fastapi import APIRouter
from src.dependencies import Dependencies

router = APIRouter(prefix="/vixcentral")

@router.get("/recent-values")
async def get_values():
  # Lightweight inspection route: it returns the service object directly and is
  # useful for debugging, not as a stable public schema contract.
  vix_central_service = Dependencies.get_vix_central_service()
  res = await vix_central_service.get_recent_values()
  return {"data": res}
