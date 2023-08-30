import json

from fastapi import APIRouter, Request

from src.config import config
from src.dependencies import Dependencies
from src.event.event_emitter import async_ee
from src.type.market_data_type import MarketDataType
from src.type.trading_view import TradingViewDataType
from src.util.date_util import get_current_date
from src.util.my_telegram import format_messages_to_telegram, escape_markdown
from src.util.sleep import sleep

router = APIRouter(prefix="/tradingview")

@router.post("/daily-stocks")
async def tradingview_daily_stocks_data(request: Request):
    # for economy_indicator type, there is no ema20s, volumes
    # request body: { type(stocks, economy_indicator), secret, test_mode, unix_ms, data: [{ symbol, timeframe(e.g. 1d), close_prices: [], ema20s: [], volumes: [] }] }
    # TODO: add threshold
    # vix spike threshold: 15-20%
    # baml high yield index spread spike: 5-10%
    # skew: 140-150

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

    test_mode = body.get('test_mode', 'false') == 'true'
    if test_mode:
        config.set_is_testing_telegram('true' if test_mode else 'false')

    if not config.get_is_testing_telegram() and body.get('secret', None) != config.get_tradingview_webhook_secret():
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

    tradingview_service = Dependencies.get_tradingview_service()
    # Save to redis
    # TODO: use unix_ms as the score
    now = get_current_date()
    key = tradingview_service.get_redis_key_for_stocks(type=TradingViewDataType(filtered_body.get('type')))
    json_data = {}
    timestamp = int(now.timestamp())
    [add_res, remove_res] = await tradingview_service.save_tradingview_data(data=json.dumps(filtered_body), key=key, score=timestamp, test_mode=config.get_is_testing_telegram())

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
        messages.append(f'*{remove_res}* elements of type *{escape_markdown(filtered_body.get("type"))}* is removed from redis, maximum number of records to store in redis: *{config.get_trading_view_days_to_store()}*')

    save_message = f'Successfully saved trading view data for type: *{escape_markdown(filtered_body.get("type"))}* at *{escape_markdown(str(now))}*, key: *{escape_markdown(key)}*, score: *{timestamp}*, days to store: *{config.get_trading_view_days_to_store()}*'
    messages.append(save_message)
    print(f'Successfully saved trading view data for {str(now)}, key: {key}, score: {timestamp}, days to store: {config.get_trading_view_days_to_store()}, data: {json_data}')
    # sleep for a bit, telegram client will timeout if concurrent requests come in
    # await sleep()
    async_ee.emit('send_to_telegram', message=format_messages_to_telegram(messages), channel=config.get_telegram_stocks_admin_id(), market_data_type=MarketDataType.STOCKS)
    return {
        'data': {
            'num_added': add_res,
            'num_removed': remove_res
        }
    }

def filter_tradingview_request_body(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != 'secret'}