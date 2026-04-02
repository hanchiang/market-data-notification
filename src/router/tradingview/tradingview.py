import json
import logging

from fastapi import APIRouter, Request

from src.config import config
from src.dependencies import Dependencies
from src.event.event_emitter import async_ee
from src.type.market_data_type import MarketDataType
from src.type.trading_view import TradingViewDataType
from src.util.date_util import get_current_date
from src.util.exception import get_exception_message
from src.util.my_telegram import format_messages_to_telegram, escape_markdown

router = APIRouter(prefix="/tradingview")

logger = logging.getLogger('Trading view')


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
        body = await parse_tradingview_request_body(request)
        filtered_body = filter_tradingview_request_body(body)
        logger.info(filtered_body)
    except Exception as e:
        logger.error(get_exception_message(e))
        messages.append(f"JSON body error: {get_exception_message(e, should_escape_markdown=True)}")
        message = format_messages_to_telegram(messages)
        async_ee.emit('send_to_telegram', message=message, channel=config.get_telegram_stocks_admin_id(), market_data_type=MarketDataType.STOCKS)
        return {"data": "OK"}

    request_test_mode = body.get('test_mode', 'false') == 'true'

    if config.get_env() == 'prod' and request_test_mode:
        messages.append(
            '*Ignored TradingView test_mode request in prod.*\n'
            f'*Request ip:* {escape_markdown(request.client.host)}\n'
            f'*Body:* {escape_markdown(str(filtered_body))}'
        )
        async_ee.emit(
            'send_to_telegram',
            message=format_messages_to_telegram(messages),
            channel=config.get_telegram_stocks_admin_id(),
            market_data_type=MarketDataType.STOCKS,
        )
        return {"data": None}

    if not request_test_mode and body.get('secret', None) != config.get_tradingview_webhook_secret():
        messages.append(
            f"*[Potential malicious request warning]‼️*\n*Incorrect tradingview webhook secret{escape_markdown('.')}*\n*Headers:* {escape_markdown(str(request.headers))}\n*Body:* {escape_markdown(str(body))}\n*Request ip:* {escape_markdown(request.client.host)}")
        message = format_messages_to_telegram(messages)
        async_ee.emit('send_to_telegram', message=message, channel=config.get_telegram_stocks_admin_id(), market_data_type=MarketDataType.STOCKS)
        return {"data": "OK"}

    trading_view_ips = config.get_trading_view_ips()
    whitelist_ips = config.get_whitelist_ips()
    if not config.get_simulate_tradingview_traffic() and request.client.host not in trading_view_ips and request.client.host not in whitelist_ips:
        messages.append(
            f"*[Potential malicious request warning]‼️*\n*Request ip {escape_markdown(request.client.host)} is not from trading view*: {escape_markdown(str(trading_view_ips))} or whitelist: {escape_markdown(str(whitelist_ips))}\n*Headers:* {escape_markdown(str(request.headers))}\n*Body:* {escape_markdown(str(filtered_body))}\n")
        message = format_messages_to_telegram(messages)
        async_ee.emit('send_to_telegram', message=message, channel=config.get_telegram_stocks_admin_id(), market_data_type=MarketDataType.STOCKS)
        return {"data": "OK"}

    tradingview_service = Dependencies.get_tradingview_service()
    # Save to redis
    now = get_current_date()
    key = tradingview_service.get_redis_key_for_stocks(type=TradingViewDataType(filtered_body.get('type')))
    json_data = {}
    timestamp = get_tradingview_score(filtered_body, fallback=now)
    [add_res, remove_res] = await tradingview_service.save_tradingview_data(
        data=json.dumps(filtered_body),
        key=key,
        score=timestamp,
        test_mode=request_test_mode,
    )

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
    logger.info(f'Successfully saved trading view data for {str(now)}, key: {key}, score: {timestamp}, days to store: {config.get_trading_view_days_to_store()}, data: {json_data}')
    # sleep for a bit, telegram client will timeout if concurrent requests come in
    # await sleep()
    async_ee.emit('send_to_telegram', message=format_messages_to_telegram(messages), channel=config.get_telegram_stocks_admin_id(), market_data_type=MarketDataType.STOCKS)
    return {
        'data': {
            'num_added': add_res,
            'num_removed': remove_res
        }
    }

async def parse_tradingview_request_body(request: Request) -> dict:
    raw_body = await request.body()
    if raw_body is None:
        raise ValueError('TradingView request body is empty')

    raw_text = raw_body.decode('utf-8').strip()
    if not raw_text:
        raise ValueError('TradingView request body is empty')

    body = parse_tradingview_body_text(raw_text)
    if not isinstance(body, dict):
        raise ValueError('TradingView request body must be a JSON object')

    return body

def parse_tradingview_body_text(raw_text: str):
    candidates = [raw_text]
    if '\\"' in raw_text:
        candidates.append(raw_text.replace('\\"', '"'))
        try:
            candidates.append(raw_text.encode('utf-8').decode('unicode_escape'))
        except UnicodeDecodeError:
            pass

    last_error = None
    seen = set()
    while candidates:
        candidate = candidates.pop(0)
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue

        if isinstance(parsed, str):
            candidates.append(parsed)
            continue

        return parsed

    if last_error is not None:
        raise last_error

    raise ValueError('TradingView request body is not valid JSON')

def filter_tradingview_request_body(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != 'secret'}


def get_tradingview_score(
    body: dict,
    fallback,
) -> int:
    unix_ms = body.get('unix_ms')
    if unix_ms is None:
        return int(fallback.timestamp())

    try:
        parsed = int(unix_ms)
    except (TypeError, ValueError):
        logger.warning('Invalid TradingView unix_ms %r, falling back to current date score', unix_ms)
        return int(fallback.timestamp())

    # TradingView payloads send millisecond timestamps, while the backend stores
    # Redis scores as Unix seconds for downstream date formatting and freshness checks.
    return parsed // 1000 if parsed >= 10**12 else parsed
