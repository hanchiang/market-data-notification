import asyncio
import datetime
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.event import event_emitter
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
        # The event name is the transport. `send_to_telegram` carries an admin
        # chat id into `send_message_to_channel`, which `DISABLE_TELEGRAM` mutes
        # -- a deployed secret, so a webhook probe would raise no alert in
        # production. `send_alert_to_telegram` cannot address a chat at all.
        assert emitted.call_args.args[0] == 'send_alert_to_telegram'
        assert 'channel' not in emitted.call_args.kwargs
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
    async def test_a_failed_redis_save_alerts_and_still_returns_an_error(
        self,
        monkeypatch,
    ):
        """A lost webhook payload must not be quieter than a saved one.

        The webhook is fire-and-forget, so a Redis failure loses the payload for
        good, and until 2026-09-06 it produced a 500 and a log line on a box
        nobody watches. The raise is kept: external monitoring reads the status
        code, and the alert is for the operator.
        """
        emitted = Mock()

        class _FailingService:
            def get_redis_key_for_stocks(self, type):
                return 'tradingview-stocks'

            async def save_tradingview_data(self, **kwargs):
                raise ConnectionError('redis is unreachable')

        monkeypatch.setattr(tradingview.async_ee, 'emit', emitted)
        monkeypatch.setattr(
            tradingview.Dependencies, 'get_tradingview_service', lambda: _FailingService()
        )
        monkeypatch.setattr(
            tradingview.config, 'get_tradingview_webhook_secret', lambda: 'expected-secret'
        )
        monkeypatch.setattr(
            tradingview.config, 'get_simulate_tradingview_traffic', lambda: True
        )

        request = DummyRequest(
            r'{\"type\": \"stocks\", \"secret\": \"expected-secret\", \"test_mode\": \"false\", \"unix_ms\": 1774468860948, \"data\": [{\"symbol\": \"QQQ\", \"timeframe\": \"1D\", \"close_prices\": [1, 2], \"ema20s\": [1, 2], \"volumes\": [10, 20]}]}',
            host='203.0.113.10',
            headers={'X-Api-Auth': 'private-token'},
        )

        with pytest.raises(ConnectionError):
            await tradingview.tradingview_daily_stocks_data(request)

        assert emitted.call_args.args[0] == 'send_alert_to_telegram'
        message = emitted.call_args.kwargs['message']
        assert 'Failed to save TradingView data' in message
        assert 'redis is unreachable' in message
        # The class name reaches the operator only through the traceback, so
        # this also pins that the traceback is included. Stripping tracebacks
        # from admin alerts would redden it, deliberately.
        assert 'ConnectionError' in message
        # The context rides along so the operator knows which payload was lost,
        # under the same redaction as the warning alerts. `filtered_body` drops
        # the secret; swapping it for `body` reddens the next line.
        assert 'QQQ 1D' in message
        assert 'expected-secret' not in message

    @pytest.mark.asyncio
    async def test_an_unrecognised_payload_type_alerts_like_a_dead_redis(
        self,
        monkeypatch,
    ):
        """`type` is free text in a hand-edited TradingView alert template.

        A typo raises out of `TradingViewDataType(...)` before any Redis call,
        and it loses the payload exactly as a dead Redis does -- so it must not
        be the one dependency-shaped failure that stays silent. The guard covers
        the whole span for this reason, not the save alone.
        """
        emitted = Mock()
        tradingview_service = SimpleNamespace(
            get_redis_key_for_stocks=Mock(return_value='tradingview-stocks'),
            save_tradingview_data=AsyncMock(return_value=[1, 0]),
        )

        monkeypatch.setattr(tradingview.async_ee, 'emit', emitted)
        monkeypatch.setattr(
            tradingview.Dependencies, 'get_tradingview_service', lambda: tradingview_service
        )
        monkeypatch.setattr(
            tradingview.config, 'get_tradingview_webhook_secret', lambda: 'expected-secret'
        )
        monkeypatch.setattr(
            tradingview.config, 'get_simulate_tradingview_traffic', lambda: True
        )

        request = DummyRequest(
            r'{\"type\": \"stonks\", \"secret\": \"expected-secret\", \"test_mode\": \"false\", \"unix_ms\": 1774468860948, \"data\": [{\"symbol\": \"QQQ\", \"timeframe\": \"1D\"}]}',
            host='203.0.113.10',
        )

        with pytest.raises(ValueError):
            await tradingview.tradingview_daily_stocks_data(request)

        assert emitted.call_args.args[0] == 'send_alert_to_telegram'
        assert 'Failed to save TradingView data' in emitted.call_args.kwargs['message']

        # The other reachable fault in the same span: the service is None until
        # `Dependencies.build()` has run, so a startup-order failure raises
        # AttributeError at the key line and loses the payload the same way.
        # The resolution line itself is a bare attribute read that cannot raise,
        # so nothing can pin its inclusion -- the key line is the real edge, and
        # moving it above the `try` reddens the coercion test above.
        emitted.reset_mock()
        monkeypatch.setattr(
            tradingview.Dependencies, 'get_tradingview_service', lambda: None
        )

        with pytest.raises(AttributeError):
            await tradingview.tradingview_daily_stocks_data(request)

        assert emitted.call_args.args[0] == 'send_alert_to_telegram'
        assert 'Failed to save TradingView data' in emitted.call_args.kwargs['message']

    @pytest.mark.asyncio
    async def test_a_router_emit_reaches_the_handler_and_the_admin_sender(
        self,
        monkeypatch,
    ):
        """The one test that joins the router to both handlers, unstubbed.

        Every other router test replaces `async_ee.emit` with a Mock and every
        handler test calls the handler function directly, so the keyword names
        on the two sides were never compared. Renaming `message=` on either side
        passes both halves: the AST scan checks the event name and the forbidden
        chat-id spellings, nothing else. In production pyee turns the resulting
        TypeError into an `'error'` event, so the operator gets "event emitter
        error" where the actual alert should have been.

        `send_message_to_admin` is stubbed, which is what keeps this off the
        wire -- the emitter and the handler are both real.
        """
        delivered = []

        async def fake_admin_send(message, market_data_type, runtime_mode=None):
            delivered.append(message)
            return object()

        monkeypatch.setattr(
            event_emitter.telegram_notification, 'send_message_to_admin', fake_admin_send
        )
        monkeypatch.setattr(
            tradingview.config, 'get_tradingview_webhook_secret', lambda: 'expected-secret'
        )
        monkeypatch.setattr(
            tradingview.config, 'get_simulate_tradingview_traffic', lambda: True
        )
        monkeypatch.setattr(
            event_emitter.config, 'get_simulate_tradingview_traffic', lambda: True
        )

        # A body that fails to parse: the earliest alert emit in the handler,
        # reached before any dependency is touched.
        request = DummyRequest('not json at all', host='203.0.113.10')

        response = await tradingview.tradingview_daily_stocks_data(request)

        assert response == {'data': 'OK'}
        # `emit` schedules the handler with `ensure_future` and returns, so it
        # has not run yet. pyee keeps a strong reference in `_waiting`.
        assert event_emitter.async_ee._waiting, 'the emit scheduled nothing'
        await asyncio.gather(*list(event_emitter.async_ee._waiting))

        assert len(delivered) == 1, 'the router emit never reached the admin sender'
        assert 'JSON body error' in delivered[0]

        # The notice half of the same seam. It has its own handler with its own
        # signature, so pinning only the alert path would leave a renamed
        # keyword on a notice emit converting routine webhook chatter into an
        # admin `'error'` alert with the suite green.
        notices = []

        async def fake_channel_send(message, chat_id, market_data_type, runtime_mode=None):
            notices.append(message)
            return None

        monkeypatch.setattr(
            event_emitter.telegram_notification,
            'send_message_to_channel',
            fake_channel_send,
        )
        monkeypatch.setattr(
            event_emitter.telegram_notification,
            'get_admin_channel_id_from_market_data_type',
            lambda market_data_type: 'stocks-admin-chat',
        )
        monkeypatch.setattr(
            tradingview.Dependencies,
            'get_tradingview_service',
            lambda: SimpleNamespace(
                get_redis_key_for_stocks=Mock(return_value='tradingview-stocks'),
                save_tradingview_data=AsyncMock(return_value=[1, 0]),
            ),
        )
        monkeypatch.setattr(
            tradingview.config, 'get_trading_view_days_to_store', lambda: 30
        )

        saved = DummyRequest(
            r'{\"type\": \"stocks\", \"secret\": \"expected-secret\", \"test_mode\": \"false\", \"unix_ms\": 1, \"data\": []}',
            host='203.0.113.10',
        )

        await tradingview.tradingview_daily_stocks_data(saved)
        await asyncio.gather(*list(event_emitter.async_ee._waiting))

        assert len(notices) == 1, 'the notice emit never reached the channel sender'
        assert 'Successfully saved' in notices[0]

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
