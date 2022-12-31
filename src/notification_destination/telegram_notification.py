import telegram
import src.config.config as config

bot = telegram.Bot(token=config.get_telegram_bot_token())
admin_bot = telegram.Bot(token=config.get_telegram_admin_bot_token())

async def send_message_to_channel(message: str, chat_id = config.get_telegram_channel_id()):
    if config.get_disable_telegram():
        print('Telegram is disabled')
        return
    res = await bot.send_message(chat_id, text=message, parse_mode='MarkdownV2')
    return res

async def send_message_to_admin(message: str, chat_id = config.get_telegram_admin_id()):
    res = await admin_bot.send_message(chat_id, text=message, parse_mode='MarkdownV2')
    return res