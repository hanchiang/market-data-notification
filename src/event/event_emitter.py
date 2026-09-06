import logging
from typing import Optional

from pyee.asyncio import  AsyncIOEventEmitter

from src.config import config
from src.notification_destination import telegram_notification
from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE, RuntimeMode
from src.type.market_data_type import MarketDataType
from src.util.my_telegram import escape_markdown
from src.util.exception import get_exception_message

async_ee = AsyncIOEventEmitter()

logger = logging.getLogger('Event emitter')

# Neither event carries a chat id. Routers used to pass an admin chat id into
# `send_to_telegram`, which delivers through `send_message_to_channel` -- muted
# by DISABLE_TELEGRAM, a deployed secret, so a webhook probe raised no alert in
# production. Splitting by INTENT rather than destination keeps both flags
# meaningful: an operator can quiet routine chatter without silencing warnings.
ALERT_EVENT = 'send_alert_to_telegram'
NOTICE_EVENT = 'send_notice_to_telegram'


def _router_runtime_mode() -> RuntimeMode:
    """Dev routing for messages a simulation produced.

    `send_message_to_channel` redirects to the dev channel when
    SIMULATE_TRADINGVIEW_TRAFFIC is on, and the webhook router is precisely the
    surface that flag simulates -- so its messages must keep that redirect when
    they travel the admin sender, which has no such rule of its own (a real job
    failure during a simulation is still real, and must not be redirected).
    """
    if config.get_simulate_tradingview_traffic():
        return RuntimeMode(use_dev_telegram=True)
    return DEFAULT_RUNTIME_MODE


async def _alert_admin(
    message: str,
    market_data_type: MarketDataType,
    runtime_mode: Optional[RuntimeMode] = None,
) -> None:
    """Deliver an admin alert, and never raise.

    pyee turns a handler exception into an `'error'` event, and this module's
    error handler alerts the same way -- so a re-raise during a Telegram outage
    feeds itself. Both handlers below route through here for that reason.
    """
    try:
        res = await telegram_notification.send_message_to_admin(
            message=message,
            market_data_type=market_data_type,
            runtime_mode=runtime_mode,
        )
        if res is None:
            logger.info('admin alert suppressed by the sender (DISABLE_TELEGRAM_ADMIN)')
    except Exception as e:
        logger.error(f"admin alert could not be delivered: {get_exception_message(e)}")


@async_ee.on('error')
async def on_error(message):
    logger.error(f"event emitter error: {message}")
    await _alert_admin(
        escape_markdown(str(f'event emitter error: {message}')),
        MarketDataType.STOCKS,
        # The same redirect the alert handler applies. An error event during a
        # simulated webhook run is router-caused by construction -- a malformed
        # emit or a handler bug -- so it must not post to the live admin chat.
        _router_runtime_mode(),
    )


@async_ee.on(ALERT_EVENT)
async def send_alert_to_telegram_handler(
    *, message: str, market_data_type: Optional[MarketDataType] = None
) -> None:
    """Errors and warnings. Gated on DISABLE_TELEGRAM_ADMIN, never DISABLE_TELEGRAM.

    The webhook router's malicious-request warnings arrive here. Keyword-only on
    purpose: a positional emit would otherwise be swallowed as malformed, and a
    dropped alert is the failure this event exists to prevent -- a TypeError
    becomes an `'error'` event, which alerts.
    """
    await _alert_admin(
        message, market_data_type or MarketDataType.STOCKS, _router_runtime_mode()
    )


@async_ee.on(NOTICE_EVENT)
async def send_notice_to_telegram_handler(
    *, message: str, market_data_type: Optional[MarketDataType] = None
) -> None:
    """Routine operator chatter. Gated on DISABLE_TELEGRAM, like other output.

    A webhook success notice is not an alarm. Routing it through the alert path
    would leave DISABLE_TELEGRAM_ADMIN as the only way to quiet it, which would
    silence the warnings too -- the coupling the flag split removed.
    """
    resolved = market_data_type or MarketDataType.STOCKS
    try:
        res = await telegram_notification.send_message_to_channel(
            message=message,
            chat_id=telegram_notification.get_admin_channel_id_from_market_data_type(
                resolved
            ),
            market_data_type=resolved,
            runtime_mode=DEFAULT_RUNTIME_MODE,
        )
        # A split send returns a list, so this cannot assume a single Message.
        for sent in res if isinstance(res, list) else [res] if res else []:
            telegram_notification.print_telegram_message(sent)
    except Exception as e:
        logger.error(f"notice could not be delivered: {get_exception_message(e)}")
        # A notice failing is itself an error, and the ruling is that production
        # errors reach the admin chat. `_alert_admin` cannot raise, so this does
        # not reintroduce the self-feeding error loop.
        await _alert_admin(
            escape_markdown(f'notice could not be delivered: {get_exception_message(e)}'),
            resolved,
            _router_runtime_mode(),
        )
