from typing import List, Optional

from src.notification_destination import telegram_notification
from src.util.my_telegram import format_messages_to_telegram


MAX_STOCKS_DIGEST_CHUNKS = 2
TRUNCATION_NOTICE = ' truncated to fit Telegram'


def build_digest_messages(
    tradingview_messages: List[str],
    vix_messages: Optional[List[str]] = None,
    sentiment_messages: Optional[List[str]] = None,
) -> List[str]:
    # The stocks digest deliberately owns a capped one-or-two-message layout:
    # message 1 is the TradingView anchor, and message 2 is optional supporting
    # context only when it still fits cleanly as one follow-up post.
    tradingview_section = _collapse_section_messages(tradingview_messages)
    if tradingview_section is None:
        return []

    vix_section = _collapse_section_messages(vix_messages)
    sentiment_section = _collapse_section_messages(sentiment_messages)
    compact_vix_section = _build_compact_vix_section(vix_messages)
    compact_sentiment_section = _build_compact_sentiment_section(sentiment_messages)
    # Try to preserve both supporting sections first, then progressively compact VIX
    # and sentiment before falling back to a single supporting section. The order
    # here defines the readability preference for the optional second message.
    supporting_message_candidates = [
        _combine_sections([vix_section, sentiment_section]),
        _combine_sections([compact_vix_section, sentiment_section]),
        _combine_sections([vix_section, compact_sentiment_section]),
        _combine_sections([compact_vix_section, compact_sentiment_section]),
        _combine_sections([vix_section]),
        _combine_sections([compact_vix_section]),
        _combine_sections([sentiment_section]),
        _combine_sections([compact_sentiment_section]),
    ]

    if _fits_single_message(tradingview_section):
        for supporting_message in supporting_message_candidates:
            if supporting_message and _fits_single_message(supporting_message):
                return [tradingview_section, supporting_message]

        if vix_section is None and sentiment_section is None:
            return [tradingview_section]

    return [_truncate_to_single_message(tradingview_section)]


def _collapse_section_messages(messages: Optional[List[str]]) -> Optional[str]:
    if messages is None or len(messages) == 0:
        return None

    return format_messages_to_telegram(messages)


def _combine_sections(sections: List[Optional[str]]) -> Optional[str]:
    normalized_sections = [section for section in sections if section]
    if len(normalized_sections) == 0:
        return None
    return format_messages_to_telegram(normalized_sections)


def _build_compact_vix_section(
    vix_messages: Optional[List[str]],
) -> Optional[str]:
    if vix_messages is None or len(vix_messages) == 0:
        return None

    lines = [
        line.strip()
        for line in format_messages_to_telegram(vix_messages).splitlines()
        if line.strip() != ''
    ]
    if len(lines) == 0:
        return None

    header = lines[0]
    latest_entry = next((line for line in lines if line.startswith('date: ')), None)
    trend_line = next(
        (
            line
            for line in lines
            if 'Contango has been decreasing for the past' in line
        ),
        None,
    )

    compact_lines = [line for line in [header, latest_entry, trend_line] if line]
    return '\n'.join(dict.fromkeys(compact_lines))


def _build_compact_sentiment_section(
    sentiment_messages: Optional[List[str]],
) -> Optional[str]:
    if sentiment_messages is None or len(sentiment_messages) == 0:
        return None

    if len(sentiment_messages) == 1:
        return sentiment_messages[0]

    header = sentiment_messages[0]
    body_lines = [
        line.strip()
        for line in sentiment_messages[1].splitlines()
        if line.strip() != ''
    ]

    recent_entry = _first_content_line(body_lines=body_lines, marker='Sentiment:')
    average_entry = _first_content_line(body_lines=body_lines, marker='Average:')
    compact_lines = [line for line in [recent_entry, average_entry] if line]

    if len(compact_lines) == 0:
        return header

    return format_messages_to_telegram([header, '\n'.join(compact_lines)])


def _first_content_line(body_lines: List[str], marker: str) -> Optional[str]:
    try:
        start_index = body_lines.index(marker) + 1
    except ValueError:
        return None

    for line in body_lines[start_index:]:
        if line.endswith(':'):
            break
        return line

    return None

def _fits_single_message(message: str) -> bool:
    return len(telegram_notification._split_message_for_telegram(message)) == 1


def _truncate_to_single_message(message: str) -> str:
    max_total_length = telegram_notification.MAX_TELEGRAM_MESSAGE_LENGTH
    if len(message) <= max_total_length:
        return message

    available = max_total_length - len(TRUNCATION_NOTICE)
    if available <= 0:
        return message[:max_total_length]

    return f'{message[:available].rstrip()}{TRUNCATION_NOTICE}'
