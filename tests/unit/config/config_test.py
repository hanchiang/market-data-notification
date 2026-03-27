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


def test_get_use_tradingview_dev_redis_keys_defaults_false(monkeypatch):
    monkeypatch.delenv('USE_TRADINGVIEW_DEV_REDIS_KEYS', raising=False)

    assert config.get_use_tradingview_dev_redis_keys() is False


def test_get_use_tradingview_dev_redis_keys_reads_true(monkeypatch):
    monkeypatch.setenv('USE_TRADINGVIEW_DEV_REDIS_KEYS', 'true')

    assert config.get_use_tradingview_dev_redis_keys() is True
