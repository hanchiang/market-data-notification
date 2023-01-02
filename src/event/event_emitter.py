from pyee.asyncio import  AsyncIOEventEmitter
from src.notification_destination import telegram_notification
from src.util.my_telegram import escape_markdown

ee = AsyncIOEventEmitter()

@ee.on('send_to_telegram')
async def send_to_telegram_handler(*args, **kwargs):
    try:
        channel = kwargs['channel']
        message = kwargs['message']
        res = await telegram_notification.send_message_to_channel(message=message, chat_id=channel)
        if res:
            print(f"Sent to {res.chat.title} {res.chat.type} at {res.date}. Message id {res.id}")
    except Exception as e:
        print(e)
        await telegram_notification.send_message_to_admin(escape_markdown(str(e)))
