"""The event emitter's two transports: alerts and routine notices."""
import ast
import pathlib

import pytest

from src.event import event_emitter
from src.type.market_data_type import MarketDataType


def _stub_admin_sender(monkeypatch, sent):
    async def fake_send(message, market_data_type, runtime_mode=None):
        sent.append((message, market_data_type, runtime_mode))
        return object()

    monkeypatch.setattr(
        event_emitter.telegram_notification, 'send_message_to_admin', fake_send
    )


def _forbid_channel_sender(monkeypatch):
    def exploding(*args, **kwargs):
        raise AssertionError('an alert must never go through send_message_to_channel')

    monkeypatch.setattr(
        event_emitter.telegram_notification, 'send_message_to_channel', exploding
    )


@pytest.mark.asyncio
async def test_alert_event_goes_through_the_admin_sender(monkeypatch):
    """Webhook warnings must not be muted by `DISABLE_TELEGRAM`.

    The router's malicious-request warnings travel as events. They used to be
    `send_to_telegram` carrying an admin chat id, which `send_message_to_channel`
    delivers and `DISABLE_TELEGRAM` mutes -- a deployed secret, so muting public
    output silenced every intrusion warning.
    """
    sent = []
    _stub_admin_sender(monkeypatch, sent)
    _forbid_channel_sender(monkeypatch)
    monkeypatch.setattr(
        event_emitter.config, 'get_simulate_tradingview_traffic', lambda: False
    )
    monkeypatch.setenv('DISABLE_TELEGRAM', 'true')

    await event_emitter.send_alert_to_telegram_handler(
        message='incorrect webhook secret',
        market_data_type=MarketDataType.STOCKS,
    )

    assert len(sent) == 1
    message, market_data_type, runtime_mode = sent[0]
    assert message == 'incorrect webhook secret'
    assert market_data_type is MarketDataType.STOCKS
    assert runtime_mode.use_dev_telegram is False


@pytest.mark.asyncio
async def test_a_simulated_webhook_alert_redirects_to_the_dev_channel(monkeypatch):
    """Moving off `send_message_to_channel` must not drop its simulate redirect.

    That function redirects to the dev channel when SIMULATE_TRADINGVIEW_TRAFFIC
    is on, and this router is the surface that flag simulates. The admin sender
    has no such rule of its own -- deliberately, since a real job failure during
    a simulation is still real -- so the router supplies it here. Without this
    the simulation posts to the live stocks admin chat.
    """
    sent = []
    _stub_admin_sender(monkeypatch, sent)
    monkeypatch.setattr(
        event_emitter.config, 'get_simulate_tradingview_traffic', lambda: True
    )

    await event_emitter.send_alert_to_telegram_handler(
        message='simulated warning', market_data_type=MarketDataType.STOCKS
    )

    assert sent[0][2].use_dev_telegram is True


@pytest.mark.asyncio
async def test_a_routine_notice_stays_on_the_user_facing_flag(monkeypatch):
    """A webhook success notice is not an alarm.

    Routing it through the alert path would leave `DISABLE_TELEGRAM_ADMIN` as
    the only way to quiet it, which would silence the warnings too -- the
    coupling the flag split removed.
    """
    sent = []

    async def fake_channel_send(message, chat_id, market_data_type, runtime_mode=None):
        sent.append((message, chat_id))
        return None

    def exploding_admin(*args, **kwargs):
        raise AssertionError('a routine notice must not use the alert path')

    monkeypatch.setattr(
        event_emitter.telegram_notification, 'send_message_to_channel', fake_channel_send
    )
    monkeypatch.setattr(
        event_emitter.telegram_notification, 'send_message_to_admin', exploding_admin
    )
    monkeypatch.setattr(
        event_emitter.telegram_notification,
        'get_admin_channel_id_from_market_data_type',
        lambda market_data_type: 'stocks-admin-chat',
    )

    await event_emitter.send_notice_to_telegram_handler(
        message='saved', market_data_type=MarketDataType.STOCKS
    )

    assert sent == [('saved', 'stocks-admin-chat')]


@pytest.mark.asyncio
async def test_a_failing_alert_does_not_re_enter_the_error_handler(monkeypatch):
    """A raise here would feed itself.

    pyee turns a handler exception into an `'error'` event, and `on_error`
    alerts through the same sender -- so during a Telegram outage each failed
    alert would emit another. The handlers swallow and log instead.
    """
    calls = []

    async def exploding_send(message, market_data_type, runtime_mode=None):
        calls.append(message)
        raise RuntimeError('telegram is down')

    monkeypatch.setattr(
        event_emitter.telegram_notification, 'send_message_to_admin', exploding_send
    )
    monkeypatch.setattr(
        event_emitter.config, 'get_simulate_tradingview_traffic', lambda: False
    )

    await event_emitter.send_alert_to_telegram_handler(
        message='boom', market_data_type=MarketDataType.STOCKS
    )
    await event_emitter.on_error('boom')

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_positional_emit_raises_rather_than_vanishing(monkeypatch):
    """Keyword-only on purpose.

    A handler that accepted `*args` would swallow a positional emit as
    malformed, and a dropped alert is the failure this event exists to prevent.
    A TypeError becomes an `'error'` event, which alerts.
    """
    _forbid_channel_sender(monkeypatch)

    with pytest.raises(TypeError):
        await event_emitter.send_alert_to_telegram_handler('positional message')


def _event_name(node, source_tree):
    """The event a first emit argument names, whatever way it is spelled.

    `ALERT_EVENT` and `NOTICE_EVENT` are exported for exactly this use, so
    accepting only a string literal would fail the better spelling with a
    message claiming the emit is malformed.

    Resolution is by identifier text, which is a guess about a binding this scan
    cannot follow -- so the guess is fenced. Without the fence a local
    `ALERT_EVENT = 'typo'` would read as the real event, in the one test whose
    purpose is catching exactly that mismatch.

    The fence is deliberately partial, and what it does NOT cover is listed here
    rather than implied: `def`/`class` of that name, a function parameter, and a
    `match` capture all bind it and are not checked. Those are absurd in a
    router, unlike the shapes below, which are plausible typos.

    No live emit reaches this code -- all eight pass a string literal -- so it
    is exercised only by `test_the_event_name_fence_rejects_every_rebinding`.
    """
    if isinstance(node, ast.Constant):
        return node.value
    constants = {
        'ALERT_EVENT': event_emitter.ALERT_EVENT,
        'NOTICE_EVENT': event_emitter.NOTICE_EVENT,
    }
    if isinstance(node, ast.Attribute):
        name = node.attr
        # The qualifier decides the value, so an unresolvable one is refused
        # rather than assumed: `config.ALERT_EVENT` names an attribute of
        # another module entirely, and reading it as this module's constant is
        # the mismatch the fence exists to catch.
        qualifier = node.value.id if isinstance(node.value, ast.Name) else None
        assert qualifier is not None, (
            f'the qualifier of {name} must be a plain module name, '
            f'got {ast.dump(node.value)}'
        )
    else:
        name = getattr(node, 'id', None)
        qualifier = None
    assert name in constants, (
        'event name must be a string literal or one of the emitter constants, '
        f'got {ast.dump(node)}'
    )
    # A store-context Name covers assignment, tuple unpacking, an annotated
    # assignment, a `for` target, `with ... as` and a walrus. Two more bindings
    # are not Name nodes and are collected separately: `except ... as X` keeps
    # its name as a plain string, and both import forms use `ast.alias`. An
    # import is not automatically legal -- importing this name from the WRONG
    # module is exactly the mismatch the fence exists to catch.
    rebound = {
        n.id
        for n in ast.walk(source_tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    }
    rebound |= {
        n.name
        for n in ast.walk(source_tree)
        if isinstance(n, ast.ExceptHandler) and n.name
    }
    for n in ast.walk(source_tree):
        if not isinstance(n, (ast.Import, ast.ImportFrom)):
            continue
        # The one legal import: this name, unrenamed, out of the emitter itself.
        # Anything else -- another module, or an alias -- rebinds the identifier
        # to a value this scan cannot see.
        package, _, module_name = event_emitter.__name__.rpartition('.')
        for alias in n.names:
            if alias.asname is None and (
                # `from src.event.event_emitter import ALERT_EVENT`
                (isinstance(n, ast.ImportFrom) and n.module == event_emitter.__name__)
                # `from src.event import event_emitter`
                or (
                    isinstance(n, ast.ImportFrom)
                    and n.module == package
                    and alias.name == module_name
                )
            ):
                continue
            rebound.add(alias.asname or alias.name)
    for bound in (name, qualifier):
        assert bound is None or bound not in rebound, (
            f'{bound} is rebound in the scanned module, so this scan cannot '
            'tell which value the emit uses'
        )
    return constants[name]


def test_every_router_emit_names_a_registered_event():
    """A producer/consumer name mismatch now DELETES a message.

    pyee returns False for an unhandled non-`error` event and every emit here
    ignores the return, so a renamed handler or a typo drops the message with
    no exception and no log. Before the split the same mistake would merely have
    muted it. A boundary check rather than one assertion per emit site, so a new
    emit in THIS file is covered the day it is written -- the scan names one
    router, and a second router would need its own.
    """
    path = pathlib.Path(__file__).parents[3] / 'src/router/tradingview/tradingview.py'
    emits = []
    source_tree = ast.parse(path.read_text())
    for node in ast.walk(source_tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == 'emit'):
            continue
        assert node.args, 'emit called with no event name'
        emits.append((node.lineno, _event_name(node.args[0], source_tree)))
        # No emit may carry a chat id under any spelling: the destination
        # belongs to the handler, so a router cannot address the muted
        # transport again. `channel=` is the spelling the deleted
        # `send_to_telegram` used; the others are what someone would reach for
        # next. A handler would raise TypeError on any of them, which alerts
        # rather than drops -- this catches it at the point it is written.
        forbidden = {'channel', 'chat_id', 'chat', 'channel_id', 'destination'}
        assert not forbidden & {kw.arg for kw in node.keywords}

    assert emits, 'no emits found -- the scan is looking at the wrong file'
    for _, name in emits:
        assert event_emitter.async_ee.listeners(
            name
        ), f'no handler registered for {name!r}'

    # The intent of each site, in file order. Registration alone does not pin
    # this: both names are registered, so flipping a malicious-request warning
    # to a notice would put it back on the DISABLE_TELEGRAM-muted transport with
    # the whole suite green. Ordered rather than keyed by line so an unrelated
    # edit above an emit does not redden it; adding, removing or reclassifying
    # one is a deliberate edit here.
    alert, notice = event_emitter.ALERT_EVENT, event_emitter.NOTICE_EVENT
    assert [name for _, name in sorted(emits)] == [
        alert,  # unparseable JSON body
        alert,  # test_mode request rejected in prod
        alert,  # incorrect webhook secret
        alert,  # request ip outside the TradingView and whitelist ranges
        alert,  # the save block failed: dead redis, bad `type`, unbuilt deps
        notice,  # already saved for this score -- idempotency chatter
        alert,  # 0 elements added: redis needs a look
        notice,  # saved successfully
    ]


@pytest.mark.asyncio
async def test_a_notice_that_cannot_be_delivered_alerts_the_admin(monkeypatch):
    """A failed notice is itself a production error, so it must be visible.

    The handler this replaced alerted on the same failure; dropping that would
    have traded one silent path for another.

    Note what this does NOT buy: a fault in the shared chat-id lookup or client
    map takes the alert down with the notice, since `send_message_to_admin`
    resolves the same id from the same map. The reachable triggers are the ones
    downstream of that -- the `print_telegram_message` loop raising, or the
    split-send re-raise for a non-admin chat.
    """
    alerts = []
    _stub_admin_sender(monkeypatch, alerts)
    monkeypatch.setattr(
        event_emitter.config, 'get_simulate_tradingview_traffic', lambda: False
    )

    async def exploding_channel_send(*args, **kwargs):
        raise KeyError('stocks')

    monkeypatch.setattr(
        event_emitter.telegram_notification,
        'send_message_to_channel',
        exploding_channel_send,
    )
    monkeypatch.setattr(
        event_emitter.telegram_notification,
        'get_admin_channel_id_from_market_data_type',
        lambda market_data_type: 'stocks-admin-chat',
    )

    await event_emitter.send_notice_to_telegram_handler(
        message='saved', market_data_type=MarketDataType.STOCKS
    )

    assert len(alerts) == 1
    assert 'notice could not be delivered' in alerts[0][0]


@pytest.mark.asyncio
async def test_the_error_handler_redirects_on_a_simulated_run(monkeypatch):
    """`on_error` fires on a router-caused fault, so it takes the same redirect.

    A malformed emit or a handler bug during a SIMULATE_TRADINGVIEW_TRAFFIC run
    reaches here by design. Without the runtime mode it resolves to the default
    and posts to the LIVE stocks admin chat, which is how this branch produced
    unauthorised sends.
    """
    sent = []
    _stub_admin_sender(monkeypatch, sent)
    monkeypatch.setattr(
        event_emitter.config, 'get_simulate_tradingview_traffic', lambda: True
    )

    await event_emitter.on_error('boom')

    assert len(sent) == 1
    assert sent[0][2] is not None, 'on_error dropped the runtime mode'
    assert sent[0][2].use_dev_telegram is True


@pytest.mark.parametrize(
    'source, accepted',
    [
        ("async_ee.emit('send_alert_to_telegram')", True),
        ('from src.event.event_emitter import ALERT_EVENT\nasync_ee.emit(ALERT_EVENT)', True),
        ('from src.event import event_emitter\nasync_ee.emit(event_emitter.ALERT_EVENT)', True),
        # The qualifier decides the value, and these two rebind it.
        ("event_emitter = object()\nasync_ee.emit(event_emitter.ALERT_EVENT)", False),
        ('import src.config.config as config\nasync_ee.emit(config.ALERT_EVENT)', False),
        ("async_ee.emit(NAMES[0].ALERT_EVENT)", False),
        ("ALERT_EVENT = 'typo'\nasync_ee.emit(ALERT_EVENT)", False),
        ("ALERT_EVENT, B = 'typo', 'b'\nasync_ee.emit(ALERT_EVENT)", False),
        ("ALERT_EVENT: str = 'typo'\nasync_ee.emit(ALERT_EVENT)", False),
        ('try:\n    pass\nexcept Exception as ALERT_EVENT:\n    pass\nasync_ee.emit(ALERT_EVENT)', False),
        ('from src.type.trading_view import ALERT_EVENT\nasync_ee.emit(ALERT_EVENT)', False),
        ('import os as ALERT_EVENT\nasync_ee.emit(ALERT_EVENT)', False),
        ('from src.event.event_emitter import NOTICE_EVENT as ALERT_EVENT\nasync_ee.emit(ALERT_EVENT)', False),
        ("async_ee.emit(f'{x}')", False),
    ],
)
def test_the_event_name_fence_rejects_every_rebinding(source, accepted):
    """Covers `_event_name`'s constant branch, which no live emit reaches.

    Every emit in the router passes a string literal, so the helper returns on
    its first line and the resolution and its fence are dead code -- exactly the
    state in which a fence quietly stops working. These are the shapes that
    would make the scan resolve an identifier to a value the running code does
    not use, which is the mismatch the whole AST test exists to catch.
    """
    tree = ast.parse(source)
    call = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == 'emit'
    )

    if accepted:
        assert _event_name(call.args[0], tree) == event_emitter.ALERT_EVENT
    else:
        with pytest.raises(AssertionError):
            _event_name(call.args[0], tree)
