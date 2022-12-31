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

  if request.headers.get('x-tradingview-webhook-secret', None) != config.get_tradingview_webhook_secret():
      message = f"[Potential malicious request warning]‼️ Trading view webhook secret {request.headers.get('x-tradingview-webhook-secret', None)} is incorrect"
      asyncio.create_task(telegram_notification.send_message_to_admin(message=message))
      return {"data": "OK"}

  trading_view_ips = config.get_trading_view_ips()
  if not config.get_is_testing_telegram() and not config.get_simulate_tradingview_traffic() and request.client.host not in trading_view_ips:
      message = f"[Potential malicious request warning]‼️Request ip {request.client.host} is not from trading view {trading_view_ips}"
      print(message)
      asyncio.create_task(telegram_notification.send_message_to_admin(message=escape_markdown(message)))
      return {"data": "OK"}

  vix_central_service = Dependencies.get_vix_central_service()
  vix_central_data = await vix_central_service.get_recent_values()
  vix_central_message = format_vix_central_message(vix_central_data)

  body = await request.json()
  tradingview_message = format_tradingview_message(body)
  tradingview_message = f"*Trading view market data:*{tradingview_message}"

  messages = [vix_central_message, tradingview_message]
  if config.get_is_testing_telegram():
    messages.insert(0, '*THIS IS A TEST MESSAGE*')

  telegram_message = escape_markdown("\n-----------------------------------------------------------------\n").join(messages)

  res = await telegram_notification.send_message_to_channel(message=telegram_message)
  if not res:
      return {"data": "OK"}
  print(f"Sent to {res.chat.title} {res.chat.type} at {res.date}. Message id {res.id}")
  return {"data": f"Sent to {res.chat.title} {res.chat.type} at {res.date}. Message id {res.id}"}

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

  for p in payload:
    symbol = p['symbol'].upper()
    close = p['close']
    ema20 = p['ema20']
    delta = (close - ema20) / ema20 if close > ema20 else -(ema20 - close) / ema20

    delta_percent = f"{delta:.2%}"
    message = f"{message}\nsymbol: {symbol}, close: {escape_markdown(str(close))}, {escape_markdown('ema20(1D)')}: {escape_markdown(str(ema20))}, % change: {escape_markdown(delta_percent)}"

    direction = 'up' if close > ema20 else 'down'
    if potential_overextended_by_symbol.get(symbol, None) is not None:
      if potential_overextended_by_symbol[symbol].get(direction) is not None:
        overextended_threshold = potential_overextended_by_symbol[symbol][direction]
        overextended_threshold_percent = f"{overextended_threshold:.2%}"
        if abs(delta) > abs(overextended_threshold):
          message = f"{message}, *greater than {escape_markdown(overextended_threshold_percent)} when it is {'above' if direction == 'up' else 'below'} the ema20, watch for potential rebound* ‼️"

    return message
