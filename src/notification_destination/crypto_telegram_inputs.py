from telegram import Update
from telegram.ext import ContextTypes

from src.job.crypto.message_formatter.telegram_formatter import crypto_sentiment_message_formatter
from src.service.crypto_sentiment import CryptoSentimentService


class CryptoTelegramCommandHandler:
    def __init__(self, sentiment_service: CryptoSentimentService):
        self.sentiment_service = sentiment_service

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text='I can provide data on fear/greed, top sectors, top gainers/losers, trending, most visited, newly added.')

    async def fear_greed(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = await self.sentiment_service.get_crypto_fear_greed_index()
        formatted_message = crypto_sentiment_message_formatter(data, should_escape=False)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=formatted_message)

