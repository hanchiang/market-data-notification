import json
import time

from fastapi import FastAPI, Request
import uvicorn
import os

from src.job.market_data_notification import get_redis_key
from src.dependencies import Dependencies
from src.router.vix_central import thirdparty_vix_central, vix_central
import src.config.config as config
from src.event.event_emitter import async_ee
from src.util.date_util import get_current_datetime
from src.util.my_telegram import format_messages_to_telegram, escape_markdown
from src.db.redis import Redis

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
    await Dependencies.build()
    await Redis.start_redis()

@app.on_event("shutdown")
async def shutdown_event():
    await Dependencies.cleanup()
    await Redis.stop_redis()

@app.middleware("http")
async def log_request_and_time_taken(request: Request, call_next):
    start_time = time.time()
    print(f"{request.method} {request.url}, headers: {request.headers} client: {request.client}")
    response = await call_next(request)
    time_elapsed = time.time() - start_time
    response.headers["X-Process-Time"] = str(time_elapsed)
    print(f"Response: {response.headers}")
    return response

@app.get("/healthz")
async def heath_check():
    return {"data": "Market data notification is running!"}

@app.post("/tradingview-webhook")
async def tradingview_webhook(request: Request):
    # request body: { secret: '', data: [{ symbol, timeframe(e.g. 1d), close, ema20 }] }

    messages = []
    try:
        body = await request.json()
        filtered_body = filter_tradingview_request_body(body)
        print(filtered_body)
    except Exception as e:
        print(e)
        messages.append(f"JSON body error: {escape_markdown(str(e))}")
        message = format_messages_to_telegram(messages)
        async_ee.emit('send_to_telegram', message=message, channel=config.get_telegram_admin_id())
        return {"data": "OK"}

    if body.get('secret', None) != config.get_tradingview_webhook_secret():
        messages.append(
            f"*[Potential malicious request warning]‼️*\n*Incorrect tradingview webhook secret{escape_markdown('.')}*\n*Headers:* {escape_markdown(str(request.headers))}\n*Body:* {escape_markdown(str(body))}\n*Request ip:* {escape_markdown(request.client.host)}")
        message = format_messages_to_telegram(messages)
        async_ee.emit('send_to_telegram', message=message, channel=config.get_telegram_admin_id())
        return {"data": "OK"}

    trading_view_ips = config.get_trading_view_ips()
    if not config.get_simulate_tradingview_traffic() and request.client.host not in trading_view_ips:
        messages.append(
            f"*[Potential malicious request warning]‼️*\n*Request ip {escape_markdown(request.client.host)} is not from trading view: {escape_markdown(str(trading_view_ips))}*\n*Headers:* {escape_markdown(str(request.headers))}\n*Body:* {escape_markdown(str(filtered_body))}\n")
        message = format_messages_to_telegram(messages)
        async_ee.emit('send_to_telegram', message=message, channel=config.get_telegram_admin_id())
        return {"data": "OK"}

    now = get_current_datetime()
    key = get_redis_key(now)
    data = await Redis.get_client().set(key, json.dumps(filtered_body), ex=config.get_trading_view_ttl())

    messages.append(f'Successfully saved trading view data at *{escape_markdown(str(get_current_datetime()))}*, key: *{escape_markdown(key)}*')
    async_ee.emit('send_to_telegram', message=format_messages_to_telegram(messages), channel=config.get_telegram_admin_id())
    return {"data": data}

def filter_tradingview_request_body(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != 'secret'}