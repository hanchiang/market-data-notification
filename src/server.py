from fastapi import FastAPI
import uvicorn
import os
from src.dependencies import Dependencies
from src.router import vix_central

app = FastAPI()
app.include_router(vix_central.router)


env = os.getenv('ENV')

def start_server():
  print('starting server...')
  reload = False
  if env == 'dev':
    reload = True

  uvicorn.run("server:app", app_dir="src", reload_dirs=["src"], host="0.0.0.0", port=8080, reload=reload)

@app.on_event("startup")
async def startup_event():
  Dependencies.build()
@app.on_event("shutdown")
async def shutdown_event():
    await Dependencies.cleanup()

@app.get("/healthz")
async def heath_check():
  return {"data": "Vix central service is running!"}