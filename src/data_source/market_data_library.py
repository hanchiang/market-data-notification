from market_data_library import CryptoAPI, TradFiAPI

crypto_api = None
tradfi_api = None
def get_crypto_api():
    init_market_data_api()
    return crypto_api

def get_tradfi_api():
    init_market_data_api()
    return tradfi_api
    
def init_market_data_api():
    global crypto_api
    global tradfi_api
    if crypto_api is None:
        crypto_api = CryptoAPI()
    if tradfi_api is None:
        tradfi_api = TradFiAPI()

