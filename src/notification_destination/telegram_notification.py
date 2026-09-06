import logging
from typing import List, Optional

import telegram
import src.config.config as config
from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE, RuntimeMode
from src.type.market_data_type import MarketDataType
from src.util.exception import get_exception_message
from src.util.my_telegram import escape_markdown, message_separator

# TODO: Clean up

stocks_bot = None
stocks_admin_bot = None
stocks_dev_bot = None
crypto_bot = None
crypto_admin_bot = None
crypto_dev_bot = None

chat_id_to_telegram_client = {}
# There is deliberately no market_data_type -> ADMIN chat id map. Handing one out
# invites addressing the admin chat through `send_message_to_channel`, which
# DISABLE_TELEGRAM mutes; `send_message_to_admin` resolves the channel itself.
market_data_type_to_chat_id = {}

logger = logging.getLogger('Telegram notification')
MAX_TELEGRAM_MESSAGE_LENGTH = 4096

def _build_telegram_request() -> telegram.request.HTTPXRequest:
    return telegram.request.HTTPXRequest(
        connect_timeout=config.get_telegram_connect_timeout_seconds(),
        read_timeout=config.get_telegram_read_timeout_seconds(),
        write_timeout=config.get_telegram_write_timeout_seconds(),
        pool_timeout=config.get_telegram_pool_timeout_seconds(),
    )

def init_telegram_bots():
    global stocks_bot, stocks_admin_bot, stocks_dev_bot, crypto_bot, crypto_admin_bot, crypto_dev_bot
    logger.info('Initialising telegram bots')
    stocks_bot = telegram.Bot(
        token=config.get_telegram_stocks_bot_token(),
        request=_build_telegram_request(),
    )
    stocks_admin_bot = telegram.Bot(
        token=config.get_telegram_stocks_admin_bot_token(),
        request=_build_telegram_request(),
    )
    stocks_dev_bot = telegram.Bot(
        token=config.get_telegram_stocks_dev_bot_token(),
        request=_build_telegram_request(),
    )
    crypto_bot = telegram.Bot(
        token=config.get_telegram_crypto_bot_token(),
        request=_build_telegram_request(),
    )
    crypto_admin_bot = telegram.Bot(
        token=config.get_telegram_crypto_admin_bot_token(),
        request=_build_telegram_request(),
    )
    crypto_dev_bot = telegram.Bot(
        token=config.get_telegram_crypto_dev_bot_token(),
        request=_build_telegram_request(),
    )

    global chat_id_to_telegram_client
    chat_id_to_telegram_client[config.get_telegram_stocks_channel_id()] = stocks_bot
    chat_id_to_telegram_client[config.get_telegram_stocks_admin_id()] = stocks_admin_bot
    chat_id_to_telegram_client[config.get_telegram_stocks_dev_id()] = stocks_dev_bot

    chat_id_to_telegram_client[config.get_telegram_crypto_channel_id()] = crypto_bot
    chat_id_to_telegram_client[config.get_telegram_crypto_admin_id()] = crypto_admin_bot
    chat_id_to_telegram_client[config.get_telegram_crypto_dev_id()] = crypto_dev_bot

    global market_data_type_to_chat_id
    market_data_type_to_chat_id[MarketDataType.STOCKS] = config.get_telegram_stocks_channel_id()
    market_data_type_to_chat_id[MarketDataType.CRYPTO] = config.get_telegram_crypto_channel_id()



async def send_message_to_channel(
    message: str,
    chat_id,
    market_data_type: MarketDataType,
    runtime_mode: RuntimeMode | None = None,
):
    if config.get_disable_telegram():
        logger.info('Telegram is disabled')
        return

    if market_data_type is None:
        logger.warning('market_data_type is not passed in')
        return

    # Callers that omit runtime_mode should stay on the normal delivery path
    # instead of inheriting dev routing from any ambient process configuration.
    active_runtime_mode = (
        DEFAULT_RUNTIME_MODE if runtime_mode is None else runtime_mode
    )
    use_dev_telegram = active_runtime_mode.use_dev_telegram
    if use_dev_telegram or config.get_simulate_tradingview_traffic():
        chat_id = get_dev_channel_id_from_market_data_type(market_data_type)

    telegram_client = chat_id_to_telegram_client[chat_id]
    is_admin_chat = chat_id == get_admin_channel_id_from_market_data_type(
        market_data_type
    )

    try:
        if not is_admin_chat and len(message) > MAX_TELEGRAM_MESSAGE_LENGTH:
            res = await _send_split_message_to_channel(
                telegram_client=telegram_client,
                chat_id=chat_id,
                message=message,
            )
        else:
            res = await telegram_client.send_message(
                chat_id,
                text=message,
                parse_mode='MarkdownV2',
            )
        return res
    except Exception as e:
        logger.error(get_exception_message(e))
        if not is_admin_chat and _is_message_too_long_error(e):
            return await _send_split_message_to_channel(
                telegram_client=telegram_client,
                chat_id=chat_id,
                message=message,
            )
        fallback_message = _build_telegram_error_alert(
            context='send_message_to_channel',
            error_text=get_exception_message(e),
        )
        try:
            await telegram_client.send_message(
                chat_id,
                text=fallback_message,
                parse_mode='MarkdownV2',
            )
        except Exception as fallback_error:
            logger.error(get_exception_message(fallback_error))
        # Falls off the end returning None even when the fallback send SUCCEEDED.
        # This function's return is not on `send_message_to_admin`'s contract:
        # None here does not mean the send was suppressed, and no caller may read
        # it that way.

async def send_message_to_admin(
    message: str,
    market_data_type: MarketDataType,
    runtime_mode: RuntimeMode | None = None,
) -> Optional[telegram.Message]:
    """Send to the admin chat. None means DISABLE_TELEGRAM_ADMIN withheld it.

    `runtime_mode` exists so a caller that used to reach the admin chat through
    `send_message_to_channel` keeps that function's dev redirect when it moves
    here. Omit it and the alert goes to the admin chat, which is what an alert
    with no run context should do.

    Deliberately NOT `DISABLE_TELEGRAM`: that flag mutes user-facing output, and
    an operator reaching for it to quiet public channels during an incident must
    still be told the incident is getting worse. Ruled by the operator on
    2026-09-06, after that coupling shipped: "any errors in production should be
    surfaced, so that I am aware."

    A delivered message -- including one delivered by the error fallback below --
    always comes back, so `is None` is the callers' test for suppression and
    every other path must keep returning the Message.
    """
    # Before the client lookup, so a disabled process needs no initialised bots
    # and can reach no network at all -- which is the state that process is
    # normally in.
    if config.get_disable_telegram_admin():
        logger.info('Telegram admin alerts are disabled')
        return None

    channel_id = get_admin_channel_id_from_market_data_type(market_data_type)
    # Only an explicit test-mode run redirects. `send_message_to_channel` also
    # redirects on SIMULATE_TRADINGVIEW_TRAFFIC, and that is deliberately not
    # copied here: simulated traffic concerns user-facing output, and a real
    # failure during a simulation is still a real failure the admin wants.
    active_runtime_mode = (
        DEFAULT_RUNTIME_MODE if runtime_mode is None else runtime_mode
    )
    if active_runtime_mode.use_dev_telegram:
        channel_id = get_dev_channel_id_from_market_data_type(market_data_type)
    telegram_client = chat_id_to_telegram_client[channel_id]
    try:
        if len(message) > MAX_TELEGRAM_MESSAGE_LENGTH:
            # Split rather than refuse. A crash report is an escaped traceback and
            # routinely exceeds the limit; raising here used to replace the whole
            # traceback with a character count, which is the one outcome an alert
            # must never produce.
            responses = await _send_split_message_to_channel(
                telegram_client=telegram_client,
                chat_id=channel_id,
                message=message,
            )
            res = responses[-1] if responses else None
        else:
            res = await telegram_client.send_message(
                chat_id=channel_id,
                text=message,
                parse_mode='MarkdownV2',
            )
    except Exception as e:
        logger.error(get_exception_message(e))
        # If this fallback send also fails it propagates, which is the behaviour
        # every caller already had. Callers reporting a job failure guard the
        # alert themselves, so a dead Telegram cannot replace the failure they
        # were reporting -- see `record.py` and `message_sender_wrapper.py`.
        res = await telegram_client.send_message(
            chat_id=channel_id,
            text=_build_telegram_error_alert(
                context='send_message_to_admin',
                error_text=get_exception_message(e),
            ),
            parse_mode='MarkdownV2',
        )
    if res is not None:
        print_telegram_message(res)
    return res


async def send_crypto_signal_message(
    message: str,
    chat_id: str | None = None,
    runtime_mode: RuntimeMode | None = None,
):
    if config.get_disable_telegram():
        logger.info('Telegram is disabled')
        return

    active_runtime_mode = (
        DEFAULT_RUNTIME_MODE if runtime_mode is None else runtime_mode
    )
    telegram_client, resolved_chat_id = _resolve_crypto_signal_target(
        requested_chat_id=chat_id,
        runtime_mode=active_runtime_mode,
    )

    try:
        if len(message) > MAX_TELEGRAM_MESSAGE_LENGTH:
            res = await _send_split_message_to_channel(
                telegram_client=telegram_client,
                chat_id=resolved_chat_id,
                message=message,
            )
        else:
            res = await telegram_client.send_message(
                chat_id=resolved_chat_id,
                text=message,
                parse_mode='MarkdownV2',
            )
        return res
    except Exception as error:
        logger.error(get_exception_message(error))
        fallback_message = _build_telegram_error_alert(
            context='send_crypto_signal_message',
            error_text=get_exception_message(error),
        )
        return await telegram_client.send_message(
            chat_id=resolved_chat_id,
            text=fallback_message,
            parse_mode='MarkdownV2',
        )

def get_admin_channel_id_from_market_data_type(market_data_type: MarketDataType):
    if market_data_type == MarketDataType.CRYPTO:
        return config.get_telegram_crypto_admin_id()
    return config.get_telegram_stocks_admin_id()

def get_dev_channel_id_from_market_data_type(market_data_type: MarketDataType):
    if market_data_type == MarketDataType.CRYPTO:
        return config.get_telegram_crypto_dev_id()
    return config.get_telegram_stocks_dev_id()

def print_telegram_message(res: telegram.Message):
    logging.info(f"Sent to {res.chat.title} {res.chat.type} at {res.date}. Message id {res.id}")


def _resolve_crypto_signal_target(
    requested_chat_id: str | None,
    runtime_mode: RuntimeMode,
):
    # Phase 1 signal review stays on the admin/operator path even in test mode;
    # unlike the public digest, it should not inherit generic dev-channel routing.
    resolved_chat_id = (
        config.get_crypto_signal_recipient_id()
        if requested_chat_id is None
        else requested_chat_id
    )
    if resolved_chat_id == config.get_telegram_crypto_channel_id():
        raise RuntimeError(
            'Crypto signal delivery to the public crypto channel is disabled in phase 1'
        )
    return crypto_admin_bot, resolved_chat_id


def _build_telegram_error_alert(context: str, error_text: str) -> str:
    header = escape_markdown(f'{context} failed. Full traceback omitted; check logs.')
    available = MAX_TELEGRAM_MESSAGE_LENGTH - len(header) - 2
    detail = escape_markdown(error_text[:max(0, available)])
    return f'{header}\n\n{detail}'


async def _send_split_message_to_channel(telegram_client, chat_id, message: str):
    chunks = _split_message_for_telegram(message=message)
    responses = []
    for chunk in chunks:
        responses.append(
            await telegram_client.send_message(
                chat_id,
                text=chunk,
                parse_mode='MarkdownV2',
            )
        )
    return responses


def _split_message_for_telegram(message: str) -> List[str]:
    section_separator = escape_markdown(f"\n{message_separator()}\n")
    sections = [
        section.strip()
        for section in message.split(section_separator)
        if section.strip() != ''
    ]
    if len(sections) > 1:
        return _pack_message_blocks(sections, separator='\n\n')

    paragraphs = [
        paragraph.strip()
        for paragraph in message.split('\n\n')
        if paragraph.strip() != ''
    ]
    if len(paragraphs) > 1:
        return _pack_message_blocks(paragraphs, separator='\n\n')

    lines = [line for line in message.split('\n') if line != '']
    if len(lines) > 1:
        return _pack_message_blocks(lines, separator='\n')

    return [
        message[index:index + MAX_TELEGRAM_MESSAGE_LENGTH]
        for index in range(0, len(message), MAX_TELEGRAM_MESSAGE_LENGTH)
    ]


def _pack_message_blocks(blocks: List[str], separator: str) -> List[str]:
    packed_blocks = []
    current = ''

    for block in blocks:
        if len(block) > MAX_TELEGRAM_MESSAGE_LENGTH:
            if current != '':
                packed_blocks.append(current)
                current = ''
            packed_blocks.extend(_split_message_for_telegram(block))
            continue

        if current == '':
            current = block
            continue

        candidate = f'{current}{separator}{block}'
        if len(candidate) <= MAX_TELEGRAM_MESSAGE_LENGTH:
            current = candidate
            continue

        packed_blocks.append(current)
        current = block

    if current != '':
        packed_blocks.append(current)

    return packed_blocks


def _is_message_too_long_error(error: Exception) -> bool:
    return 'message is too long' in str(error).lower()
