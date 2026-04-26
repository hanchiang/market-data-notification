import asyncio
import datetime
import json
import logging
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
    async def test_tradingview_daily_stocks_data_logs_metadata_only(
        self,
        monkeypatch,
        caplog,
    ):
        fixed_now = datetime.datetime(2026, 3, 26, 9, 0, tzinfo=datetime.timezone.utc)
        tradingview_service = SimpleNamespace(
            get_redis_key_for_stocks=Mock(return_value='tradingview-stocks'),
            save_tradingview_data=AsyncMock(return_value=[1, 0]),
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
        caplog.set_level(logging.INFO, logger='Trading view')

        request = DummyRequest(
            r'{\"type\": \"stocks\", \"secret\": \"secret\", \"test_mode\": \"false\", \"unix_ms\": 1, \"data\": [{\"symbol\": \"SPY\", \"timeframe\": \"1D\", \"close_prices\": [1, 2], \"nested\": {\"secret\": \"nested-secret\"}}], \"unexpected\": \"do-not-log\"}'
        )

        response = await tradingview.tradingview_daily_stocks_data(request)

        assert response == {'data': {'num_added': 1, 'num_removed': 0}}
        assert 'TradingView webhook payload metadata' in caplog.text
        assert 'data_count=1' in caplog.text
        assert 'close_prices' not in caplog.text
        assert 'nested-secret' not in caplog.text
        assert 'do-not-log' not in caplog.text

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
        assert 'Data item count' in emitted.call_args.kwargs['message']

    @pytest.mark.asyncio
    async def test_tradingview_daily_stocks_data_alerts_bounded_context_without_raw_secret_or_headers(
        self,
        monkeypatch,
    ):
        emitted = Mock()

        monkeypatch.setattr(tradingview.async_ee, 'emit', emitted)
        monkeypatch.setattr(tradingview.config, 'get_tradingview_webhook_secret', lambda: 'expected-secret')
        monkeypatch.setattr(tradingview.config, 'get_telegram_stocks_admin_id', lambda: 'admin-chat')

        request = DummyRequest(
            r'{\"type\": \"stocks\", \"secret\": \"wrong-secret\", \"test_mode\": \"false\", \"unix_ms\": 1774468860948, \"data\": [{\"symbol\": \"SPY\", \"timeframe\": \"1D\", \"close_prices\": [1, 2], \"ema20s\": [1, 2], \"volumes\": [10, 20]}], \"unexpected\": \"do-not-alert\"}',
            headers={'X-Api-Auth': 'private-token'},
        )

        response = await tradingview.tradingview_daily_stocks_data(request)

        assert response == {'data': 'OK'}
        message = emitted.call_args.kwargs['message']
        assert 'Incorrect tradingview webhook secret' in message
        assert 'Payload type' in message
        assert 'Data item count' in message
        assert 'SPY 1D' in message
        assert 'Payload preview' in message
        assert 'close\\_prices' in message
        assert 'private-token' not in message
        assert 'wrong-secret' not in message
        assert 'do-not-alert' not in message
        assert 'Headers' not in message
        assert 'Body' not in message

    @pytest.mark.asyncio
    async def test_tradingview_daily_stocks_data_alerts_bounded_context_for_bad_ip(
        self,
        monkeypatch,
    ):
        emitted = Mock()

        monkeypatch.setattr(tradingview.async_ee, 'emit', emitted)
        monkeypatch.setattr(tradingview.config, 'get_tradingview_webhook_secret', lambda: 'expected-secret')
        monkeypatch.setattr(tradingview.config, 'get_simulate_tradingview_traffic', lambda: False)
        monkeypatch.setattr(tradingview.config, 'get_trading_view_ips', lambda: ['192.0.2.10'])
        monkeypatch.setattr(tradingview.config, 'get_whitelist_ips', lambda: [])
        monkeypatch.setattr(tradingview.config, 'get_telegram_stocks_admin_id', lambda: 'admin-chat')

        request = DummyRequest(
            r'{\"type\": \"stocks\", \"secret\": \"expected-secret\", \"test_mode\": \"false\", \"unix_ms\": 1774468860948, \"data\": [{\"symbol\": \"QQQ\", \"timeframe\": \"1D\", \"close_prices\": [1, 2], \"ema20s\": [1, 2], \"volumes\": [10, 20]}]}',
            host='203.0.113.10',
            headers={'X-Api-Auth': 'private-token'},
        )

        response = await tradingview.tradingview_daily_stocks_data(request)

        assert response == {'data': 'OK'}
        message = emitted.call_args.kwargs['message']
        assert 'not from a configured TradingView source' in message
        assert 'Payload type' in message
        assert 'Data item count' in message
        assert 'QQQ 1D' in message
        assert 'Payload preview' in message
        assert 'close\\_prices' in message
        assert 'private-token' not in message
        assert 'expected-secret' not in message
        assert 'Headers' not in message
        assert 'Body' not in message

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

    def test_format_tradingview_alert_context_includes_ticker_preview_only(self):
        context = tradingview.format_tradingview_alert_context(
            {
                'type': 'stocks',
                'test_mode': 'false',
                'unix_ms': 1774468860948,
                'unexpected': 'do-not-alert',
                'data': [
                    {
                        'symbol': 'SPY',
                        'timeframe': '1D',
                        'close_prices': [100, 101],
                        'ema20s': [99, 100],
                        'volumes': [1, 2],
                    },
                    {
                        'symbol': 'QQQ',
                        'timeframe': '1D',
                        'close_prices': [200, 201],
                    },
                ],
            }
        )

        assert 'stocks' in context
        assert '1774468860948' in context
        assert 'Data item count' in context
        assert 'SPY 1D, QQQ 1D' in context
        assert 'Payload preview' in context
        assert 'close\\_prices' in context
        assert 'ema20s' in context
        assert 'volumes' in context
        assert 'do-not-alert' not in context

    def test_format_tradingview_alert_context_caps_oversized_sample_values(self):
        long_symbol = 'S' * 1000
        long_timeframe = 'T' * 1000
        long_price = '9' * 1000

        context = tradingview.format_tradingview_alert_context(
            {
                'type': 'stocks',
                'test_mode': 'false',
                'unix_ms': 1774468860948,
                'data': [
                    {
                        'symbol': long_symbol,
                        'timeframe': long_timeframe,
                        'close_prices': [long_price] * 100,
                        'ema20s': [long_price] * 100,
                        'volumes': [long_price] * 100,
                    },
                ],
            }
        )

        assert long_symbol not in context
        assert long_timeframe not in context
        assert long_price not in context
        assert 'S' * 29 in context
        assert 'T' * 29 in context
        assert len(context) < 1200
