import logging
from pyee.asyncio import  AsyncIOEventEmitter

from src.notification_destination import telegram_notification
from src.type.market_data_type import MarketDataType
from src.util.my_telegram import escape_markdown
from src.util.exception import get_exception_message

async_ee = AsyncIOEventEmitter()

logger = logging.getLogger('Event emitter')
@async_ee.on('error')
async def on_error(message):
    logger.error(f"event emitter error: {message}")
    await telegram_notification.send_message_to_admin(escape_markdown(str(f'event emitter error: {message}')), market_data_type=MarketDataType.STOCKS)

@async_ee.on('send_to_telegram')
async def send_to_telegram_handler(*args, **kwargs):
    try:
        channel = kwargs['channel']
        message = kwargs['message']
        market_data_type = kwargs['market_data_type']
        res = await telegram_notification.send_message_to_channel(message=message, chat_id=channel, market_data_type=market_data_type)
        if res:
            telegram_notification.print_telegram_message(res)
    except Exception as e:
        logger.error(f"{get_exception_message(e)}")
        market_data_type = kwargs['market_data_type'] or MarketDataType.STOCKS
        await telegram_notification.send_message_to_admin(f"{get_exception_message(e, should_escape_markdown=True)}", market_data_type=market_data_type)
