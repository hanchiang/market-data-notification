import os
from pathlib import Path
from typing import Tuple, List
from urllib.parse import urlparse

from dotenv import load_dotenv
from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE, RuntimeMode

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

def get_cnn_page_load_timeout_seconds() -> int:
    raw_value = os.getenv('CNN_PAGE_LOAD_TIMEOUT_SECONDS', '45')
    try:
        timeout_seconds = int(raw_value)
    except ValueError as error:
        raise RuntimeError(
            'CNN_PAGE_LOAD_TIMEOUT_SECONDS must be a positive integer'
        ) from error
    if timeout_seconds <= 0:
        raise RuntimeError(
            'CNN_PAGE_LOAD_TIMEOUT_SECONDS must be a positive integer'
        )
    return timeout_seconds

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

def resolve_test_mode(is_test_mode: bool | None = None) -> bool:
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
    return [
        endpoint.strip()
        for endpoint in os.getenv('AUTH_EXCLUDE_ENDPOINTS', '').split(',')
        if endpoint.strip()
    ]

def get_api_auth_token():
    if not os.getenv('API_AUTH_TOKEN', None):
        raise RuntimeError("api auth token is missing")
    return os.getenv('API_AUTH_TOKEN')

def get_contango_single_day_decrease_threshold_ratio(
    is_test_mode: bool | None = None,
):
    return 0.4 if not resolve_test_mode(is_test_mode) else 0.01

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
    default = '2,5' if resolve_test_mode(is_test_mode) else '5,30'
    data = os.getenv('NUM_PAST_DAYS_RANGE_STOCKS_VOLUME_RANK', default)
    string_list = data.replace(' ', '').split(',')
    return tuple((int(x) for x in string_list))

def get_stocks_volume_alert_ratio_threshold(
    is_test_mode: bool | None = None,
) -> float:
    # Lower the default threshold in test mode for the same reason: easier local
    # visibility of real alert text without fabricating alerts in formatter code.
    default = '0.05' if resolve_test_mode(is_test_mode) else '0.2'
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
    if not resolve_test_mode(is_test_mode):
        return value
    return default if not is_negative else -default

def get_potential_overextended_by_symbol(is_test_mode: bool | None = None):
    resolved_test_mode = resolve_test_mode(is_test_mode)
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

def _get_positive_float_env(var_name: str, default: str) -> float:
    raw_value = os.getenv(var_name, default)
    try:
        parsed_value = float(raw_value)
    except ValueError as error:
        raise RuntimeError(f'{var_name} must be a positive number') from error
    if parsed_value <= 0:
        raise RuntimeError(f'{var_name} must be a positive number')
    return parsed_value

def get_telegram_connect_timeout_seconds() -> float:
    return _get_positive_float_env('TELEGRAM_CONNECT_TIMEOUT_SECONDS', '20')

def get_telegram_read_timeout_seconds() -> float:
    return _get_positive_float_env('TELEGRAM_READ_TIMEOUT_SECONDS', '20')

def get_telegram_write_timeout_seconds() -> float:
    return _get_positive_float_env('TELEGRAM_WRITE_TIMEOUT_SECONDS', '20')

def get_telegram_pool_timeout_seconds() -> float:
    return _get_positive_float_env('TELEGRAM_POOL_TIMEOUT_SECONDS', '5')

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

def get_coinalyze_api_key() -> str:
    return os.getenv('COINALYZE_API_KEY', '')

def has_coinalyze_api_key() -> bool:
    return bool(get_coinalyze_api_key().strip())

def is_crypto_signal_market_regime_enabled() -> bool:
    return os.getenv('CRYPTO_SIGNAL_MARKET_REGIME_ENABLED', 'false') == 'true'

def get_crypto_signal_market_regime_provider() -> str:
    provider = os.getenv(
        'CRYPTO_SIGNAL_MARKET_REGIME_PROVIDER',
        'coinalyze',
    ).strip().lower()
    if provider not in {'coinalyze', 'binance'}:
        raise RuntimeError(
            'CRYPTO_SIGNAL_MARKET_REGIME_PROVIDER must be coinalyze or binance'
        )
    return provider

def get_crypto_signal_market_regime_interval() -> str:
    interval = os.getenv(
        'CRYPTO_SIGNAL_MARKET_REGIME_INTERVAL',
        '1hour',
    ).strip()
    allowed_intervals = {
        '1min',
        '5min',
        '15min',
        '30min',
        '1hour',
        '2hour',
        '4hour',
        '6hour',
        '12hour',
        'daily',
    }
    if interval not in allowed_intervals:
        raise RuntimeError(
            'CRYPTO_SIGNAL_MARKET_REGIME_INTERVAL must be a Coinalyze interval'
        )
    return interval

def get_crypto_signal_market_regime_backfill_days() -> int:
    raw_value = os.getenv('CRYPTO_SIGNAL_MARKET_REGIME_BACKFILL_DAYS', '30')
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(
            'CRYPTO_SIGNAL_MARKET_REGIME_BACKFILL_DAYS must be a positive integer'
        ) from error
    if value <= 0:
        raise RuntimeError(
            'CRYPTO_SIGNAL_MARKET_REGIME_BACKFILL_DAYS must be a positive integer'
        )
    interval = get_crypto_signal_market_regime_interval()
    max_intraday_days = _get_market_regime_max_intraday_backfill_days(interval)
    if max_intraday_days is not None and value > max_intraday_days:
        # Coinalyze intraday history is datapoint-limited; rejecting oversized
        # windows is safer than silently summarizing a partial lookback.
        raise RuntimeError(
            'CRYPTO_SIGNAL_MARKET_REGIME_BACKFILL_DAYS exceeds Coinalyze '
            f'intraday retention for {interval}; maximum is {max_intraday_days} days'
        )
    return value

def _get_market_regime_max_intraday_backfill_days(interval: str) -> int | None:
    interval_minutes = {
        '1min': 1,
        '5min': 5,
        '15min': 15,
        '30min': 30,
        '1hour': 60,
        '2hour': 120,
        '4hour': 240,
        '6hour': 360,
        '12hour': 720,
    }
    minutes = interval_minutes.get(interval)
    if minutes is None:
        return None
    return max(1, (1500 * minutes) // (60 * 24))

def get_crypto_signal_market_regime_coinalyze_symbols() -> list[str]:
    raw_value = os.getenv('CRYPTO_SIGNAL_MARKET_REGIME_COINALYZE_SYMBOLS', '')
    symbols = [
        symbol.strip()
        for symbol in raw_value.split(',')
        if symbol.strip() != ''
    ]
    if len(symbols) > 10:
        raise RuntimeError(
            'CRYPTO_SIGNAL_MARKET_REGIME_COINALYZE_SYMBOLS supports at most 10 symbols'
        )
    return symbols

_DEFAULT_CRYPTO_SIGNAL_WATCHLIST_IDS = {
    'BTC': 1,
    'ETH': 1027,
    'SOL': 5426,
}


def get_crypto_signal_db_path(
    runtime_mode: RuntimeMode | None = None,
) -> str:
    prod_db_path = os.getenv(
        'CRYPTO_SIGNAL_DB_PATH',
        'var/crypto_signal/crypto_signal.sqlite3',
    )
    active_runtime_mode = (
        DEFAULT_RUNTIME_MODE if runtime_mode is None else runtime_mode
    )
    # Keep test-mode history isolated so local replay does not contaminate the
    # operator-facing store used by normal runs.
    if not active_runtime_mode.is_test_mode:
        return prod_db_path
    return os.getenv(
        'CRYPTO_SIGNAL_TEST_DB_PATH',
        _build_test_crypto_signal_db_path(prod_db_path),
    )


def get_crypto_signal_recipient_id() -> str:
    return os.getenv(
        'CRYPTO_SIGNAL_RECIPIENT_ID',
        get_telegram_crypto_admin_id(),
    )


def get_crypto_signal_tracked_universe() -> list[tuple[str, int]]:
    return _parse_crypto_signal_coin_entries(
        env_name='CRYPTO_SIGNAL_TRACKED_UNIVERSE',
        default_raw_value='BTC,ETH,SOL',
    )


def get_crypto_signal_watchlist() -> list[tuple[str, int]]:
    return _parse_crypto_signal_coin_entries(
        env_name='CRYPTO_SIGNAL_WATCHLIST',
        default_raw_value='',
    )


def get_crypto_signal_dynamic_candidate_min_price_usd() -> float:
    # Token price is a weak proxy for tradability because supply design can
    # make legitimate projects look artificially "cheap". Keep the default at
    # zero and rely on the separate volume floor unless an operator explicitly
    # wants a stricter price-based presentation filter.
    return _get_positive_float_env(
        env_name='CRYPTO_SIGNAL_DYNAMIC_CANDIDATE_MIN_PRICE_USD',
        default_raw_value='0',
        allow_zero=True,
    )


def get_crypto_signal_dynamic_candidate_min_volume_24h() -> float:
    # This floor only affects operator ranking for dynamic candidates. Stored
    # history remains broader so later analysis is not forced through the same
    # presentation threshold.
    return _get_positive_float_env(
        env_name='CRYPTO_SIGNAL_DYNAMIC_CANDIDATE_MIN_VOLUME_24H',
        default_raw_value='50000000',
        allow_zero=True,
    )


def _parse_crypto_signal_coin_entries(
    env_name: str,
    default_raw_value: str,
) -> list[tuple[str, int]]:
    raw_value = os.getenv(env_name, default_raw_value)
    entries = []
    for raw_entry in raw_value.split(','):
        entry = raw_entry.strip()
        if entry == '':
            continue

        if ':' in entry:
            symbol, raw_coin_id = entry.split(':', 1)
            symbol = symbol.strip().upper()
            raw_coin_id = raw_coin_id.strip()
            if symbol == '' or raw_coin_id == '':
                raise RuntimeError(
                    f'{env_name} entries must look like SYMBOL or SYMBOL:ID'
                )
            try:
                coin_id = int(raw_coin_id)
            except ValueError as error:
                raise RuntimeError(
                    f'{env_name} coin ids must be integers'
                ) from error
        else:
            symbol = entry.upper()
            if symbol not in _DEFAULT_CRYPTO_SIGNAL_WATCHLIST_IDS:
                raise RuntimeError(
                    f'{env_name} unqualified symbols must use a '
                    'known default id or the SYMBOL:ID form'
                )
            coin_id = _DEFAULT_CRYPTO_SIGNAL_WATCHLIST_IDS[symbol]

        entries.append((symbol, coin_id))
    return entries


def _build_test_crypto_signal_db_path(prod_db_path: str) -> str:
    path = Path(prod_db_path)
    if path.suffix == '':
        return f'{prod_db_path}.test'
    return str(path.with_name(f'{path.stem}.test{path.suffix}'))


def _get_positive_float_env(
    env_name: str,
    default_raw_value: str,
    allow_zero: bool = False,
) -> float:
    raw_value = os.getenv(env_name, default_raw_value)
    try:
        parsed_value = float(raw_value)
    except ValueError as error:
        raise RuntimeError(f'{env_name} must be a positive number') from error

    if allow_zero:
        if parsed_value < 0:
            raise RuntimeError(f'{env_name} must be a positive number')
    elif parsed_value <= 0:
        raise RuntimeError(f'{env_name} must be a positive number')
    return parsed_value

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
