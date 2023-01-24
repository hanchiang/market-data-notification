import telegram
import src.config.config as config
from src.type.market_data_type import MarketDataType
from src.util.my_telegram import escape_markdown

bot = telegram.Bot(token=config.get_telegram_stocks_bot_token())
admin_bot = telegram.Bot(token=config.get_telegram_stocks_admin_bot_token())
dev_bot = telegram.Bot(token=config.get_telegram_stocks_dev_bot_token())

chat_id_to_telegram_client = {}

chat_id_to_telegram_client[MarketDataType.STOCKS.value] = {}
chat_id_to_telegram_client[MarketDataType.STOCKS.value][config.get_telegram_stocks_channel_id()] = bot
chat_id_to_telegram_client[MarketDataType.STOCKS.value][config.get_telegram_stocks_admin_id()] = admin_bot
chat_id_to_telegram_client[MarketDataType.STOCKS.value][config.get_telegram_stocks_dev_id()] = dev_bot

async def send_message_to_channel(message: str, chat_id, market_data_type: MarketDataType):
    if config.get_disable_telegram():
        print('Telegram is disabled')
        return

    if config.get_is_testing_telegram() or config.get_simulate_tradingview_traffic():
        chat_id = config.get_telegram_stocks_dev_id()

    try:
        res = await chat_id_to_telegram_client[chat_id].send_message(chat_id, text=message, parse_mode='MarkdownV2')
        return res
    except Exception as e:
        print(e)
        await chat_id_to_telegram_client[chat_id].send_message(chat_id, text=escape_markdown(str(e)), parse_mode='MarkdownV2')

async def send_message_to_admin(message: str):
    res = await admin_bot.send_message(chat_id=config.get_telegram_stocks_admin_id(), text=message, parse_mode='MarkdownV2')
    print_telegram_message(res)
    return res

def print_telegram_message(res: telegram.Message):
    print(f"Sent to {res.chat.title} {res.chat.type} at {res.date}. Message id {res.id}")