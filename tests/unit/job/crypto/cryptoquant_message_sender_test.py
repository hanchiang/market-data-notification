from unittest.mock import AsyncMock

import pytest

from market_data_library.types import cryptoquant_type
from src.job.crypto.cryptoquant_message_sender import CryptoQuantMessageSender


class TestCryptoQuantMessageSender:
    @pytest.mark.asyncio
    async def test_format_message_returns_empty_without_token(self, monkeypatch):
        monkeypatch.delenv('CRYPTOQUANT_API_TOKEN', raising=False)
        sender = CryptoQuantMessageSender()

        res = await sender.format_message()

        assert res == []

    @pytest.mark.asyncio
    async def test_format_message(self, monkeypatch):
        monkeypatch.setenv('CRYPTOQUANT_API_TOKEN', 'test-token')

        sender = CryptoQuantMessageSender()
        sender_service = AsyncMock()
        sender_service.get_asset_metrics.return_value = cryptoquant_type.AssetMetrics(
            symbol='BTC',
            price_usd=65000.0,
            exchange_supply={
                'Total': {'usd': 6500000.0, 'quantity': 100.0},
                'binance': {'usd': 3900000.0, 'quantity': 60.0},
            },
            exchange_net_flows={
                'Total': {'usd': -130000.0, 'quantity': -2.0},
                'binance': {'usd': -195000.0, 'quantity': -3.0},
            },
        )

        monkeypatch.setattr('src.job.crypto.cryptoquant_message_sender.Dependencies.get_cryptoquant_service', lambda: sender_service)

        res = await sender.format_message()

        assert len(res) == 2
        assert 'Crypto market data at' in res[0]
        assert '*BTC price:' in res[1]
        assert 'Exchange net flows' in res[1]
        assert 'Exchange supply' in res[1]
