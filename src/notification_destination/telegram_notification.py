import logging

import telegram
import src.config.config as config
from src.type.market_data_type import MarketDataType
from src.util.exception import get_exception_message
from src.util.my_telegram import escape_markdown

# TODO: Clean up

stocks_bot = None
stocks_admin_bot = None
stocks_dev_bot = None
crypto_bot = None
crypto_admin_bot = None
crypto_dev_bot = None

chat_id_to_telegram_client = {}
market_data_type_to_admin_chat_id = {}
market_data_type_to_chat_id = {}

logger = logging.getLogger('Telegram notification')

def init_telegram_bots():
    global stocks_bot, stocks_admin_bot, stocks_dev_bot, crypto_bot, crypto_admin_bot, crypto_dev_bot
    logger.info('Initialising telegram bots')
    stocks_bot = telegram.Bot(token=config.get_telegram_stocks_bot_token())
    stocks_admin_bot = telegram.Bot(token=config.get_telegram_stocks_admin_bot_token())
    stocks_dev_bot = telegram.Bot(token=config.get_telegram_stocks_dev_bot_token())
    crypto_bot = telegram.Bot(token=config.get_telegram_crypto_bot_token())
    crypto_admin_bot = telegram.Bot(token=config.get_telegram_crypto_admin_bot_token())
    crypto_dev_bot = telegram.Bot(token=config.get_telegram_crypto_dev_bot_token())

    global chat_id_to_telegram_client
    chat_id_to_telegram_client[config.get_telegram_stocks_channel_id()] = stocks_bot
    chat_id_to_telegram_client[config.get_telegram_stocks_admin_id()] = stocks_admin_bot
    chat_id_to_telegram_client[config.get_telegram_stocks_dev_id()] = stocks_dev_bot

    chat_id_to_telegram_client[config.get_telegram_crypto_channel_id()] = crypto_bot
    chat_id_to_telegram_client[config.get_telegram_crypto_admin_id()] = crypto_admin_bot
    chat_id_to_telegram_client[config.get_telegram_crypto_dev_id()] = crypto_dev_bot

    global market_data_type_to_admin_chat_id
    market_data_type_to_admin_chat_id[MarketDataType.STOCKS] = config.get_telegram_stocks_admin_id()
    market_data_type_to_admin_chat_id[MarketDataType.CRYPTO] = config.get_telegram_crypto_admin_id()

    global market_data_type_to_chat_id
    market_data_type_to_chat_id[MarketDataType.STOCKS] = config.get_telegram_stocks_channel_id()
    market_data_type_to_chat_id[MarketDataType.CRYPTO] = config.get_telegram_crypto_channel_id()



async def send_message_to_channel(message: str, chat_id, market_data_type: MarketDataType):
    if config.get_disable_telegram():
        logger.info('Telegram is disabled')
        return

    if market_data_type is None:
        logger.warning('market_data_type is not passed in')
        return

    if config.get_is_testing_telegram() or config.get_simulate_tradingview_traffic():
        chat_id = get_dev_channel_id_from_market_data_type(market_data_type)

    try:
        res = await chat_id_to_telegram_client[chat_id].send_message(chat_id, text=message, parse_mode='MarkdownV2')
        return res
    except Exception as e:
        logger.error(get_exception_message(e))
        await chat_id_to_telegram_client[chat_id].send_message(chat_id, text=escape_markdown(str(e)), parse_mode='MarkdownV2')

async def send_message_to_admin(message: str, market_data_type: MarketDataType):
    channel_id = get_admin_channel_id_from_market_data_type(market_data_type)
    res = await stocks_admin_bot.send_message(chat_id=channel_id, text=message, parse_mode='MarkdownV2')
    print_telegram_message(res)
    return res

def get_admin_channel_id_from_market_data_type(market_data_type: MarketDataType):
    if market_data_type == MarketDataType.CRYPTO:
        return config.get_telegram_crypto_admin_id()
    return config.get_telegram_stocks_admin_id()

def get_dev_channel_id_from_market_data_type(market_data_type: MarketDataType):
    if market_data_type == MarketDataType.CRYPTO:
        return config.get_telegram_crypto_dev_id()
    return config.get_telegram_stocks_dev_id()

def print_telegram_message(res: telegram.Message):
    logging.info(f"Sent to {res.chat.title} {res.chat.type} at {res.date}. Message id {res.id}")