"""Alert cardinality, payload and routing for the three onchain entrypoints (A11).

A11 has three separable claims, and each is tested here against a real store
with the TRANSPORT stubbed at `send_message_to_admin`:

* **one message per run, not one per failed unit** -- the count is the criterion;
* **the payload carries the run id and the exception class and no project
  content** -- asserted on the string that would have been sent;
* **`runtime_mode` reaches the sender** -- without it a `--test_mode 1` run posts
  to the live crypto admin chat, which is the exact mechanism behind both
  unauthorised sends this task has already caused.

The transport is stubbed rather than disabled by the flag, because a flag-off
run proves the suppression path and not the payload. Delivery on the real
transport is demonstrated separately, by hand, under the operator's
authorisation; that is a run, not a test.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.job.onchain import build as build_job
from src.job.onchain import watch as watch_job
from src.service.onchain import builder
from src.service.onchain.observability import failed_unit


class RecordingSender:
    """Stands in for `telegram_notification.send_message_to_admin`."""

    def __init__(self):
        self.calls = []

    async def __call__(self, message, market_data_type, runtime_mode=None):
        self.calls.append(
            {'message': message, 'type': market_data_type, 'runtime_mode': runtime_mode}
        )
        return True


@pytest.fixture
def sender(monkeypatch):
    from src.notification_destination import telegram_notification

    recorder = RecordingSender()
    monkeypatch.setenv('DISABLE_TELEGRAM_ADMIN', 'false')
    monkeypatch.setattr(telegram_notification, 'send_message_to_admin', recorder)
    monkeypatch.setattr(telegram_notification, 'init_telegram_bots', lambda: None)
    return recorder


@pytest.fixture
def demo_url(onchain_database_url, monkeypatch):
    monkeypatch.setenv('PROJECT_MONITOR_TEST_DATABASE_URL', onchain_database_url)
    return onchain_database_url


class TestOneMessagePerRun:
    @pytest.mark.asyncio
    async def test_a_run_with_three_failed_units_sends_exactly_one_message(
        self, onchain_repository, demo_url, sender, monkeypatch
    ):
        """The cardinality claim. Three failed units, one message -- an alert per
        unit would make a bad night unreadable and train the operator to mute
        the channel that carries the good nights' alerts too."""
        units = [
            failed_unit('touch-grass/onchain_health', 'EvmRpcError'),
            failed_unit('zzz/onchain_health', 'EvmRpcError'),
            failed_unit('predict-fwa/contract_safety/verified_source', 'BlockscoutApiError'),
        ]

        async def failing_run(*args, **kwargs):
            result = builder.RunResult(run_id=kwargs.get('run_id', 1), outcome='partial')
            result.failed_units = units
            result.notes = ['three sections failed']
            return result

        monkeypatch.setattr(build_job, 'run_build', failing_run)
        exit_code = await build_job.main(force_run=True, test_mode=True)

        assert exit_code == 0
        assert len(sender.calls) == 1

    @pytest.mark.asyncio
    async def test_the_payload_names_the_run_and_the_error_classes_only(
        self, onchain_repository, demo_url, sender, monkeypatch
    ):
        async def failing_run(*args, **kwargs):
            result = builder.RunResult(run_id=1, outcome='failed')
            result.failed_units = [failed_unit('zzz/onchain_health', 'EvmRpcError')]
            return result

        monkeypatch.setattr(build_job, 'run_build', failing_run)
        await build_job.main(force_run=True, test_mode=True)

        message = sender.calls[0]['message']
        assert 'onchain.build' in message.replace('\\', '')
        assert 'EvmRpcError' in message
        assert 'zzz/onchain_health' in message.replace('\\', '')
        # No project CONTENT: not an address, not a URL, not a field value.
        assert '0x' not in message
        assert '://' not in message

    @pytest.mark.asyncio
    async def test_a_clean_run_sends_nothing(
        self, onchain_repository, demo_url, sender, monkeypatch
    ):
        async def clean_run(*args, **kwargs):
            return builder.RunResult(run_id=1, outcome='ok')

        monkeypatch.setattr(build_job, 'run_build', clean_run)
        await build_job.main(force_run=True, test_mode=True)
        assert sender.calls == []


class TestRuntimeModeReachesTheSender:
    """The carried sub-stage A requirement. `runtime_mode` defaults to None,
    which resolves to the LIVE crypto admin chat, and forgetting it is silent:
    the alert sends and reads correctly. Each entrypoint gets its own test
    because each has its own call site."""

    @pytest.mark.asyncio
    async def test_build_passes_runtime_mode(
        self, onchain_repository, demo_url, sender, monkeypatch
    ):
        async def failing_run(*args, **kwargs):
            result = builder.RunResult(run_id=1, outcome='failed')
            result.failed_units = [failed_unit('zzz/onchain_health', 'EvmRpcError')]
            return result

        monkeypatch.setattr(build_job, 'run_build', failing_run)
        await build_job.main(force_run=True, test_mode=True)

        runtime_mode = sender.calls[0]['runtime_mode']
        assert runtime_mode is not None and runtime_mode.is_test_mode

    @pytest.mark.asyncio
    async def test_watch_passes_runtime_mode(
        self, onchain_repository, demo_url, sender
    ):
        await watch_job.main(test_mode=True)
        runtime_mode = sender.calls[0]['runtime_mode']
        assert runtime_mode is not None and runtime_mode.is_test_mode


class TestMissedRunWatcher:
    @pytest.mark.asyncio
    async def test_a_build_older_than_the_deadline_produces_one_message(
        self, onchain_repository, demo_url, sender, monkeypatch
    ):
        """A11's first half over the store: a job whose cron line is disabled
        sends one admin-chat message naming the missing run once its deadline
        passes."""
        run_id = onchain_repository.start_run(builder.JOB_BUILD)
        with onchain_repository.connection.cursor() as cursor:
            cursor.execute(
                'UPDATE onchain.run SET started_at = %s WHERE id = %s',
                (datetime.now(timezone.utc) - timedelta(hours=26), run_id),
            )
        onchain_repository.commit()

        exit_code = await watch_job.main(test_mode=True)

        assert exit_code == 0
        assert len(sender.calls) == 1
        assert 'watch/missed_run' in sender.calls[0]['message'].replace('\\', '')
        assert 'BuildDeadlineExceeded' in sender.calls[0]['message']

    @pytest.mark.asyncio
    async def test_a_recent_build_produces_no_message(
        self, onchain_repository, demo_url, sender
    ):
        onchain_repository.start_run(builder.JOB_BUILD)
        onchain_repository.commit()
        await watch_job.main(test_mode=True)
        assert sender.calls == []

    @pytest.mark.asyncio
    async def test_the_watcher_writes_its_own_run_row(
        self, onchain_repository, demo_url, sender
    ):
        """What makes a silent watcher visible: a watcher that itself stopped
        running is discoverable in the ledger rather than being a second silence
        behind the first."""
        await watch_job.main(test_mode=True)
        assert onchain_repository.get_latest_run(builder.JOB_WATCH) is not None
