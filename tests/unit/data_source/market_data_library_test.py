from unittest.mock import Mock

from src.data_source import market_data_library


def test_init_market_data_api_passes_cnn_page_load_timeout(monkeypatch):
    market_data_library.crypto_api = None
    market_data_library.tradfi_api = None

    crypto_api = Mock()
    tradfi_api = Mock()
    crypto_api_cls = Mock(return_value=crypto_api)
    tradfi_api_cls = Mock(return_value=tradfi_api)

    monkeypatch.setattr(market_data_library, 'CryptoAPI', crypto_api_cls)
    monkeypatch.setattr(market_data_library, 'TradFiAPI', tradfi_api_cls)
    monkeypatch.setattr(
        market_data_library.config,
        'get_cryptoquant_api_token',
        lambda: 'token',
    )
    monkeypatch.setattr(
        market_data_library.config,
        'get_selenium_stealth',
        lambda: True,
    )
    monkeypatch.setattr(
        market_data_library.config,
        'get_selenium_remote_mode',
        lambda: True,
    )
    monkeypatch.setattr(
        market_data_library.config,
        'get_cnn_page_load_timeout_seconds',
        lambda: 12,
    )
    monkeypatch.setattr(
        market_data_library.config,
        'get_selenium_server_host',
        lambda: 'http://localhost:4444',
    )

    try:
        market_data_library.init_market_data_api()

        crypto_api_cls.assert_called_once_with(cryptoquant_api_token='token')
        tradfi_api_cls.assert_called_once_with(
            is_stealth=True,
            remote_mode=True,
            page_load_timeout_seconds=12,
            server_host='http://localhost:4444',
        )
    finally:
        market_data_library.crypto_api = None
        market_data_library.tradfi_api = None
