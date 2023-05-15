import json
import time

from fastapi import FastAPI, Request
import uvicorn
import os

from src.dependencies import Dependencies
from src.job.service.tradingview import get_redis_key_for_stocks, save_tradingview_data
from src.router.chainanalysis import thirdparty_chainanalysis, chainanalysis
from src.router.vix_central import thirdparty_vix_central, vix_central
from src.router.messari import thirdparty_messari, messari
import src.config.config as config
from src.event.event_emitter import async_ee
from src.type.market_data_type import MarketDataType
from src.util.date_util import get_current_date
from src.util.my_telegram import format_messages_to_telegram, escape_markdown
from src.db.redis import Redis

app = FastAPI()
app.include_router(thirdparty_vix_central.router)
app.include_router(vix_central.router)
app.include_router(thirdparty_messari.router)
app.include_router(messari.router)
app.include_router(thirdparty_chainanalysis.router)
app.include_router(chainanalysis.router)

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

@app.post("/tradingview-daily-stocks")
async def tradingview_daily_stocks_data(request: Request):
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
        async_ee.emit('send_to_telegram', message=message, channel=config.get_telegram_stocks_admin_id(), market_data_type=MarketDataType.STOCKS)
        return {"data": "OK"}

    if body.get('secret', None) != config.get_tradingview_webhook_secret():
        messages.append(
            f"*[Potential malicious request warning]‼️*\n*Incorrect tradingview webhook secret{escape_markdown('.')}*\n*Headers:* {escape_markdown(str(request.headers))}\n*Body:* {escape_markdown(str(body))}\n*Request ip:* {escape_markdown(request.client.host)}")
        message = format_messages_to_telegram(messages)
        async_ee.emit('send_to_telegram', message=message, channel=config.get_telegram_stocks_admin_id(), market_data_type=MarketDataType.STOCKS)
        return {"data": "OK"}

    trading_view_ips = config.get_trading_view_ips()
    if not config.get_simulate_tradingview_traffic() and request.client.host not in trading_view_ips:
        messages.append(
            f"*[Potential malicious request warning]‼️*\n*Request ip {escape_markdown(request.client.host)} is not from trading view: {escape_markdown(str(trading_view_ips))}*\n*Headers:* {escape_markdown(str(request.headers))}\n*Body:* {escape_markdown(str(filtered_body))}\n")
        message = format_messages_to_telegram(messages)
        async_ee.emit('send_to_telegram', message=message, channel=config.get_telegram_stocks_admin_id(), market_data_type=MarketDataType.STOCKS)
        return {"data": "OK"}

    # Save to redis
    test_mode = body.get('test_mode', 'false') == 'true'
    if test_mode:
        config.set_is_testing_telegram('true')
    now = get_current_date()
    key = get_redis_key_for_stocks()
    json_data = {}
    timestamp = int(now.timestamp())
    [add_res, remove_res] = await save_tradingview_data(json.dumps(filtered_body), timestamp)

    if add_res == 0 and remove_res == 0:
        messages.append(f'trading view data for {now}, score: *{timestamp}* already exist. skip saving to redis')
        message = format_messages_to_telegram(messages)
        async_ee.emit('send_to_telegram', message=escape_markdown(message), channel=config.get_telegram_stocks_admin_id(), market_data_type=MarketDataType.STOCKS)
        return {"data": None}
    if add_res == 0:
        messages.append(f'0 element is added for *{now}*, score: *{timestamp}*. Please check redis')
        message = format_messages_to_telegram(messages)
        async_ee.emit('send_to_telegram', message=escape_markdown(message), channel=config.get_telegram_stocks_admin_id(), market_data_type=MarketDataType.STOCKS)
        return {"data": None}
    if remove_res > 0:
        messages.append(f'*{remove_res}* elements is removed from redis, maximum number of records to store in redis: *{config.get_trading_view_days_to_store()}*')

    save_message = f'Successfully saved trading view data for *{escape_markdown(str(now))}*, key: *{escape_markdown(key)}*, score: *{timestamp}*, days to store: *{config.get_trading_view_days_to_store()}*'
    messages.append(save_message)
    print(f'Successfully saved trading view data for {str(now)}, key: {key}, score: {timestamp}, days to store: {config.get_trading_view_days_to_store()}, data: {json_data}')
    async_ee.emit('send_to_telegram', message=format_messages_to_telegram(messages), channel=config.get_telegram_stocks_admin_id(), market_data_type=MarketDataType.STOCKS)
    return {"data": add_res}

# TODO: most active options, change in open interest

def filter_tradingview_request_body(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != 'secret'}