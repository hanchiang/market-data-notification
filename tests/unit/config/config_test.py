import pytest

from src.config import config
from src.runtime.runtime_mode import RuntimeMode


def test_get_selenium_server_host_returns_host_for_remote_mode(monkeypatch):
    monkeypatch.setenv('SELENIUM_REMOTE_MODE', 'true')
    monkeypatch.setenv('SELENIUM_SERVER_HOST', 'http://localhost:4444')

    assert config.get_selenium_server_host() == 'http://localhost:4444'


def test_get_selenium_server_host_is_optional_for_local_mode(monkeypatch):
    monkeypatch.setenv('SELENIUM_REMOTE_MODE', 'false')
    monkeypatch.delenv('SELENIUM_SERVER_HOST', raising=False)

    assert config.get_selenium_server_host() is None


def test_get_selenium_server_host_requires_host_for_remote_mode(monkeypatch):
    monkeypatch.setenv('SELENIUM_REMOTE_MODE', 'true')
    monkeypatch.delenv('SELENIUM_SERVER_HOST', raising=False)

    with pytest.raises(
        RuntimeError,
        match='SELENIUM_SERVER_HOST is missing while SELENIUM_REMOTE_MODE=true',
    ):
        config.get_selenium_server_host()


def test_get_selenium_server_host_rejects_docker_only_chrome_host_on_machine(
    monkeypatch,
):
    monkeypatch.setenv('SELENIUM_REMOTE_MODE', 'true')
    monkeypatch.setenv('SELENIUM_SERVER_HOST', 'http://chrome:4444')
    monkeypatch.setattr(config, 'is_running_in_container', lambda: False)

    with pytest.raises(
        RuntimeError,
        match='SELENIUM_SERVER_HOST points to the Docker-only host "chrome"',
    ):
        config.get_selenium_server_host()


def test_get_selenium_server_host_allows_docker_only_chrome_host_in_container(
    monkeypatch,
):
    monkeypatch.setenv('SELENIUM_REMOTE_MODE', 'true')
    monkeypatch.setenv('SELENIUM_SERVER_HOST', 'http://chrome:4444')
    monkeypatch.setattr(config, 'is_running_in_container', lambda: True)

    assert config.get_selenium_server_host() == 'http://chrome:4444'


def test_get_auth_exclude_endpoints_strips_blank_entries(monkeypatch):
    monkeypatch.setenv('AUTH_EXCLUDE_ENDPOINTS', ' /healthz, ,/tradingview/daily-stocks,')

    assert config.get_auth_exclude_endpoints() == [
        '/healthz',
        '/tradingview/daily-stocks',
    ]


def test_get_auth_exclude_endpoints_defaults_to_empty_list(monkeypatch):
    monkeypatch.delenv('AUTH_EXCLUDE_ENDPOINTS', raising=False)

    assert config.get_auth_exclude_endpoints() == []


def test_get_cnn_page_load_timeout_seconds_defaults_to_45(monkeypatch):
    monkeypatch.delenv('CNN_PAGE_LOAD_TIMEOUT_SECONDS', raising=False)

    assert config.get_cnn_page_load_timeout_seconds() == 45


def test_get_cnn_page_load_timeout_seconds_reads_env(monkeypatch):
    monkeypatch.setenv('CNN_PAGE_LOAD_TIMEOUT_SECONDS', '12')

    assert config.get_cnn_page_load_timeout_seconds() == 12


@pytest.mark.parametrize('value', ['0', '-1', 'abc'])
def test_get_cnn_page_load_timeout_seconds_requires_positive_integer(
    monkeypatch,
    value,
):
    monkeypatch.setenv('CNN_PAGE_LOAD_TIMEOUT_SECONDS', value)

    with pytest.raises(
        RuntimeError,
        match='CNN_PAGE_LOAD_TIMEOUT_SECONDS must be a positive integer',
    ):
        config.get_cnn_page_load_timeout_seconds()


@pytest.mark.parametrize(
    ('getter_name', 'env_name', 'default', 'configured'),
    [
        (
            'get_telegram_connect_timeout_seconds',
            'TELEGRAM_CONNECT_TIMEOUT_SECONDS',
            20.0,
            12.5,
        ),
        (
            'get_telegram_read_timeout_seconds',
            'TELEGRAM_READ_TIMEOUT_SECONDS',
            20.0,
            13.5,
        ),
        (
            'get_telegram_write_timeout_seconds',
            'TELEGRAM_WRITE_TIMEOUT_SECONDS',
            20.0,
            14.5,
        ),
        (
            'get_telegram_pool_timeout_seconds',
            'TELEGRAM_POOL_TIMEOUT_SECONDS',
            5.0,
            6.5,
        ),
    ],
)
def test_telegram_timeout_getters_support_defaults_and_env(
    monkeypatch,
    getter_name,
    env_name,
    default,
    configured,
):
    monkeypatch.delenv(env_name, raising=False)
    getter = getattr(config, getter_name)
    assert getter() == default

    monkeypatch.setenv(env_name, str(configured))
    assert getter() == configured


@pytest.mark.parametrize(
    ('getter_name', 'env_name'),
    [
        ('get_telegram_connect_timeout_seconds', 'TELEGRAM_CONNECT_TIMEOUT_SECONDS'),
        ('get_telegram_read_timeout_seconds', 'TELEGRAM_READ_TIMEOUT_SECONDS'),
        ('get_telegram_write_timeout_seconds', 'TELEGRAM_WRITE_TIMEOUT_SECONDS'),
        ('get_telegram_pool_timeout_seconds', 'TELEGRAM_POOL_TIMEOUT_SECONDS'),
    ],
)
@pytest.mark.parametrize('value', ['0', '-1', 'abc'])
def test_telegram_timeout_getters_require_positive_numbers(
    monkeypatch,
    getter_name,
    env_name,
    value,
):
    monkeypatch.setenv(env_name, value)
    getter = getattr(config, getter_name)

    with pytest.raises(
        RuntimeError,
        match=f'{env_name} must be a positive number',
    ):
        getter()


def test_crypto_signal_db_path_defaults_to_var_sqlite(monkeypatch):
    monkeypatch.delenv('CRYPTO_SIGNAL_DB_PATH', raising=False)
    monkeypatch.delenv('CRYPTO_SIGNAL_TEST_DB_PATH', raising=False)

    assert config.get_crypto_signal_db_path() == 'var/crypto_signal/crypto_signal.sqlite3'


def test_crypto_signal_db_path_uses_test_default_in_test_mode(monkeypatch):
    monkeypatch.delenv('CRYPTO_SIGNAL_DB_PATH', raising=False)
    monkeypatch.delenv('CRYPTO_SIGNAL_TEST_DB_PATH', raising=False)

    assert config.get_crypto_signal_db_path(
        runtime_mode=RuntimeMode.from_test_mode(True)
    ) == 'var/crypto_signal/crypto_signal.test.sqlite3'


def test_crypto_signal_db_path_allows_explicit_test_override(monkeypatch):
    monkeypatch.setenv('CRYPTO_SIGNAL_DB_PATH', 'var/crypto_signal/custom.sqlite3')
    monkeypatch.setenv(
        'CRYPTO_SIGNAL_TEST_DB_PATH',
        'var/crypto_signal/custom.test.sqlite3',
    )

    assert config.get_crypto_signal_db_path(
        runtime_mode=RuntimeMode.from_test_mode(True)
    ) == 'var/crypto_signal/custom.test.sqlite3'


def test_crypto_signal_recipient_id_defaults_to_crypto_admin(monkeypatch):
    monkeypatch.delenv('CRYPTO_SIGNAL_RECIPIENT_ID', raising=False)
    monkeypatch.setattr(config, 'get_telegram_crypto_admin_id', lambda: 'crypto-admin')

    assert config.get_crypto_signal_recipient_id() == 'crypto-admin'


def test_crypto_signal_tracked_universe_defaults(monkeypatch):
    monkeypatch.delenv('CRYPTO_SIGNAL_TRACKED_UNIVERSE', raising=False)

    assert config.get_crypto_signal_tracked_universe() == [
        ('BTC', 1),
        ('ETH', 1027),
        ('SOL', 5426),
    ]


def test_crypto_signal_tracked_universe_supports_explicit_coin_ids(monkeypatch):
    monkeypatch.setenv('CRYPTO_SIGNAL_TRACKED_UNIVERSE', 'BTC,TAO:22974')

    assert config.get_crypto_signal_tracked_universe() == [
        ('BTC', 1),
        ('TAO', 22974),
    ]


def test_crypto_signal_tracked_universe_rejects_unknown_symbol_without_id(
    monkeypatch,
):
    monkeypatch.setenv('CRYPTO_SIGNAL_TRACKED_UNIVERSE', 'TAO')

    with pytest.raises(
        RuntimeError,
        match='CRYPTO_SIGNAL_TRACKED_UNIVERSE unqualified symbols must use a known default id or the SYMBOL:ID form',
    ):
        config.get_crypto_signal_tracked_universe()


def test_crypto_signal_watchlist_defaults_to_empty(monkeypatch):
    monkeypatch.delenv('CRYPTO_SIGNAL_WATCHLIST', raising=False)

    assert config.get_crypto_signal_watchlist() == []


def test_crypto_signal_watchlist_supports_explicit_coin_ids(monkeypatch):
    monkeypatch.setenv('CRYPTO_SIGNAL_WATCHLIST', 'BTC,TAO:22974')

    assert config.get_crypto_signal_watchlist() == [
        ('BTC', 1),
        ('TAO', 22974),
    ]


def test_crypto_signal_watchlist_rejects_unknown_symbol_without_id(monkeypatch):
    monkeypatch.setenv('CRYPTO_SIGNAL_WATCHLIST', 'TAO')

    with pytest.raises(
        RuntimeError,
        match='CRYPTO_SIGNAL_WATCHLIST unqualified symbols must use a known default id or the SYMBOL:ID form',
    ):
        config.get_crypto_signal_watchlist()


def test_crypto_signal_dynamic_candidate_min_price_usd_defaults(monkeypatch):
    monkeypatch.delenv('CRYPTO_SIGNAL_DYNAMIC_CANDIDATE_MIN_PRICE_USD', raising=False)

    assert config.get_crypto_signal_dynamic_candidate_min_price_usd() == 0.0


def test_crypto_signal_dynamic_candidate_min_volume_24h_defaults(monkeypatch):
    monkeypatch.delenv(
        'CRYPTO_SIGNAL_DYNAMIC_CANDIDATE_MIN_VOLUME_24H',
        raising=False,
    )

    assert config.get_crypto_signal_dynamic_candidate_min_volume_24h() == 50_000_000.0


@pytest.mark.parametrize(
    ('getter_name', 'env_name'),
    [
        (
            'get_crypto_signal_dynamic_candidate_min_price_usd',
            'CRYPTO_SIGNAL_DYNAMIC_CANDIDATE_MIN_PRICE_USD',
        ),
        (
            'get_crypto_signal_dynamic_candidate_min_volume_24h',
            'CRYPTO_SIGNAL_DYNAMIC_CANDIDATE_MIN_VOLUME_24H',
        ),
    ],
)
def test_crypto_signal_dynamic_candidate_floors_reject_negative_values(
    monkeypatch,
    getter_name,
    env_name,
):
    monkeypatch.setenv(env_name, '-1')
    getter = getattr(config, getter_name)

    with pytest.raises(
        RuntimeError,
        match=f'{env_name} must be a positive number',
    ):
        getter()
