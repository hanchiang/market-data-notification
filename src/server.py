import asyncio
from functools import reduce
from typing import List, Any

from fastapi import FastAPI, Request
import uvicorn
import os
from src.dependencies import Dependencies
from src.router.vix_central import thirdparty_vix_central, vix_central
import src.notification_destination.telegram_notification as telegram_notification
from src.service.vix_central import RecentVixFuturesValues
import src.config.config as config
from src.util.my_telegram import escape_markdown

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

@app.on_event("shutdown")
async def shutdown_event():
    await Dependencies.cleanup()


@app.get("/healthz")
async def heath_check():
    return {"data": "Market data notification is running!"}


@app.post("/tradingview-webhook")
async def tradingview_webhook(request: Request):
    # Body is a list of: symbol, timeframe(e.g. 1d), close, ema20
    print(f"{request.method} {request.url} Received request from {request.client}")

    messages = []
    if config.get_is_testing_telegram():
        messages.insert(0, '*THIS IS A TEST MESSAGE*')

    if request.headers.get('x-tradingview-webhook-secret', None) != config.get_tradingview_webhook_secret():
        messages.append(f"*[Potential malicious request warning]‼️*\nIncorrect tradingview webhook secret{escape_markdown('.')}\nHeaders: *{escape_markdown(str(request.headers))}*\nRequest ip: *{escape_markdown(request.client.host)}*")
        message = format_messages_to_telegram(messages)
        print(message)
        asyncio.create_task(telegram_notification.send_message_to_admin(message=message))
        return {"data": "OK"}

    trading_view_ips = config.get_trading_view_ips()
    if not config.get_simulate_tradingview_traffic() and request.client.host not in trading_view_ips:
        messages.append(f"*[Potential malicious request warning]‼️*\nRequest ip *{escape_markdown(request.client.host)}* is not from trading view: *{escape_markdown(str(trading_view_ips))}*")
        message = format_messages_to_telegram(messages)
        print(message)
        asyncio.create_task(telegram_notification.send_message_to_admin(message=message))
        return {"data": "OK"}

    vix_central_service = Dependencies.get_vix_central_service()
    vix_central_data = await vix_central_service.get_recent_values()
    vix_central_message = format_vix_central_message(vix_central_data)

    body = await request.json()
    tradingview_message = format_tradingview_message(body)
    tradingview_message = f"*Trading view market data:*{tradingview_message}"

    messages.append(vix_central_message)
    messages.append(tradingview_message)

    telegram_message = format_messages_to_telegram(messages)

    asyncio.create_task(telegram_notification.send_message_to_channel(message=telegram_message))
    return {"data": "OK"}

    # res = await telegram_notification.send_message_to_channel(message=telegram_message)
    # if not res:
    #     return {"data": "OK"}
    # print(f"Sent to {res.chat.title} {res.chat.type} at {res.date}. Message id {res.id}")
    # return {"data": f"Sent to {res.chat.title} {res.chat.type} at {res.date}. Message id {res.id}"}

def format_messages_to_telegram(messages: list[str]) -> str:
    return escape_markdown("\n-----------------------------------------------------------------\n").join(
        messages)

# TODO: cleanup trading view webhook code
def format_vix_central_message(vix_central_value: RecentVixFuturesValues):
    message = reduce(format_vix_futures_values, vix_central_value.vix_futures_values,
                     f"*VIX central data for {vix_central_value.vix_futures_values[0].futures_date} futures:*")
    if vix_central_value.is_contango_decrease_for_past_n_days:
        message = f"{message}\n*Contango has been decreasing for the past {vix_central_value.contango_decrease_past_n_days} days ‼️*"
    return message


def format_vix_futures_values(res, curr):
    message = f"{res}\ndate: {escape_markdown(curr.current_date)}, contango %: {escape_markdown(curr.formatted_contango)}"
    if curr.is_contango_single_day_decrease_alert:
        threshold = f"{curr.contango_single_day_decrease_alert_ratio:.1%}"
        message = f"{message}{escape_markdown('.')} *Contango changed by more than {escape_markdown(threshold)} from the previous day* ‼️"
    return message


def format_tradingview_message(payload: List[Any]):
    message = ''
    potential_overextended_by_symbol = config.get_potential_overextended_by_symbol()

    sorted_payload = sorted(payload, key=payload_sorter)

    for p in sorted_payload:
        symbol = p['symbol'].upper()

        close = p['close']
        ema20 = p['ema20']
        close_ema20_delta_ratio = (close - ema20) / ema20 if close > ema20 else -(ema20 - close) / ema20

        # For VIX, compare close and overextended threshold. Other other symbols, compare close and ema20
        close_ema20_delta_percent = f"{close_ema20_delta_ratio:.2%}"

        if symbol != 'VIX':
            message = f"{message}\nsymbol: {symbol}, close: {escape_markdown(str(close))}, {escape_markdown('ema20(1D)')}: {escape_markdown(str(f'{ema20:.2f}'))}, % change: {escape_markdown(close_ema20_delta_percent)}"
            close_ema20_direction = 'up' if close > ema20 else 'down'
        else:
            message = f"{message}\nsymbol: {symbol}, close: {escape_markdown(str(close))}"

        if potential_overextended_by_symbol.get(symbol, None) is not None:
            if symbol != 'VIX':
                if potential_overextended_by_symbol[symbol].get(close_ema20_direction) is not None:
                    overextended_threshold = potential_overextended_by_symbol[symbol][close_ema20_direction]
                    if abs(close_ema20_delta_ratio) > abs(overextended_threshold):
                        message = f"{message}, *greater than {escape_markdown(f'{overextended_threshold:.2%}')} when it is {'above' if close_ema20_direction == 'up' else 'below'} the ema20, watch for potential rebound* ‼️"
            else:
                vix_overextended_up_threshold = potential_overextended_by_symbol[symbol]['up']
                vix_overextended_down_threshold = potential_overextended_by_symbol[symbol]['down']
                if close >= vix_overextended_up_threshold:
                    message = f"{message}, *VIX is near the top around {f'{escape_markdown(str(vix_overextended_up_threshold))}'}, meaning market is near the bottom, watch for potential rebound* ‼️"
                elif close <= vix_overextended_down_threshold:
                    message = f"{message}, *VIX is near the bottom around {f'{escape_markdown(str(vix_overextended_down_threshold))}'}, meaning market is near the top, watch for potential rebound* ‼️"
    return message

def payload_sorter(item):
    symbol = item['symbol'].upper()
    # VIX should appear last
    return symbol if symbol != 'VIX' else 'zzzzzzzzzzzzzz'