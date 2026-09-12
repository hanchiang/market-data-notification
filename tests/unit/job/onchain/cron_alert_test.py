"""The cron wrapper's alert path: what the operator's chat receives when the
build never ran, exercised with recorders instead of a transport.

Expected messages are LITERAL strings, not recomputed through `format_alert`:
a review of the first version found `--detail 60` rendering as `(60h)` for a
60-minute timeout, invisible to an oracle that used the function under test.
"""
import asyncio

import pytest

import src.notification_destination.telegram_notification as telegram_notification
from src.job.onchain import cron_alert
from src.runtime.runtime_mode import RuntimeMode
from src.service.onchain.observability import AlertPayloadError
from src.util.my_telegram import escape_markdown


@pytest.fixture
def recorder(monkeypatch):
    calls = []

    async def record_send(message, market_data_type, runtime_mode=None):
        calls.append((message, market_data_type, runtime_mode))
        return object()

    monkeypatch.setenv('DISABLE_TELEGRAM_ADMIN', 'false')
    monkeypatch.setattr(telegram_notification, 'init_telegram_bots', lambda: None)
    monkeypatch.setattr(telegram_notification, 'send_message_to_admin', record_send)
    return calls


class TestCronAlert:
    def test_the_chat_gets_the_unit_the_reason_and_the_hours_and_nothing_free_text(
        self, recorder, capsys
    ):
        rc = asyncio.run(cron_alert.main(
            job='onchain.build', unit='cron/build', reason='BuildTimeout', detail=1,
        ))
        assert rc == 0
        assert capsys.readouterr().out.strip() == 'delivered'
        message, _, runtime_mode = recorder[0]
        assert message == escape_markdown(
            'onchain onchain.build run unknown\n- cron/build: BuildTimeout (1h)'
        )
        assert runtime_mode == RuntimeMode.from_test_mode(False)
        assert runtime_mode.use_dev_telegram is False
        assert '/home/' not in message

    def test_no_detail_renders_no_suffix(self, recorder):
        asyncio.run(cron_alert.main(job='onchain.build', unit='cron/build', reason='LockHeld'))
        message, _, _ = recorder[0]
        assert message == escape_markdown('onchain onchain.build run unknown\n- cron/build: LockHeld')

    def test_test_mode_reaches_the_sender_so_it_can_redirect_to_the_dev_chat(self, recorder):
        asyncio.run(cron_alert.main(
            job='onchain.watch', unit='watch/cron', reason='WatchTimeout', test_mode=True,
        ))
        _, _, runtime_mode = recorder[0]
        assert runtime_mode == RuntimeMode.from_test_mode(True)
        assert runtime_mode.use_dev_telegram is True

    def test_a_message_shaped_reason_is_refused_before_any_send(self, recorder):
        """The wrapper passes an exception-class-shaped word; a shell that
        passed `$*` would be stopped here, not in the chat."""
        with pytest.raises(AlertPayloadError):
            asyncio.run(cron_alert.main(
                job='onchain.build', unit='cron/build',
                reason='FAILED exit=1 -- see /home/han/onchain-data/logs',
            ))
        assert recorder == []

    def test_a_path_shaped_job_is_refused_before_it_names_a_log_file(self, recorder):
        with pytest.raises(ValueError):
            asyncio.run(cron_alert.main(job='../../x', unit='cron/build', reason='LockHeld'))
        assert recorder == []

    def test_a_send_the_sender_withholds_still_exits_zero(self, monkeypatch, recorder, capsys):
        seen = []

        async def withhold(message, market_data_type, runtime_mode=None):
            seen.append(message)
            return None

        monkeypatch.setattr(telegram_notification, 'send_message_to_admin', withhold)
        rc = asyncio.run(cron_alert.main(job='onchain.build', unit='cron/build', reason='LockHeld'))
        assert rc == 0
        assert len(seen) == 1
        assert capsys.readouterr().out.strip() == 'not delivered'


class TestCommandLine:
    def test_the_shell_shape_the_wrapper_uses_parses(self, monkeypatch, recorder):
        import runpy
        import sys

        monkeypatch.setattr(sys, 'argv', [
            'cron_alert.py', '--job', 'onchain.build', '--unit', 'cron/build',
            '--reason', 'BuildTimeout', '--detail', '1',
        ])
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_module('src.job.onchain.cron_alert', run_name='__main__')
        assert exit_info.value.code == 0
        message, _, _ = recorder[0]
        assert message == escape_markdown(
            'onchain onchain.build run unknown\n- cron/build: BuildTimeout (1h)'
        )

    def test_reason_is_required(self, monkeypatch):
        import runpy
        import sys

        monkeypatch.setattr(sys, 'argv', ['cron_alert.py', '--job', 'onchain.build'])
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_module('src.job.onchain.cron_alert', run_name='__main__')
        assert exit_info.value.code == 2
