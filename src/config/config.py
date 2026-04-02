import os
from typing import Tuple, List
from urllib.parse import urlparse

from dotenv import load_dotenv
from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE

load_dotenv()

def get_env():
    return os.getenv('ENV', 'dev')

def get_selenium_remote_mode():
    return os.getenv('SELENIUM_REMOTE_MODE', 'false') == 'true'

def get_selenium_server_host():
    host = os.getenv('SELENIUM_SERVER_HOST', None)
    if get_selenium_remote_mode() and not host:
        raise RuntimeError(
            'SELENIUM_SERVER_HOST is missing while SELENIUM_REMOTE_MODE=true'
        )
    if host:
        parsed_host = urlparse(host if '://' in host else f'http://{host}')
        if parsed_host.hostname == 'chrome' and not is_running_in_container():
            raise RuntimeError(
                'SELENIUM_SERVER_HOST points to the Docker-only host "chrome" '
                'while this process is running outside a container. Use '
                'http://localhost:4444 when connecting from the host machine, '
                'or set SELENIUM_REMOTE_MODE=false for local browser mode.'
            )
    return host

def get_selenium_stealth():
    return os.getenv('SELENIUM_STEALTH', 'true') == 'true'

def is_running_in_container():
    return os.path.exists('/.dockerenv')

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
    return os.getenv('IS_TESTING_TELEGRAM', 'false') == 'true'

def set_is_testing_telegram(testing: str):
    os.environ['IS_TESTING_TELEGRAM'] = testing

def resolve_is_testing_telegram(is_test_mode: bool | None = None) -> bool:
    # Keep helper defaults production-safe. RuntimeMode is now the supported
    # source for test-mode behavior; the legacy env flag should not implicitly
    # reroute helpers when a caller forgets to pass explicit runtime state.
    return DEFAULT_RUNTIME_MODE.is_test_mode if is_test_mode is None else is_test_mode

def get_simulate_tradingview_traffic():
    return os.getenv('SIMULATE_TRADINGVIEW_TRAFFIC', 'false') == 'true'

def get_trading_view_ips():
    if not os.getenv('TRADING_VIEW_IPS', None):
        raise RuntimeError("trading view ips is missing")
    return os.getenv('TRADING_VIEW_IPS').split(',')

def get_whitelist_ips():
    return os.getenv('WHITELIST_IPS', '').split(',')

def get_auth_exclude_endpoints() -> List[str]:
    return os.getenv('AUTH_EXCLUDE_ENDPOINTS', '').split(',')

def get_api_auth_token():
    if not os.getenv('API_AUTH_TOKEN', None):
        raise RuntimeError("api auth token is missing")
    return os.getenv('API_AUTH_TOKEN')

def get_contango_single_day_decrease_threshold_ratio(
    is_test_mode: bool | None = None,
):
    return 0.4 if not resolve_is_testing_telegram(is_test_mode) else 0.01

def get_contango_decrease_past_n_days_threshold():
    return 5

def get_vix_central_number_of_days():
    return 7

def get_should_compare_stocks_volume_rank() -> bool:
    val = os.getenv('SHOULD_COMPARE_STOCKS_VOLUME_RANK', 'true')
    return True if val == 'true' or not val else False

def get_display_vix_futures_contango_decrease_past_n_days() -> bool:
    val = os.getenv('DISPLAY_VIX_FUTURES_CONTANGO_DECREASE_PAST_N_DAYS', 'true')
    return True if val == 'true' or not val else False

def get_number_of_past_days_range_for_stock_volume_rank(
    is_test_mode: bool | None = None,
) -> Tuple[int, int]:
    # Test-mode replay often uses older snapshots, so keep the comparison window
    # shorter there to make legitimate volume alerts easier to surface locally.
    default = '2,5' if resolve_is_testing_telegram(is_test_mode) else '5,30'
    data = os.getenv('NUM_PAST_DAYS_RANGE_STOCKS_VOLUME_RANK', default)
    string_list = data.replace(' ', '').split(',')
    return tuple((int(x) for x in string_list))

def get_stocks_volume_alert_ratio_threshold(
    is_test_mode: bool | None = None,
) -> float:
    # Lower the default threshold in test mode for the same reason: easier local
    # visibility of real alert text without fabricating alerts in formatter code.
    default = '0.05' if resolve_is_testing_telegram(is_test_mode) else '0.2'
    try:
        return float(os.getenv('STOCKS_VOLUME_ALERT_RATIO_THRESHOLD', default))
    except Exception:
        return float(default)

def overextended_helper(
    value: float,
    is_negative=False,
    default=0.01,
    is_test_mode: bool | None = None,
) -> float:
    if not resolve_is_testing_telegram(is_test_mode):
        return value
    return default if not is_negative else -default

def get_potential_overextended_by_symbol(is_test_mode: bool | None = None):
    resolved_test_mode = resolve_is_testing_telegram(is_test_mode)
    return {
        'DIA': {

        },
        'IWM': {

        },
        'QQQ': {
            'above': overextended_helper(
                value=0.058,
                is_test_mode=resolved_test_mode,
            ),
            'below': overextended_helper(
                value=-0.081,
                is_negative=True,
                is_test_mode=resolved_test_mode,
            ),
        },
        'SPY': {
            'above': overextended_helper(
                value=0.0435,
                is_test_mode=resolved_test_mode,
            ),
            'below': overextended_helper(
                value=-0.067,
                is_negative=True,
                is_test_mode=resolved_test_mode,
            ),
        },
        'AAPL': {
            'above': overextended_helper(
                value=0.0735,
                is_test_mode=resolved_test_mode,
            ),
            'below': overextended_helper(
                value=-0.081,
                is_negative=True,
                is_test_mode=resolved_test_mode,
            )
        },
        'AMD': {

        },
        'AMZN': {
            'above': overextended_helper(
                value=0.087,
                is_test_mode=resolved_test_mode,
            ),
            'below': overextended_helper(
                value=-0.111,
                is_negative=True,
                is_test_mode=resolved_test_mode,
            )
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
            'above': 30 if not resolved_test_mode else 26,
            'below': 15.5 if not resolved_test_mode else 26
        }
    }

def get_tradingview_webhook_secret():
    if not os.getenv('TRADING_VIEW_WEBHOOK_SECRET', None):
        raise RuntimeError('TRADING_VIEW_WEBHOOK_SECRET is missing')
    return os.getenv('TRADING_VIEW_WEBHOOK_SECRET')

def get_disable_telegram():
    return os.getenv('DISABLE_TELEGRAM', 'false') == 'true'

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

def get_cryptoquant_api_token() -> str:
    return os.getenv('CRYPTOQUANT_API_TOKEN', '')

def has_cryptoquant_api_token() -> bool:
    return bool(get_cryptoquant_api_token().strip())

def get_should_send_stocks_sentiment_message():
    return os.getenv('SHOULD_SEND_STOCKS_SENTIMENT_MESSAGE', 'true') == 'true'

def get_cmc_coin_price_change_24h_percentage_threshold() -> float:
    try:
        return float(os.getenv('CMC_COIN_PRICE_CHANGE_24H_PERCENTAGE_THRESHOLD', '10'))
    except Exception:
        return 10

def get_cmc_market_cap_change_24h_percentage_threshold() -> float:
    try:
        return float(os.getenv('CMC_COIN_MARKET_CAP_CHANGE_24H_PERCENTAGE_THRESHOLD', '3'))
    except Exception:
        return 3
