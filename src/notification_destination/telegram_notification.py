import telegram
import src.config.config as config

bot = telegram.Bot(token=config.get_telegram_bot_token())
admin_bot = telegram.Bot(token=config.get_telegram_admin_bot_token())
dev_bot = telegram.Bot(token=config.get_telegram_dev_bot_token())

chat_id_to_telegram_client = {}

chat_id_to_telegram_client[config.get_telegram_channel_id()] = bot
chat_id_to_telegram_client[config.get_telegram_admin_id()] = admin_bot
chat_id_to_telegram_client[config.get_telegram_dev_id()] = dev_bot

async def send_message_to_channel(message: str, chat_id):
    if config.get_disable_telegram():
        print('Telegram is disabled')
        return

    if config.get_is_testing_telegram() or config.get_simulate_tradingview_traffic():
        chat_id = config.get_telegram_dev_id()

    res = await chat_id_to_telegram_client[chat_id].send_message(chat_id, text=message, parse_mode='MarkdownV2')
    return res

async def send_message_to_admin(message: str):
    res = await admin_bot.send_message(chat_id=config.get_telegram_admin_id(), text=message, parse_mode='MarkdownV2')
    return res