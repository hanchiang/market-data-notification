from typing import Optional

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
            cryptoquant_preferred_exchanges=config.get_cryptoquant_preferred_exchanges(),
        )
    if tradfi_api is None:
        tradfi_api = TradFiAPI()
