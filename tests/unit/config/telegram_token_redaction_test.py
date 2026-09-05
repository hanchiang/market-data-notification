import importlib
import io
import logging

import pytest

from src.config import config

# Same shape as a real token (<bot id>:<35 chars>), not a real one.
_FAKE_TOKEN = '123456789:AAFakeTokenForTests_ABCDEFGHIJKLMNOP'


@pytest.fixture
def root_armed_at_info():
    """Reproduce production: the library's configure_logger() runs basicConfig(INFO).

    Without this the httpx logger inherits a default root of WARNING and the
    leak assertions below pass whether or not the redaction exists.
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
        f'https://api.telegram.org/bot{_FAKE_TOKEN}/sendMessage',
        'HTTP/1.1 200 OK',
    )


def test_token_never_reaches_a_handler(root_armed_at_info):
    """The leak is silent when it regresses, so pin the outcome, not the mechanism."""
    _emit_like_httpx(logging.getLogger('httpx'))
    assert _FAKE_TOKEN not in root_armed_at_info.getvalue()


def test_request_line_still_written_with_token_redacted(root_armed_at_info):
    """A scrub, not a level drop: the trace survives, only the secret goes."""
    _emit_like_httpx(logging.getLogger('httpx'))
    written = root_armed_at_info.getvalue()
    assert 'HTTP Request: POST https://api.telegram.org/bot<redacted>/sendMessage' in written
    assert '"HTTP/1.1 200 OK"' in written


def test_wrapped_httpx_error_logged_on_another_logger_is_redacted(root_armed_at_info):
    """telegram/request/_httpxrequest.py wraps httpx errors as NetworkError(str(err)) and
    our senders log that on the 'Telegram notification' logger, not on 'httpx'."""
    url = f'https://api.telegram.org/bot{_FAKE_TOKEN}/sendMessage'
    logging.getLogger('Telegram notification').error(
        f"exception: httpx.HTTPStatusError: Server error '502 Bad Gateway' for url '{url}'"
    )
    written = root_armed_at_info.getvalue()
    assert _FAKE_TOKEN not in written
    assert "for url 'https://api.telegram.org/bot<redacted>/sendMessage'" in written


def test_bare_token_without_bot_prefix_is_redacted(root_armed_at_info):
    """telegram/_bot.py:592 raises InvalidToken(f'The token `{self._token}` was rejected')."""
    logging.getLogger('anything').error('The token `%s` was rejected by the server.', _FAKE_TOKEN)
    written = root_armed_at_info.getvalue()
    assert _FAKE_TOKEN not in written
    assert 'The token `<redacted-token>` was rejected' in written


def test_ordinary_colon_text_is_left_alone(root_armed_at_info):
    """The bare-token pattern must not eat timestamps, ratios or short ids."""
    line = 'run at 12:34:56, ratio 123456:1, id 123456789:short'
    logging.getLogger('anything').info(line)
    assert line in root_armed_at_info.getvalue()


def test_httpx_info_is_not_suppressed_by_level(root_armed_at_info):
    """Raising the level was the old fix; the new one must not depend on it."""
    assert logging.getLogger('httpx').getEffectiveLevel() <= logging.INFO


def test_genuine_httpx_warnings_still_reach_a_handler(root_armed_at_info):
    logging.getLogger('httpx').warning('connection failed')
    assert 'connection failed' in root_armed_at_info.getvalue()


def test_reimport_keeps_one_redacting_factory(root_armed_at_info):
    """config is reloaded in tests and imported from four entrypoints; the factory is
    installed once and still redacts after a reload."""
    importlib.reload(config)
    assert getattr(logging.getLogRecordFactory(), 'marker', None) == 'redact-telegram-bot-token'
    _emit_like_httpx(logging.getLogger('httpx'))
    assert _FAKE_TOKEN not in root_armed_at_info.getvalue()
