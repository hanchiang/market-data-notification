from typing import Any, Optional

from market_data_library import CryptoAPI, TradFiAPI

from src.config import config

crypto_api: Optional[CryptoAPI] = None
tradfi_api: Optional[TradFiAPI] = None


def get_crypto_api() -> CryptoAPI:
    init_market_data_api()
    assert crypto_api is not None
    return crypto_api


def get_tradfi_api() -> TradFiAPI:
    init_market_data_api()
    assert tradfi_api is not None
    return tradfi_api


def init_market_data_api() -> None:
    global crypto_api
    global tradfi_api
    if crypto_api is None:
        crypto_api = CryptoAPI(
            cryptoquant_api_token=config.get_cryptoquant_api_token(),
        )
    if tradfi_api is None:
        tradfi_api_kwargs: dict[str, Any] = {
            'is_stealth': config.get_selenium_stealth(),
            'remote_mode': config.get_selenium_remote_mode(),
        }
        selenium_server_host = config.get_selenium_server_host()
        if selenium_server_host is not None:
            tradfi_api_kwargs['server_host'] = selenium_server_host

        tradfi_api = TradFiAPI(**tradfi_api_kwargs)
