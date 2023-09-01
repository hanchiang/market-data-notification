from typing import Dict

import logging

import telegram
from telegram.ext import Application

import src.config.config as config
from src.type.market_data_type import MarketDataType
from src.util.exception import get_exception_message
from src.util.my_telegram import escape_markdown

# TODO: Clean up
chat_id_to_telegram_client: Dict[int, Application] = {}
market_data_type_to_admin_chat_id: Dict[MarketDataType, int] = {}
market_data_type_to_chat_id: Dict[MarketDataType, int] = {}

logger = logging.getLogger('Telegram notification')

def init_telegram_applications(local_mode=False):

    global chat_id_to_telegram_client

    if chat_id_to_telegram_client.get(config.get_telegram_stocks_channel_id()) is not None and \
            chat_id_to_telegram_client.get(config.get_telegram_stocks_dev_id()) is not None and \
        chat_id_to_telegram_client.get(config.get_telegram_stocks_admin_id()) is not None and \
        chat_id_to_telegram_client.get(config.get_telegram_crypto_channel_id()) is not None and \
        chat_id_to_telegram_client.get(config.get_telegram_crypto_dev_id()) is not None and \
        chat_id_to_telegram_client.get(config.get_telegram_crypto_admin_id()) is not None:
        print('Telegram applications are already initialised')
        return

    print(f'Initialising telegram applications, local mode: {local_mode}')

    if local_mode:
        stocks_application = Application.builder().token(config.get_telegram_stocks_bot_token()).base_url('http://localhost:8081/bot').build()
        stocks_admin_application = Application.builder().token(config.get_telegram_stocks_admin_bot_token()).base_url('http://localhost:8081/bot').build()
        stocks_dev_application = Application.builder().token(config.get_telegram_stocks_dev_bot_token()).base_url('http://localhost:8081/bot').build()
        crypto_application = Application.builder().token(config.get_telegram_crypto_bot_token()).base_url('http://localhost:8081/bot').build()
        crypto_admin_application = Application.builder().token(config.get_telegram_crypto_admin_bot_token()).base_url('http://localhost:8081/bot').build()
        crypto_dev_application = Application.builder().token(config.get_telegram_crypto_dev_bot_token()).base_url('http://localhost:8081/bot').build()
    else:
        stocks_application = Application.builder().token(config.get_telegram_stocks_bot_token()).build()
        stocks_admin_application = Application.builder().token(config.get_telegram_stocks_admin_bot_token()).build()
        stocks_dev_application = Application.builder().token(config.get_telegram_stocks_dev_bot_token()).build()
        crypto_application = Application.builder().token(config.get_telegram_crypto_bot_token()).build()
        crypto_admin_application = Application.builder().token(config.get_telegram_crypto_admin_bot_token()).build()
        crypto_dev_application = Application.builder().token(config.get_telegram_crypto_dev_bot_token()).build()

    chat_id_to_telegram_client[config.get_telegram_stocks_channel_id()] = stocks_application
    chat_id_to_telegram_client[config.get_telegram_stocks_admin_id()] = stocks_admin_application
    chat_id_to_telegram_client[config.get_telegram_stocks_dev_id()] = stocks_dev_application

    chat_id_to_telegram_client[config.get_telegram_crypto_channel_id()] = crypto_application
    chat_id_to_telegram_client[config.get_telegram_crypto_admin_id()] = crypto_admin_application
    chat_id_to_telegram_client[config.get_telegram_crypto_dev_id()] = crypto_dev_application

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
        res = await chat_id_to_telegram_client[chat_id].bot.send_message(chat_id, text=message, parse_mode='MarkdownV2')
        return res
    except Exception as e:
        logger.error(get_exception_message(e))
        await chat_id_to_telegram_client[chat_id].send_message(chat_id, text=escape_markdown(get_exception_message(e, should_escape_markdown=True)), parse_mode='MarkdownV2')

async def send_message_to_admin(message: str, market_data_type: MarketDataType):
    channel_id = get_admin_channel_id_from_market_data_type(market_data_type)
    res = await chat_id_to_telegram_client[channel_id].bot.send_message(chat_id=channel_id, text=message, parse_mode='MarkdownV2')
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
    logger.info(f"Sent to {res.chat.title} {res.chat.type} at {res.date}. Message id {res.id}")