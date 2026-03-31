from src.job.stocks import stocks_digest_formatter
from src.util.my_telegram import format_messages_to_telegram


class TestStocksDigestFormatter:
    def test_build_digest_messages_prefers_two_message_layout_for_supporting_context(
        self,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            stocks_digest_formatter.telegram_notification,
            'MAX_TELEGRAM_MESSAGE_LENGTH',
            4096,
        )

        digest_messages = stocks_digest_formatter.build_digest_messages(
            tradingview_messages=['tradingview section'],
            vix_messages=['vix section'],
            sentiment_messages=['sentiment header', 'sentiment body'],
        )

        assert digest_messages == [
            'tradingview section',
            format_messages_to_telegram(
                ['vix section', format_messages_to_telegram(['sentiment header', 'sentiment body'])]
            ),
        ]

    def test_build_digest_messages_caps_output_to_two_chunks(self, monkeypatch) -> None:
        monkeypatch.setattr(
            stocks_digest_formatter.telegram_notification,
            'MAX_TELEGRAM_MESSAGE_LENGTH',
            4096,
        )

        tradingview_messages = ['T' * 3500]
        vix_messages = ['V' * 3500]
        sentiment_messages = [
            '*Stocks fear greed index*: 2026-03-31',
            'Sentiment:\n'
            + '\n'.join(['now: fear (10)' for _ in range(40)])
            + '\n\nAverage:\n'
            + '\n'.join(['timeframe: 1w: neutral (50)' for _ in range(40)]),
        ]

        digest_messages = stocks_digest_formatter.build_digest_messages(
            tradingview_messages=tradingview_messages,
            vix_messages=vix_messages,
            sentiment_messages=sentiment_messages,
        )

        assert len(digest_messages) <= 2
        assert all(
            len(
                stocks_digest_formatter.telegram_notification._split_message_for_telegram(
                    message
                )
            )
            == 1
            for message in digest_messages
        )
        assert digest_messages[0] == tradingview_messages[0]
        assert any('Stocks fear greed index' in message for message in digest_messages)

    def test_build_digest_messages_falls_back_to_truncated_anchor(self, monkeypatch) -> None:
        monkeypatch.setattr(
            stocks_digest_formatter.telegram_notification,
            'MAX_TELEGRAM_MESSAGE_LENGTH',
            50,
        )

        digest_messages = stocks_digest_formatter.build_digest_messages(
            tradingview_messages=['T' * 120],
            vix_messages=['V' * 120],
            sentiment_messages=['S' * 120],
        )

        assert len(digest_messages) == 1
        assert (
            len(
                stocks_digest_formatter.telegram_notification._split_message_for_telegram(
                    digest_messages[0]
                )
            )
            == 1
        )
        assert digest_messages[0].endswith(
            stocks_digest_formatter.TRUNCATION_NOTICE
        )
