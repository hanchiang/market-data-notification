import pytest

from src.config import config


def test_get_selenium_server_host_returns_host_for_remote_mode(monkeypatch):
    monkeypatch.setenv('SELENIUM_REMOTE_MODE', 'true')
    monkeypatch.setenv('SELENIUM_SERVER_HOST', 'http://chrome:4444')

    assert config.get_selenium_server_host() == 'http://chrome:4444'


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
