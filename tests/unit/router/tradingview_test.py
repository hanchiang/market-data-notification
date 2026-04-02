import asyncio
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
    async def test_parse_tradingview_request_body_rejects_form_wrapped_payload(self):
        request = DummyRequest('message={"type":"stocks","unix_ms":1,"data":[]}')

        with pytest.raises(json.JSONDecodeError):
            await tradingview.parse_tradingview_request_body(request)

    @pytest.mark.asyncio
    async def test_tradingview_daily_stocks_data_saves_escaped_json_payload(self, monkeypatch):
        fixed_now = datetime.datetime(2026, 3, 26, 9, 0, tzinfo=datetime.timezone.utc)
        tradingview_service = SimpleNamespace(
            get_redis_key_for_stocks=Mock(return_value='tradingview-stocks'),
            save_tradingview_data=AsyncMock(return_value=[1, 0]),
        )
        emitted = Mock()

        monkeypatch.setattr(tradingview, 'get_current_date', lambda: fixed_now)
        monkeypatch.setattr(tradingview.Dependencies, 'get_tradingview_service', lambda: tradingview_service)
        monkeypatch.setattr(tradingview.async_ee, 'emit', emitted)
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
        assert tradingview_service.save_tradingview_data.await_args.kwargs['score'] == 1
        emitted.assert_called_once()

    @pytest.mark.asyncio
    async def test_tradingview_daily_stocks_data_uses_canonical_key_in_test_mode(self, monkeypatch):
        fixed_now = datetime.datetime(2026, 3, 26, 9, 0, tzinfo=datetime.timezone.utc)
        tradingview_service = SimpleNamespace(
            get_redis_key_for_stocks=Mock(return_value='tradingview-stocks'),
            save_tradingview_data=AsyncMock(return_value=[1, 0]),
        )

        monkeypatch.setattr(tradingview, 'get_current_date', lambda: fixed_now)
        monkeypatch.setattr(tradingview.Dependencies, 'get_tradingview_service', lambda: tradingview_service)
        monkeypatch.setattr(tradingview.async_ee, 'emit', Mock())
        monkeypatch.setattr(tradingview.config, 'get_tradingview_webhook_secret', lambda: 'secret')
        monkeypatch.setattr(tradingview.config, 'get_simulate_tradingview_traffic', lambda: True)
        monkeypatch.setattr(tradingview.config, 'get_trading_view_ips', lambda: [])
        monkeypatch.setattr(tradingview.config, 'get_whitelist_ips', lambda: [])
        monkeypatch.setattr(tradingview.config, 'get_trading_view_days_to_store', lambda: 30)
        monkeypatch.setattr(tradingview.config, 'get_telegram_stocks_admin_id', lambda: 'admin-chat')

        request = DummyRequest(
            r'{\"type\": \"stocks\", \"secret\": \"secret\", \"test_mode\": \"true\", \"unix_ms\": 1, \"data\": []}'
        )

        response = await tradingview.tradingview_daily_stocks_data(request)

        assert response == {'data': {'num_added': 1, 'num_removed': 0}}
        tradingview_service.get_redis_key_for_stocks.assert_called_once_with(type=TradingViewDataType.STOCKS)
        assert tradingview_service.save_tradingview_data.await_args.kwargs['key'] == 'tradingview-stocks'
        assert tradingview_service.save_tradingview_data.await_args.kwargs['test_mode'] is True

    @pytest.mark.asyncio
    async def test_tradingview_daily_stocks_data_ignores_test_mode_request_in_prod(self, monkeypatch):
        tradingview_service = SimpleNamespace(
            get_redis_key_for_stocks=Mock(return_value='tradingview-stocks'),
            save_tradingview_data=AsyncMock(return_value=[1, 0]),
        )
        emitted = Mock()

        monkeypatch.setattr(tradingview.Dependencies, 'get_tradingview_service', lambda: tradingview_service)
        monkeypatch.setattr(tradingview.async_ee, 'emit', emitted)
        monkeypatch.setattr(tradingview.config, 'get_env', lambda: 'prod')
        monkeypatch.setattr(tradingview.config, 'get_telegram_stocks_admin_id', lambda: 'admin-chat')

        request = DummyRequest(
            r'{\"type\": \"stocks\", \"secret\": \"secret\", \"test_mode\": \"true\", \"unix_ms\": 1774468860948, \"data\": []}'
        )

        response = await tradingview.tradingview_daily_stocks_data(request)

        assert response == {'data': None}
        tradingview_service.save_tradingview_data.assert_not_awaited()
        emitted.assert_called_once()

    @pytest.mark.asyncio
    async def test_tradingview_daily_stocks_data_keeps_test_mode_request_local_under_concurrency(
        self,
        monkeypatch,
    ):
        fixed_now = datetime.datetime(2026, 3, 26, 9, 0, tzinfo=datetime.timezone.utc)
        saved_test_modes = []

        async def save_tradingview_data(**kwargs):
            await asyncio.sleep(0)
            saved_test_modes.append(kwargs['test_mode'])
            return [1, 0]

        tradingview_service = SimpleNamespace(
            get_redis_key_for_stocks=Mock(return_value='tradingview-stocks'),
            save_tradingview_data=AsyncMock(side_effect=save_tradingview_data),
        )

        monkeypatch.setattr(tradingview, 'get_current_date', lambda: fixed_now)
        monkeypatch.setattr(
            tradingview.Dependencies,
            'get_tradingview_service',
            lambda: tradingview_service,
        )
        monkeypatch.setattr(tradingview.async_ee, 'emit', Mock())
        monkeypatch.setattr(
            tradingview.config,
            'get_tradingview_webhook_secret',
            lambda: 'secret',
        )
        monkeypatch.setattr(
            tradingview.config,
            'get_simulate_tradingview_traffic',
            lambda: True,
        )
        monkeypatch.setattr(tradingview.config, 'get_trading_view_ips', lambda: [])
        monkeypatch.setattr(tradingview.config, 'get_whitelist_ips', lambda: [])
        monkeypatch.setattr(
            tradingview.config,
            'get_trading_view_days_to_store',
            lambda: 30,
        )
        monkeypatch.setattr(
            tradingview.config,
            'get_telegram_stocks_admin_id',
            lambda: 'admin-chat',
        )

        prod_request = DummyRequest(
            r'{\"type\": \"stocks\", \"secret\": \"secret\", \"test_mode\": \"false\", \"unix_ms\": 1, \"data\": []}'
        )
        test_request = DummyRequest(
            r'{\"type\": \"stocks\", \"secret\": \"secret\", \"test_mode\": \"true\", \"unix_ms\": 2, \"data\": []}'
        )

        responses = await asyncio.gather(
            tradingview.tradingview_daily_stocks_data(prod_request),
            tradingview.tradingview_daily_stocks_data(test_request),
        )

        assert responses == [
            {'data': {'num_added': 1, 'num_removed': 0}},
            {'data': {'num_added': 1, 'num_removed': 0}},
        ]
        assert sorted(saved_test_modes) == [False, True]

    @pytest.mark.parametrize(
        'unix_ms, expected',
        [
            (1774468860948, 1774468860),
            (1774468860, 1774468860),
            ('1774468860948', 1774468860),
            (None, None),
        ],
    )
    def test_get_tradingview_score(self, unix_ms, expected):
        fallback = datetime.datetime(2026, 3, 25, 9, 0, tzinfo=datetime.timezone.utc)

        result = tradingview.get_tradingview_score({'unix_ms': unix_ms}, fallback=fallback)

        expected_score = int(fallback.timestamp()) if expected is None else expected

        assert result == expected_score
