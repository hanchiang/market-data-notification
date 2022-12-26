from fastapi import FastAPI
import uvicorn
import os
from src.dependencies import Dependencies
from src.router.vix_central import thirdparty_vix_central, vix_central
import src.notification_destination.telegram_notification as telegram_notification

app = FastAPI()
app.include_router(thirdparty_vix_central.router)
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
  return {"data": "Market data notification is running!"}

# @app.post("/telegram")
# async def heath_check():
#   res = await telegram_notification.send_message("hello world")
#   return {"data": f"Sent to {res.chat.title} {res.chat.type} at {res.date}"}