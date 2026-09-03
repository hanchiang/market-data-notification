import importlib
import io
import logging

import pytest

from src.config import config


@pytest.fixture
def root_armed_at_info():
    """Reproduce production: the library's configure_logger() runs basicConfig(INFO).

    Without this the httpx logger inherits a default root of WARNING and every
    assertion below passes whether or not the guard exists.
    """
    root = logging.getLogger()
    previous_root_level = root.level
    previous_httpx_level = logging.getLogger('httpx').level
    root.setLevel(logging.INFO)
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root.addHandler(handler)
    try:
        importlib.reload(config)
        yield buf
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_root_level)
        logging.getLogger('httpx').setLevel(previous_httpx_level)


def test_httpx_request_logging_is_silenced(root_armed_at_info):
    """The token leak is silent when it regresses, so pin the mechanism.

    python-telegram-bot puts the bot token in the request URL and httpx logs every
    request URL at INFO (httpx/_client.py:1013 and :1729, both on the "httpx"
    logger). Importing src.config.config must leave that logger above INFO.
    """
    assert logging.getLogger('httpx').getEffectiveLevel() > logging.INFO


def test_httpx_info_record_does_not_reach_a_handler(root_armed_at_info):
    """Level alone is not the contract -- what matters is that nothing is written."""
    httpx_logger = logging.getLogger('httpx')
    httpx_logger.info(
        'HTTP Request: POST '
        'https://api.telegram.org/bot123456:NOT-A-REAL-TOKEN/sendMessage'
    )
    assert 'NOT-A-REAL-TOKEN' not in root_armed_at_info.getvalue()


def test_genuine_httpx_warnings_still_reach_a_handler(root_armed_at_info):
    """A false positive kills the fix by the other route: this is targeted, not blanket."""
    logging.getLogger('httpx').warning('connection failed')
    assert 'connection failed' in root_armed_at_info.getvalue()
