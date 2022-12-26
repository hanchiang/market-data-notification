from fastapi import FastAPI, Response, status
import uvicorn
from datetime import datetime
from http_client import HttpClient
from dependencies import Dependencies
from router import vix_central

app = FastAPI()
app.include_router(vix_central.router)


def start_server():
  print('starting server...')
  uvicorn.run(app, host="0.0.0.0", port=8080)


@app.on_event("startup")
async def startup_event():
  Dependencies.build()


@app.get("/healthz")
async def heath_check():
  return {"data": "Vix central service is running!"}
