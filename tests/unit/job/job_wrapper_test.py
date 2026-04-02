import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.job.job_wrapper import JobWrapper
from src.runtime.runtime_mode import RuntimeMode
from src.type.market_data_type import MarketDataType


class RecordingSender:
    def __init__(self, job: "DummyJob"):
        self.job = job

    async def start(self):
        self.job.sender_runtime_modes.append(self.job.runtime_mode)
        return self.job.runtime_mode


class DummyJob(JobWrapper):
    def __init__(self):
        super().__init__()
        self.should_run_runtime_modes = []
        self.sender_runtime_modes = []

    def should_run(self, runtime_mode: RuntimeMode | None = None) -> bool:
        self.should_run_runtime_modes.append(runtime_mode)
        return False if runtime_mode is None else runtime_mode.bypass_schedule

    @property
    def market_data_type(self) -> MarketDataType:
        return MarketDataType.STOCKS

    @property
    def message_senders(self):
        return [RecordingSender(self)]


@pytest.mark.asyncio
async def test_job_wrapper_start_builds_runtime_mode_from_test_mode_flag(monkeypatch):
    job = DummyJob()

    monkeypatch.setattr(
        'src.job.job_wrapper.argparse.ArgumentParser.parse_args',
        lambda _self: SimpleNamespace(force_run=0, test_mode=1),
    )
    monkeypatch.setattr(
        'src.job.job_wrapper.TimeTrackerContext',
        lambda _label: contextlib.nullcontext(),
    )
    monkeypatch.setattr('src.job.job_wrapper.init_telegram_bots', Mock())
    monkeypatch.setattr('src.job.job_wrapper.Redis.start_redis', AsyncMock())
    monkeypatch.setattr('src.job.job_wrapper.Redis.stop_redis', AsyncMock())
    monkeypatch.setattr('src.job.job_wrapper.Dependencies.build', AsyncMock())
    monkeypatch.setattr('src.job.job_wrapper.Dependencies.cleanup', AsyncMock())

    result = await job.start()

    expected_runtime_mode = RuntimeMode.from_test_mode(True)
    assert result == [expected_runtime_mode]
    assert job.runtime_mode == expected_runtime_mode
    assert job.should_run_runtime_modes == [expected_runtime_mode]
    assert job.sender_runtime_modes == [expected_runtime_mode]


@pytest.mark.asyncio
async def test_job_wrapper_start_skips_work_when_not_scheduled(monkeypatch):
    job = DummyJob()

    monkeypatch.setattr(
        'src.job.job_wrapper.argparse.ArgumentParser.parse_args',
        lambda _self: SimpleNamespace(force_run=0, test_mode=0),
    )
    init_telegram_bots = Mock()
    start_redis = AsyncMock()
    build_dependencies = AsyncMock()

    monkeypatch.setattr('src.job.job_wrapper.init_telegram_bots', init_telegram_bots)
    monkeypatch.setattr('src.job.job_wrapper.Redis.start_redis', start_redis)
    monkeypatch.setattr('src.job.job_wrapper.Dependencies.build', build_dependencies)

    result = await job.start()

    assert result is None
    assert job.should_run_runtime_modes == [RuntimeMode.from_test_mode(False)]
    assert job.sender_runtime_modes == []
    init_telegram_bots.assert_not_called()
    start_redis.assert_not_awaited()
    build_dependencies.assert_not_awaited()
