import logging
from typing import List

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
market_data_type_to_admin_chat_id = {}
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

    global market_data_type_to_admin_chat_id
    market_data_type_to_admin_chat_id[MarketDataType.STOCKS] = config.get_telegram_stocks_admin_id()
    market_data_type_to_admin_chat_id[MarketDataType.CRYPTO] = config.get_telegram_crypto_admin_id()

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

async def send_message_to_admin(message: str, market_data_type: MarketDataType):
    channel_id = get_admin_channel_id_from_market_data_type(market_data_type)
    telegram_client = chat_id_to_telegram_client[channel_id]
    try:
        if len(message) > MAX_TELEGRAM_MESSAGE_LENGTH:
            raise ValueError(
                f'Admin message exceeds Telegram limit: {len(message)} characters'
            )
        res = await telegram_client.send_message(
            chat_id=channel_id,
            text=message,
            parse_mode='MarkdownV2',
        )
    except Exception as e:
        logger.error(get_exception_message(e))
        res = await telegram_client.send_message(
            chat_id=channel_id,
            text=_build_telegram_error_alert(
                context='send_message_to_admin',
                error_text=get_exception_message(e),
            ),
            parse_mode='MarkdownV2',
        )
    print_telegram_message(res)
    return res

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
