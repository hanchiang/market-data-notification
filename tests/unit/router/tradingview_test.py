import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.router.tradingview import tradingview
from src.type.trading_view import TradingViewDataType


class DummyRequest:
    def __init__(self, body: str, host: str = '127.0.0.1', headers=None):
        self._body = body.encode('utf-8')
        self.client = SimpleNamespace(host=host)
        self.headers = headers or {}

    async def body(self):
        return self._body


class TestTradingViewRouter:
    @pytest.mark.asyncio
    async def test_parse_tradingview_request_body_accepts_shell_escaped_json(self):
        request = DummyRequest(r'{\"type\": \"stocks\", \"unix_ms\": 1, \"data\": []}')

        body = await tradingview.parse_tradingview_request_body(request)

        assert body == {'type': 'stocks', 'unix_ms': 1, 'data': []}

    @pytest.mark.asyncio
    async def test_parse_tradingview_request_body_rejects_empty_body(self):
        request = DummyRequest('')

        with pytest.raises(ValueError, match='empty'):
            await tradingview.parse_tradingview_request_body(request)

    @pytest.mark.asyncio
    async def test_tradingview_daily_stocks_data_saves_escaped_json_payload(self, monkeypatch):
        fixed_now = datetime.datetime(2026, 3, 26, 9, 0, tzinfo=datetime.timezone.utc)
        tradingview_service = SimpleNamespace(
            get_redis_key_for_stocks=Mock(return_value='tradingview-stocks'),
            save_tradingview_data=AsyncMock(return_value=[1, 0]),
        )
        emitted = Mock()

        def discard_task(coro):
            coro.close()
            return None

        monkeypatch.setattr(tradingview, 'get_current_date', lambda: fixed_now)
        monkeypatch.setattr(tradingview.Dependencies, 'get_tradingview_service', lambda: tradingview_service)
        monkeypatch.setattr(tradingview.async_ee, 'emit', emitted)
        monkeypatch.setattr(tradingview.asyncio, 'create_task', discard_task)
        monkeypatch.setattr(tradingview.config, 'get_is_testing_telegram', lambda: False)
        monkeypatch.setattr(tradingview.config, 'set_is_testing_telegram', lambda _: None)
        monkeypatch.setattr(tradingview.config, 'get_tradingview_webhook_secret', lambda: 'secret')
        monkeypatch.setattr(tradingview.config, 'get_simulate_tradingview_traffic', lambda: True)
        monkeypatch.setattr(tradingview.config, 'get_trading_view_ips', lambda: [])
        monkeypatch.setattr(tradingview.config, 'get_whitelist_ips', lambda: [])
        monkeypatch.setattr(tradingview.config, 'get_trading_view_days_to_store', lambda: 30)
        monkeypatch.setattr(tradingview.config, 'get_telegram_stocks_admin_id', lambda: 'admin-chat')

        request = DummyRequest(
            r'{\"type\": \"stocks\", \"secret\": \"secret\", \"test_mode\": \"false\", \"unix_ms\": 1, \"data\": []}'
        )

        response = await tradingview.tradingview_daily_stocks_data(request)

        assert response == {'data': {'num_added': 1, 'num_removed': 0}}
        tradingview_service.get_redis_key_for_stocks.assert_called_once_with(type=TradingViewDataType.STOCKS)
        saved_payload = json.loads(tradingview_service.save_tradingview_data.await_args.kwargs['data'])
        assert saved_payload == {'type': 'stocks', 'test_mode': 'false', 'unix_ms': 1, 'data': []}
        emitted.assert_called_once()
