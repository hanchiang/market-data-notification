import pytest

from src.config import config


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
