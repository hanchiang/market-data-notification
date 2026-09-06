from unittest.mock import AsyncMock

import pytest

from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE
from src.job.stocks.stocks_digest_message_sender import StocksDigestMessageSender
from src.type.market_data_type import MarketDataType
from src.util.my_telegram import format_messages_to_telegram


class TestStocksDigestMessageSender:
    @pytest.mark.asyncio
    async def test_format_message_returns_empty_when_tradingview_anchor_missing(self):
        sender = StocksDigestMessageSender()
        sender.tradingview_message_sender.format_message = AsyncMock(return_value=[])
        sender.vix_central_message_sender.format_message = AsyncMock()
        sender.sentiment_message_sender.format_message = AsyncMock()

        result = await sender.format_message()

        assert result == []
        sender.vix_central_message_sender.format_message.assert_not_awaited()
        sender.sentiment_message_sender.format_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_format_message_returns_empty_when_tradingview_anchor_is_stale(self):
        sender = StocksDigestMessageSender()
        sender.tradingview_message_sender.format_message = AsyncMock(return_value=[])
        sender.vix_central_message_sender.format_message = AsyncMock()
        sender.sentiment_message_sender.format_message = AsyncMock()

        result = await sender.format_message()

        assert result == []
        sender.vix_central_message_sender.format_message.assert_not_awaited()
        sender.sentiment_message_sender.format_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_format_message_keeps_digest_when_vix_fails(
        self,
        monkeypatch,
    ):
        sender = StocksDigestMessageSender()
        sender.tradingview_message_sender.format_message = AsyncMock(
            return_value=['tradingview section']
        )
        sender.vix_central_message_sender.format_message = AsyncMock(
            side_effect=RuntimeError('vix failed')
        )
        sender.sentiment_message_sender.format_message = AsyncMock(
            return_value=['sentiment header', 'sentiment body']
        )

        send_message_to_admin = AsyncMock()
        monkeypatch.setattr(
            'src.job.stocks.stocks_digest_message_sender.send_message_to_admin',
            send_message_to_admin,
        )
        monkeypatch.setattr(
            'src.job.stocks.stocks_digest_message_sender.config.get_should_send_stocks_sentiment_message',
            lambda: True,
        )

        result = await sender.format_message()

        assert result == [
            'tradingview section',
            format_messages_to_telegram(['sentiment header', 'sentiment body']),
        ]
        # Through the admin sender, so `DISABLE_TELEGRAM` cannot silence a
        # partial-failure alert. The channel is resolved inside that function,
        # so there is no chat_id to assert here.
        send_message_to_admin.assert_awaited_once()
        assert (
            send_message_to_admin.await_args.kwargs['market_data_type']
            is MarketDataType.STOCKS
        )

    @pytest.mark.asyncio
    async def test_format_message_keeps_digest_when_sentiment_fails(
        self,
        monkeypatch,
    ):
        sender = StocksDigestMessageSender()
        sender.tradingview_message_sender.format_message = AsyncMock(
            return_value=['tradingview section']
        )
        sender.vix_central_message_sender.format_message = AsyncMock(
            return_value=['vix section']
        )
        sender.sentiment_message_sender.format_message = AsyncMock(
            side_effect=RuntimeError('sentiment failed')
        )

        send_message_to_admin = AsyncMock()
        monkeypatch.setattr(
            'src.job.stocks.stocks_digest_message_sender.send_message_to_admin',
            send_message_to_admin,
        )
        monkeypatch.setattr(
            'src.job.stocks.stocks_digest_message_sender.config.get_should_send_stocks_sentiment_message',
            lambda: True,
        )

        result = await sender.format_message()

        assert result == ['tradingview section', 'vix section']
        # Through the admin sender, so `DISABLE_TELEGRAM` cannot silence a
        # partial-failure alert. The channel is resolved inside that function,
        # so there is no chat_id to assert here.
        send_message_to_admin.assert_awaited_once()
        assert (
            send_message_to_admin.await_args.kwargs['market_data_type']
            is MarketDataType.STOCKS
        )

    @pytest.mark.asyncio
    async def test_a_failing_admin_alert_does_not_cost_the_digest(
        self,
        monkeypatch,
    ):
        """The alert must not become a second way to lose the whole digest.

        `send_message_to_admin` re-raises a delivery failure, unlike the
        `send_message_to_channel` this site used until 2026-09-06. Unguarded,
        that raise leaves `_load_supporting_messages`, whose entire contract is
        that a failing section still leaves a digest to send.
        """
        sender = StocksDigestMessageSender()
        sender.tradingview_message_sender.format_message = AsyncMock(
            return_value=['tradingview section']
        )
        sender.vix_central_message_sender.format_message = AsyncMock(
            return_value=['vix section']
        )
        sender.sentiment_message_sender.format_message = AsyncMock(
            side_effect=RuntimeError('sentiment failed')
        )

        monkeypatch.setattr(
            'src.job.stocks.stocks_digest_message_sender.send_message_to_admin',
            AsyncMock(side_effect=RuntimeError('telegram is down')),
        )
        monkeypatch.setattr(
            'src.job.stocks.stocks_digest_message_sender.config.get_should_send_stocks_sentiment_message',
            lambda: True,
        )

        result = await sender.format_message()

        assert result == ['tradingview section', 'vix section']

    @pytest.mark.asyncio
    async def test_start_sends_digest_messages_separately(
        self,
        monkeypatch,
    ):
        sender = StocksDigestMessageSender()
        sender.format_message = AsyncMock(return_value=['message one', 'message two'])

        send_message_to_channel = AsyncMock(
            side_effect=['response one', 'response two']
        )
        monkeypatch.setattr(
            'src.job.stocks.stocks_digest_message_sender.send_message_to_channel',
            send_message_to_channel,
        )
        monkeypatch.setattr(
            'src.job.stocks.stocks_digest_message_sender.market_data_type_to_chat_id',
            {MarketDataType.STOCKS: 'stocks-chat'},
        )

        result = await sender.start()

        assert result == ['response one', 'response two']
        assert send_message_to_channel.await_count == 2
        assert send_message_to_channel.await_args_list[0].kwargs == {
            'message': 'message one',
            'chat_id': 'stocks-chat',
            'market_data_type': MarketDataType.STOCKS,
            'runtime_mode': DEFAULT_RUNTIME_MODE,
        }
        assert send_message_to_channel.await_args_list[1].kwargs == {
            'message': 'message two',
            'chat_id': 'stocks-chat',
            'market_data_type': MarketDataType.STOCKS,
            'runtime_mode': DEFAULT_RUNTIME_MODE,
        }
