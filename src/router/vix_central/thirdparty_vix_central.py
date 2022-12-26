from fastapi import APIRouter, Response, status
from src.dependencies import Dependencies

router = APIRouter(prefix="/thirdparty/vixcentral")

@router.get("/current")
async def get_current():
  thirdparty_vix_central_service = Dependencies.get_thirdparty_vix_central_service()
  res = await thirdparty_vix_central_service.get_current()
  return {"data": res}


@router.get("/historical")
# yyyy-mm-dd, e.g. 2022-12-30
async def get_historical(date: str, response: Response):
  if date is None or len(date) != 10:
    response.status_code = status.HTTP_400_BAD_REQUEST
    return {"error": "invalid date. Date should be in the format <yyyy-mm-dd>"}
  thirdparty_vix_central_service = Dependencies.get_thirdparty_vix_central_service()

  try:
    res = await thirdparty_vix_central_service.get_historical(date=date)
    return {"data": res}
  except Exception as e:
    print(e)
    return {"error": str(e)}
