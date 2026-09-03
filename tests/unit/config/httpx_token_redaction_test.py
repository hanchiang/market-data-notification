import importlib
import io
import logging

import pytest

from src.config import config


@pytest.fixture
def root_armed_at_info():
    """Reproduce production: the library's configure_logger() runs basicConfig(INFO).

    Without this the httpx logger inherits a default root of WARNING and the
    leak assertions below pass whether or not the filter exists.
    """
    root = logging.getLogger()
    previous_root_level = root.level
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


def _emit_like_httpx(logger: logging.Logger) -> None:
    # httpx/_client.py:1013 and :1729 -- URL arrives as a %-arg, not pre-formatted.
    logger.info(
        'HTTP Request: %s %s "%s"',
        'POST',
        'https://api.telegram.org/bot123456:NOT-A-REAL-TOKEN/sendMessage',
        'HTTP/1.1 200 OK',
    )


def test_token_never_reaches_a_handler(root_armed_at_info):
    """The leak is silent when it regresses, so pin the outcome, not the mechanism."""
    _emit_like_httpx(logging.getLogger('httpx'))
    assert 'NOT-A-REAL-TOKEN' not in root_armed_at_info.getvalue()


def test_request_line_still_written_with_token_redacted(root_armed_at_info):
    """This is a filter, not a level drop: the trace survives, only the secret goes."""
    _emit_like_httpx(logging.getLogger('httpx'))
    written = root_armed_at_info.getvalue()
    assert 'HTTP Request: POST https://api.telegram.org/bot<redacted>/sendMessage' in written
    assert '"HTTP/1.1 200 OK"' in written


def test_httpx_info_is_not_suppressed_by_level(root_armed_at_info):
    """Raising the level was the old fix; the new one must not depend on it."""
    assert logging.getLogger('httpx').getEffectiveLevel() <= logging.INFO


def test_genuine_httpx_warnings_still_reach_a_handler(root_armed_at_info):
    logging.getLogger('httpx').warning('connection failed')
    assert 'connection failed' in root_armed_at_info.getvalue()


def test_reimport_does_not_stack_filters(root_armed_at_info):
    """config is reloaded in tests and imported from four entrypoints; one filter, always."""
    importlib.reload(config)
    filters = [f for f in logging.getLogger('httpx').filters if getattr(f, 'marker', None) == 'redact-telegram-bot-token']
    assert len(filters) == 1
