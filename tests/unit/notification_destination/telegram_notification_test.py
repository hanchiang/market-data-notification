from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.notification_destination import telegram_notification
from src.type.market_data_type import MarketDataType


def _build_message_response(message_id: int):
    return SimpleNamespace(
        chat=SimpleNamespace(title='channel', type='private'),
        date='2026-03-29T00:00:00Z',
        id=message_id,
    )


@pytest.mark.asyncio
async def test_send_message_to_admin_uses_crypto_admin_client(monkeypatch):
    crypto_admin_client = AsyncMock()
    crypto_admin_client.send_message = AsyncMock(
        return_value=_build_message_response(message_id=123)
    )
    stocks_admin_client = AsyncMock()
    stocks_admin_client.send_message = AsyncMock()

    monkeypatch.setattr(
        telegram_notification,
        'chat_id_to_telegram_client',
        {
            'crypto-admin-chat': crypto_admin_client,
            'stocks-admin-chat': stocks_admin_client,
        },
    )
    monkeypatch.setattr(
        telegram_notification,
        'get_admin_channel_id_from_market_data_type',
        lambda market_data_type: (
            'crypto-admin-chat'
            if market_data_type == MarketDataType.CRYPTO
            else 'stocks-admin-chat'
        ),
    )
    monkeypatch.setattr(telegram_notification, 'print_telegram_message', lambda _res: None)

    await telegram_notification.send_message_to_admin(
        message='crypto alert',
        market_data_type=MarketDataType.CRYPTO,
    )

    crypto_admin_client.send_message.assert_awaited_once_with(
        chat_id='crypto-admin-chat',
        text='crypto alert',
        parse_mode='MarkdownV2',
    )
    stocks_admin_client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_message_to_admin_uses_same_client_for_fallback(monkeypatch):
    crypto_admin_client = AsyncMock()
    crypto_admin_client.send_message = AsyncMock(
        side_effect=[
            RuntimeError('primary failure'),
            _build_message_response(message_id=456),
        ]
    )
    stocks_admin_client = AsyncMock()
    stocks_admin_client.send_message = AsyncMock()

    monkeypatch.setattr(
        telegram_notification,
        'chat_id_to_telegram_client',
        {
            'crypto-admin-chat': crypto_admin_client,
            'stocks-admin-chat': stocks_admin_client,
        },
    )
    monkeypatch.setattr(
        telegram_notification,
        'get_admin_channel_id_from_market_data_type',
        lambda market_data_type: (
            'crypto-admin-chat'
            if market_data_type == MarketDataType.CRYPTO
            else 'stocks-admin-chat'
        ),
    )
    monkeypatch.setattr(telegram_notification, 'print_telegram_message', lambda _res: None)

    await telegram_notification.send_message_to_admin(
        message='crypto alert',
        market_data_type=MarketDataType.CRYPTO,
    )

    assert crypto_admin_client.send_message.await_count == 2
    stocks_admin_client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_message_to_channel_splits_large_user_facing_message(monkeypatch):
    channel_client = AsyncMock()
    channel_client.send_message = AsyncMock(
        side_effect=[
            _build_message_response(message_id=1),
            _build_message_response(message_id=2),
        ]
    )

    monkeypatch.setattr(
        telegram_notification,
        'chat_id_to_telegram_client',
        {'crypto-channel': channel_client},
    )
    monkeypatch.setattr(
        telegram_notification,
        'get_admin_channel_id_from_market_data_type',
        lambda _market_data_type: 'crypto-admin-chat',
    )
    monkeypatch.setattr(
        telegram_notification,
        'MAX_TELEGRAM_MESSAGE_LENGTH',
        20,
    )
    monkeypatch.setattr(
        telegram_notification.config,
        'get_disable_telegram',
        lambda: False,
    )
    monkeypatch.setattr(
        telegram_notification.config,
        'get_is_testing_telegram',
        lambda: False,
    )
    monkeypatch.setattr(
        telegram_notification.config,
        'get_simulate_tradingview_traffic',
        lambda: False,
    )

    separator = telegram_notification.escape_markdown(
        f"\n{telegram_notification.message_separator()}\n"
    )
    message = f'alpha section{separator}beta section'

    responses = await telegram_notification.send_message_to_channel(
        message=message,
        chat_id='crypto-channel',
        market_data_type=MarketDataType.CRYPTO,
    )

    assert [response.id for response in responses] == [1, 2]
    assert channel_client.send_message.await_count == 2
    first_call = channel_client.send_message.await_args_list[0]
    second_call = channel_client.send_message.await_args_list[1]
    assert first_call.kwargs['text'] == 'alpha section'
    assert second_call.kwargs['text'] == 'beta section'


@pytest.mark.asyncio
async def test_send_message_to_channel_keeps_admin_path_explicit_when_too_large(monkeypatch):
    admin_client = AsyncMock()
    admin_client.send_message = AsyncMock(
        side_effect=[
            RuntimeError('Message is too long'),
            _build_message_response(message_id=3),
        ]
    )

    monkeypatch.setattr(
        telegram_notification,
        'chat_id_to_telegram_client',
        {'crypto-admin-chat': admin_client},
    )
    monkeypatch.setattr(
        telegram_notification,
        'get_admin_channel_id_from_market_data_type',
        lambda _market_data_type: 'crypto-admin-chat',
    )
    monkeypatch.setattr(
        telegram_notification.config,
        'get_disable_telegram',
        lambda: False,
    )
    monkeypatch.setattr(
        telegram_notification.config,
        'get_is_testing_telegram',
        lambda: False,
    )
    monkeypatch.setattr(
        telegram_notification.config,
        'get_simulate_tradingview_traffic',
        lambda: False,
    )

    await telegram_notification.send_message_to_channel(
        message='x' * 5000,
        chat_id='crypto-admin-chat',
        market_data_type=MarketDataType.CRYPTO,
    )

    assert admin_client.send_message.await_count == 2
    first_call = admin_client.send_message.await_args_list[0]
    second_call = admin_client.send_message.await_args_list[1]
    assert first_call.kwargs['text'] == 'x' * 5000
    assert 'send\\_message\\_to\\_channel failed' in second_call.kwargs['text']
