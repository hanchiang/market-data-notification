from pyee.asyncio import  AsyncIOEventEmitter

from notification_destination.telegram_notification import print_telegram_message
from src.notification_destination import telegram_notification
from src.util.my_telegram import escape_markdown

async_ee = AsyncIOEventEmitter()

@async_ee.on('error')
async def on_error(message):
    print(message)
    await telegram_notification.send_message_to_admin(escape_markdown(str(message)))

@async_ee.on('send_to_telegram')
async def send_to_telegram_handler(*args, **kwargs):
    try:
        channel = kwargs['channel']
        message = kwargs['message']
        res = await telegram_notification.send_message_to_channel(message=message, chat_id=channel)
        if res:
            print_telegram_message(res)
    except Exception as e:
        print(e)
        await telegram_notification.send_message_to_admin(escape_markdown(str(e)))
