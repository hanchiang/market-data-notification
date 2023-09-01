import asyncio
import logging

from telegram import BotCommand
from telegram.ext import CommandHandler, Application

from src.config import config
from src.dependencies import Dependencies
from src.notification_destination.telegram_notification import init_telegram_applications, chat_id_to_telegram_client
import src.notification_destination.crypto_telegram_inputs as telegram_inputs

logger = logging.getLogger('telegram app')
async def configure_crypto_app():
    logger.info('Configure bot commands')
    telegram_app: Application = chat_id_to_telegram_client[config.get_telegram_crypto_dev_id()]

    await telegram_app.bot.set_my_commands([
        BotCommand(command='help', description='Find out what I can do'),
        BotCommand(command='fear_greed', description='Display fear greed data'),
        BotCommand(command='top_sectors_24h', description='Display top sectors in the past 24 hours'),
        BotCommand(command='top_gainers_24h', description='Display top gainers in the past 24 hours'),
        BotCommand(command='top_losers_24h', description='Display top losers in the past 24 hours'),
        BotCommand(command='top_trending_24h', description='Display top trending in the past 24 hours'),
        BotCommand(command='newly_added_24h', description='Display newly added in the past 24 hours'),
    ])

async def start_telegram_app():
    logger.info('start telegram app')
    init_telegram_applications(local_mode=True)
    await Dependencies.build()

    await start_crypto_telegram_app()

async def start_crypto_telegram_app():
    await configure_crypto_app()

    crypto_sentiment_service = Dependencies.get_crypto_sentiment_service()

    crypto_telegram_input = telegram_inputs.CryptoTelegramCommandHandler(sentiment_service=crypto_sentiment_service)

    help_handler = CommandHandler('help', crypto_telegram_input.help)
    fear_greed_handler = CommandHandler('fear_greed', crypto_telegram_input.fear_greed)

    telegram_app: Application = chat_id_to_telegram_client[config.get_telegram_crypto_dev_id()]
    telegram_app.add_handler(help_handler)
    telegram_app.add_handler(fear_greed_handler)

    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()


if __name__ == '__main__':
    init_telegram_applications(local_mode=True)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_telegram_app())
    start_telegram_app()