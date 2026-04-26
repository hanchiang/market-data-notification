import logging
import time

from fastapi import FastAPI, Request
import uvicorn
import os

from starlette.responses import JSONResponse

from src.data_source.market_data_library import init_market_data_api
from src.dependencies import Dependencies
from src.notification_destination.telegram_notification import init_telegram_bots
from src.router.barchart import thirdparty_barchart
from src.router.cryptoquant import cryptoquant
from src.router.sentiment import sentiment
from src.router.vix_central import thirdparty_vix_central, vix_central
from src.router.tradingview import tradingview
from src.router.crypto_stats import crypto_stats
import src.config.config as config
from src.db.redis import Redis

app = FastAPI()
app.include_router(tradingview.router)

# stocks
app.include_router(thirdparty_vix_central.router)
app.include_router(vix_central.router)
app.include_router(thirdparty_barchart.router)
# crypto
app.include_router(cryptoquant.router)
app.include_router(sentiment.router)
app.include_router(crypto_stats.router)

env = os.getenv('ENV')

logger = logging.getLogger('Server')
PUBLIC_ROUTES = {
    ('GET', '/healthz'),
    ('POST', '/tradingview/daily-stocks'),
}


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
    init_market_data_api()

@app.on_event("shutdown")
async def shutdown_event():
    await Dependencies.cleanup()
    await Redis.stop_redis()

@app.middleware("http")
async def log_request_and_time_taken(request: Request, call_next):
    start_time = time.time()
    logger.info(
        "Request: method=%s path=%s client=%s",
        request.method,
        request.url.path,
        request.client,
    )
    response = await call_next(request)
    time_elapsed = time.time() - start_time
    response.headers["X-Process-Time"] = str(time_elapsed)
    logger.info(
        "Response: method=%s path=%s status=%s elapsed_seconds=%s",
        request.method,
        request.url.path,
        response.status_code,
        time_elapsed,
    )
    return response

@app.middleware("http")
async def auth_check(request: Request, call_next):
    if config.get_env() != 'prod':
        logger.info('env is not prod. Skipping auth')
        return await call_next(request)

    if (request.method, request.url.path) in PUBLIC_ROUTES:
        return await call_next(request)

    auth_token = request.headers.get('X-Api-Auth')
    if not auth_token or auth_token != config.get_api_auth_token():
        logger.error('X-Api-Auth token is missing')
        return JSONResponse(status_code=500, content={'data': 'You shall not pass'})
    return await call_next(request)

@app.get("/healthz")
async def heath_check():
    return {"data": "Market data notification is running!"}
