import os
from dotenv import load_dotenv

load_dotenv()

def get_telegram_stocks_bot_token():
    if not os.getenv('STOCKS_TELEGRAM_BOT_TOKEN', None):
        raise RuntimeError("telegram stocks bot token is missing")
    return os.getenv('STOCKS_TELEGRAM_BOT_TOKEN')

def get_telegram_stocks_channel_id():
    if not os.getenv('STOCKS_TELEGRAM_CHANNEL_ID', None):
        raise RuntimeError("telegram stocks channel is missing")
    return os.getenv('STOCKS_TELEGRAM_CHANNEL_ID')

def get_telegram_stocks_admin_bot_token():
    if not os.getenv('STOCKS_TELEGRAM_ADMIN_BOT_TOKEN', None):
        raise RuntimeError("telegram stocks admin bot token is missing")
    return os.getenv('STOCKS_TELEGRAM_ADMIN_BOT_TOKEN')

def get_telegram_stocks_admin_id():
    if not os.getenv('STOCKS_TELEGRAM_ADMIN_ID', None):
        raise RuntimeError("telegram stocks admin id is missing")
    return os.getenv('STOCKS_TELEGRAM_ADMIN_ID')

def get_telegram_stocks_dev_bot_token():
    if not os.getenv('STOCKS_TELEGRAM_DEV_BOT_TOKEN', None):
        raise RuntimeError("telegram stocks dev bot token is missing")
    return os.getenv('STOCKS_TELEGRAM_DEV_BOT_TOKEN')

def get_telegram_stocks_dev_id():
    if not os.getenv('STOCKS_TELEGRAM_DEV_ID', None):
        raise RuntimeError("telegram stocks dev id is missing")
    return os.getenv('STOCKS_TELEGRAM_DEV_ID')

def get_telegram_crypto_bot_token():
    if not os.getenv('CRYPTO_TELEGRAM_BOT_TOKEN', None):
        raise RuntimeError("telegram crypto bot token is missing")
    return os.getenv('CRYPTO_TELEGRAM_BOT_TOKEN')

def get_telegram_crypto_channel_id():
    if not os.getenv('CRYPTO_TELEGRAM_CHANNEL_ID', None):
        raise RuntimeError("telegram crypto channel is missing")
    return os.getenv('CRYPTO_TELEGRAM_CHANNEL_ID')

def get_telegram_crypto_admin_bot_token():
    if not os.getenv('CRYPTO_TELEGRAM_ADMIN_BOT_TOKEN', None):
        raise RuntimeError("telegram crypto admin bot token is missing")
    return os.getenv('CRYPTO_TELEGRAM_ADMIN_BOT_TOKEN')

def get_telegram_crypto_admin_id():
    if not os.getenv('CRYPTO_TELEGRAM_ADMIN_ID', None):
        raise RuntimeError("telegram crypto admin id is missing")
    return os.getenv('CRYPTO_TELEGRAM_ADMIN_ID')

def get_telegram_crypto_dev_bot_token():
    if not os.getenv('CRYPTO_TELEGRAM_DEV_BOT_TOKEN', None):
        raise RuntimeError("telegram crypto dev bot token is missing")
    return os.getenv('CRYPTO_TELEGRAM_DEV_BOT_TOKEN')

def get_telegram_crypto_dev_id():
    if not os.getenv('CRYPTO_TELEGRAM_DEV_ID', None):
        raise RuntimeError("telegram crypto dev id is missing")
    return os.getenv('CRYPTO_TELEGRAM_DEV_ID')

def get_is_testing_telegram():
    return os.getenv('IS_TESTING_TELEGRAM', False) == 'true'

def set_is_testing_telegram(testing: str):
    os.environ['IS_TESTING_TELEGRAM'] = testing

def get_simulate_tradingview_traffic():
    return os.getenv('SIMULATE_TRADINGVIEW_TRAFFIC', False) == 'true'

def get_trading_view_ips():
    if not os.getenv('TRADING_VIEW_IPS', None):
        raise RuntimeError("trading view ips is missing")
    return os.getenv('TRADING_VIEW_IPS').split(',')

def get_contango_single_day_decrease_threshold_ratio():
    return 0.4 if not get_is_testing_telegram() else 0.01

def get_contango_decrease_past_n_days_threshold():
    return 5 if not get_is_testing_telegram() else 2

def get_vix_central_number_of_days():
    return 7

def overextended_helper(value: float, is_negative=False, default=0.01) -> float:
    if not get_is_testing_telegram():
        return value
    return default if not is_negative else -default

median_overextended_by_symbol = {
    'DJIA': {

    },
    'IWM': {

    },
    'QQQ': {
        'above': overextended_helper(value=0.058) if not get_is_testing_telegram() else overextended_helper(value=0.01),
        'below': overextended_helper(value=-0.081, is_negative=True) if not get_is_testing_telegram() else overextended_helper(value=-0.01, is_negative=True),
    },
    'SPY': {
        'above': overextended_helper(value=0.0435) if not get_is_testing_telegram() else overextended_helper(value=0.01),
        'below': overextended_helper(value=-0.067, is_negative=True) if not get_is_testing_telegram() else overextended_helper(value=-0.01, is_negative=True),
    },
    'AAPL': {
        'above': overextended_helper(value=0.0735) if not get_is_testing_telegram() else overextended_helper(value=0.01),
        'below': overextended_helper(value=-0.081, is_negative=True) if not get_is_testing_telegram() else overextended_helper(value=-0.01, is_negative=True)
    },
    'AMD': {

    },
    'AMZN': {
        'above': overextended_helper(value=0.087) if not get_is_testing_telegram() else overextended_helper(value=0.01),
        'below': overextended_helper(value=-0.111, is_negative=True) if not get_is_testing_telegram() else overextended_helper(value=-0.01, is_negative=True)
    },
    'BABA': {

    },
    'COIN': {

    },
    'GOOGL': {

    },
    'META': {

    },
    'MSFT': {

    },
    'NFLX': {

    },
    'NVDA': {

    },
    'TSLA': {

    },
    'VIX': {
        'above': 30 if not get_is_testing_telegram() else 26,
        'below': 15.5 if not get_is_testing_telegram() else 26
    }
}
def get_potential_overextended_by_symbol():
    return median_overextended_by_symbol

def get_tradingview_webhook_secret():
    if not os.getenv('TRADING_VIEW_WEBHOOK_SECRET', None):
        raise RuntimeError('TRADING_VIEW_WEBHOOK_SECRET is missing')
    return os.getenv('TRADING_VIEW_WEBHOOK_SECRET')

def get_disable_telegram():
    return os.getenv('DISABLE_TELEGRAM', False) == 'true'

def get_redis_host():
    return os.getenv('REDIS_HOST', 'localhost')

def get_redis_port():
    return os.getenv('REDIS_PORT', 6379)

def get_redis_db():
    return os.getenv('REDIS_DB', 0)
def get_trading_view_days_to_store():
    return os.getenv('TRADING_VIEW_DAYS_TO_STORE', 30)

def get_stocks_job_start_local_hour():
    return int(os.getenv('STOCKS_JOB_START_LOCAL_HOUR', 9))

def get_stocks_job_start_local_minute():
    return int(os.getenv('STOCKS_JOB_START_LOCAL_MINUTE', 0))

def get_crypto_job_start_local_hours():
    return os.getenv('CRYPTO_JOB_START_LOCAL_HOURS', '9,16')

def get_crypto_job_start_local_minutes():
    return os.getenv('CRYPTO_JOB_START_LOCAL_MINUTES', '0,15')

def get_job_delay_tolerance_second():
    return int(os.getenv('JOB_DELAY_TOLERANCE_SECOND', 60 * 30))

def get_messari_asset_metrics_sha256():
    return os.getenv('MESSARI_ASSET_METRICS_SHA256', '')