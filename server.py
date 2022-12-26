from fastapi import FastAPI, Response, status
import uvicorn
from http_client import HttpClient, HttpClientContextManager
from datetime import datetime

app = FastAPI()


def start_server():
  print('starting server...')
  uvicorn.run(app, host="0.0.0.0", port=8080)


@app.get("/healthz")
async def heath_check():
  return {"data": "Vix central service is running!"}


@app.get("/current")
async def get_current():
  with HttpClientContextManager() as http_client:
    res = await http_client.get(url='/ajax_update')
    if res.status != 200:
      print(res.text)
      res.raise_for_status()
    res_json = await res.json()
    return {"data": res_json}


@app.get("/historical")
# yyyy-mm-dd, e.g. 2022-12-30
async def get_historical(date: str, response: Response):
  if date is None or len(date) != 10:
    response.status_code = status.HTTP_400_BAD_REQUEST
    return {"error": "invalid date. Date should be in the format <yyyy-mm-dd>"}
  with HttpClientContextManager() as http_client:
    res = await http_client.get(url='/ajax_historical', params={"n1": date})
    if res.status != 200:
      print(res.text)
      res.raise_for_status()

    res_json = await res.json()
    if res_json == "error":
      response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
      return {"error": f"no data found for {date}"}
    return {"data": res_json}
