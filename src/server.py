import json
import logging
import time

from fastapi import FastAPI, Request
import uvicorn
import os

from starlette.responses import JSONResponse

from src.dependencies import Dependencies
from src.notification_destination.telegram_notification import init_telegram_bots
from src.router.barchart import thirdparty_barchart
from src.router.chainanalysis import thirdparty_chainanalysis, chainanalysis
from src.router.sentiment import sentiment
from src.router.vix_central import thirdparty_vix_central, vix_central
from src.router.messari import thirdparty_messari, messari
from src.router.tradingview import tradingview
from src.router.crypto_stats import crypto_stats
import src.config.config as config
from src.db.redis import Redis

app = FastAPI()
app.include_router(tradingview.router)

# stocks
app.include_router(thirdparty_vix_central.router)
app.include_router(vix_central.router)
app.include_router(thirdparty_messari.router)
app.include_router(thirdparty_barchart.router)
# crypto
app.include_router(messari.router)
app.include_router(thirdparty_chainanalysis.router)
app.include_router(chainanalysis.router)
app.include_router(sentiment.router)
app.include_router(crypto_stats.router)

env = os.getenv('ENV')

logger = logging.getLogger('Server')
def start_server():
    logger.info('starting server...')
    reload = False
    if env == 'dev':
        reload = True

    uvicorn.run("server:app", app_dir="src", reload_dirs=["src"], host="0.0.0.0", port=8080, reload=reload)

@app.on_event("startup")
async def startup_event():
    await Dependencies.build()
    await Redis.start_redis()
    init_telegram_bots()

@app.on_event("shutdown")
async def shutdown_event():
    await Dependencies.cleanup()
    await Redis.stop_redis()

@app.middleware("http")
async def log_request_and_time_taken(request: Request, call_next):
    start_time = time.time()
    logger.info(f"{request.method} {request.url}, headers: {request.headers} client: {request.client}")
    response = await call_next(request)
    time_elapsed = time.time() - start_time
    response.headers["X-Process-Time"] = str(time_elapsed)
    logger.info(f"Response: {response.headers}")
    return response

@app.middleware("http")
async def auth_check(request: Request, call_next):
    if config.get_env() != 'prod' or 'localhost' in request.url.hostname:
        logger.info('env is not prod or hostname is localhost. Skipping auth')
        return await call_next(request)

    excluded_endpoints = config.get_auth_exclude_endpoints()
    if excluded_endpoints is not None:
        for excluded_endpoint in excluded_endpoints:
            if excluded_endpoint in request.url.path:
                return await call_next(request)

    auth_token = request.headers.get('X-Api-Auth')
    if not auth_token or auth_token != config.get_api_auth_token():
        logger.error('X-Api-Auth token is missing')
        return JSONResponse(status_code=500, content={'data': 'You shall not pass'})
    return await call_next(request)

@app.get("/healthz")
async def heath_check():
    return {"data": "Market data notification is running!"}
