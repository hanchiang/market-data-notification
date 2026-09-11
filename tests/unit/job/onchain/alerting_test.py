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
from src.runtime.runtime_mode import RuntimeMode
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
        exit_code = await build_job.main(test_mode=True)

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
        await build_job.main(test_mode=True)

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
        await build_job.main(test_mode=True)
        assert sender.calls == []


class TestConsecutiveRuns:
    """A11's second half, in its own words: "a collector that fails on three
    consecutive runs" sends "three messages, one per run and not one per failed
    unit". The per-run half was pinned; the ACROSS-runs half was not, and the two
    fail differently. A cache that alerted once per unit-and-error-class, or a
    module-level "already alerted" flag, satisfies every single-run test in this
    file and silently swallows nights two and three -- which is the failure mode
    that matters, because a collector broken for one night is noise and one
    broken for three is the signal.
    """

    @pytest.mark.asyncio
    async def test_three_consecutive_failing_runs_send_three_messages(
        self, onchain_repository, demo_url, sender, monkeypatch
    ):
        units = [failed_unit('zzz/onchain_health', 'EvmRpcError')]

        async def failing_run(*args, **kwargs):
            result = builder.RunResult(run_id=kwargs.get('run_id', 1), outcome='partial')
            result.failed_units = list(units)
            return result

        monkeypatch.setattr(build_job, 'run_build', failing_run)
        for _ in range(3):
            assert await build_job.main(test_mode=True) == 0

        assert len(sender.calls) == 3
        # Each names its OWN run, so three identical messages -- which would also
        # be "three" -- do not pass.
        run_ids = {
            call['message'].replace('\\', '').split(' run ')[1].split()[0]
            for call in sender.calls
        }
        assert len(run_ids) == 3

    @pytest.mark.asyncio
    async def test_a_run_that_recovers_between_two_failures_sends_only_its_own(
        self, onchain_repository, demo_url, sender, monkeypatch
    ):
        """The complement: alerting must be per-run state, not sticky. A latch
        set on the first failure would keep alerting through the clean night."""
        outcomes = iter(['partial', 'ok', 'partial'])

        async def run(*args, **kwargs):
            outcome = next(outcomes)
            result = builder.RunResult(run_id=kwargs.get('run_id', 1), outcome=outcome)
            if outcome != 'ok':
                result.failed_units = [failed_unit('zzz/onchain_health', 'EvmRpcError')]
            return result

        monkeypatch.setattr(build_job, 'run_build', run)
        for _ in range(3):
            await build_job.main(test_mode=True)

        assert len(sender.calls) == 2


def test_the_alert_has_exactly_two_call_sites_in_the_product():
    """The cardinality tests above stub `run_build`, so they see only the send at
    `main()`'s end. A second `send_run_alert` added inside the build loop -- per
    project, or per failed section -- would be invisible to every one of them
    while turning a six-failure night into seven messages.

    Enumerating the call sites is the check that does not depend on which code
    path a test happens to drive. If a third site is ever legitimate, this list
    is where the decision gets recorded.
    """
    import collections
    import pathlib

    src = pathlib.Path(build_job.__file__).resolve().parents[3] / 'src'
    sites = collections.Counter(
        str(path.relative_to(src))
        for path in src.rglob('*.py')
        for line in path.read_text().splitlines()
        if 'send_run_alert(' in line
        and not line.lstrip().startswith(('#', 'def ', 'async def ', 'from ', 'import '))
    )
    # Counted per file, not per line: a line number would redden on any edit
    # above it, which trains the next reader to update the list without reading
    # it -- the opposite of what a tripwire is for.
    assert dict(sites) == {
        'job/onchain/build.py': 1,
        'job/onchain/watch.py': 1,
    }, dict(sites)


def test_no_collector_reaches_the_telegram_transport_directly():
    """F6 (test round 1): the tripwire above only counts `send_run_alert(`.

    A collector under `src/service/onchain` calling
    `telegram_notification.send_message_to_admin` or `send_alert_to_telegram`
    directly -- instead of going through the one alert at `main()`'s end --
    would add a message per unit invisibly, since every cardinality test in
    this file stubs `run_build` and never sees inside it. `observability.py` is
    the one sanctioned call site (`send_run_alert`'s own body) and is excluded.
    """
    import pathlib

    src = pathlib.Path(build_job.__file__).resolve().parents[3] / 'src'
    hits = []
    for package in ('service/onchain', 'job/onchain'):
        for path in (src / package).rglob('*.py'):
            if path.name == 'observability.py':
                continue
            for line in path.read_text().splitlines():
                stripped = line.lstrip()
                if stripped.startswith(('#', 'def ', 'async def ', 'from ', 'import ')):
                    continue
                if 'send_message_to_admin(' in line or 'send_alert_to_telegram(' in line:
                    hits.append(f'{path.relative_to(src)}: {stripped}')
    assert hits == []


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
        await build_job.main(test_mode=True)

        runtime_mode = sender.calls[0]['runtime_mode']
        assert runtime_mode is not None and runtime_mode.is_test_mode

    @pytest.mark.asyncio
    async def test_watch_passes_runtime_mode(
        self, onchain_repository, demo_url, sender
    ):
        await watch_job.main(test_mode=True)
        runtime_mode = sender.calls[0]['runtime_mode']
        assert runtime_mode is not None and runtime_mode.is_test_mode


class TestOneChainPerRun:
    """One run pins one block, so it cannot honestly span chains.

    The registry file and the schema both admit several. Pinning the first
    chain's head and then reading a second chain's state at that height would
    produce a dossier of numbers from no particular moment -- silently, since
    both chains have a block of that number.
    """

    @pytest.mark.asyncio
    async def test_projects_on_two_chains_are_refused_rather_than_pinned_to_one(
        self, onchain_repository, demo_url, monkeypatch, onchain_registry_payload
    ):
        from src.service.onchain import builder as builder_module
        from src.service.onchain.registry import parse_registry

        payload = onchain_registry_payload
        payload['chains'] = list(payload['chains']) + [
            dict(payload['chains'][0], chain_id=9999, key='other',
                 display_name='Other', dexscreener_slug='other')
        ]
        payload['projects'] = list(payload['projects']) + [
            {'key': 'other-project', 'display_name': 'Other', 'chain_id': 9999,
             'archetype': 'launchpad-fixed-supply', 'pool_ref': '0x' + 'cd' * 20,
             'sources': []}
        ]
        registry = parse_registry(payload)
        monkeypatch.setattr(build_job, 'load_registry', lambda _path: registry)
        monkeypatch.setattr(
            build_job, 'upsert_registry', lambda repo, reg: {
                key: repo.upsert_entity(level='project', key=f'project:{key}')
                for key in reg.projects
            }
        )
        monkeypatch.setattr(
            builder_module, 'select_projects', lambda reg, key: list(reg.projects.values())
        )
        with pytest.raises(build_job.MultiChainRunError, match='cannot span chains'):
            await build_job.run_build(
                onchain_repository, 1,
                runtime_mode=RuntimeMode.from_test_mode(True), project_key=None,
            )


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
        # Names the WATCHED job ('build'), not the watcher itself (E-1, test
        # stage round 1): the watcher's own run id is already in the "run N"
        # line, so the unit's project segment is the one place the message can
        # say which job's cron line went silent.
        assert 'build/missed_run' in sender.calls[0]['message'].replace('\\', '')
        assert 'BuildDeadlineExceeded' in sender.calls[0]['message']
        # Post-gate ruling, E-1: the alert also names the deadline, over the
        # real transport-adjacent path (watch_job.main, not evaluate()
        # called directly -- see watch_test.TestRenderedAlertCarriesTheDeadline
        # for the exact-string, mutation-proven guard).
        assert '(25h)' in sender.calls[0]['message'].replace('\\', '')

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


class TestCeilingHitIsRecordedAndAlertedOnce:
    """The cost gate: skip the call, record the skip, alert once per run.

    The refusal is raised from the metered budget before any send, so the run
    that hits it has spent nothing past the ceiling; what must survive is the
    record -- the run row carries the spend the run DID make and the alert
    names the class -- and the cardinality: one message, however many
    sections the refusal took down.
    """

    @pytest.mark.asyncio
    async def test_a_run_refused_while_pinning_writes_its_spend_and_alerts_once(
        self, onchain_repository, demo_url, sender, monkeypatch
    ):
        from src.service.onchain.spend import SpendCeilingReachedError

        async def refused_run(*args, ledger=None, **kwargs):
            # What a real run does before the refusal: a few attempts land on
            # the meter, then the next one crosses the line.
            ledger.meter('alchemy', bills_units=True).admit(20)
            raise SpendCeilingReachedError('alchemy', spent=20, cost=26, ceiling=40)

        monkeypatch.setattr(build_job, 'run_build', refused_run)
        exit_code = await build_job.main(test_mode=True)

        run = onchain_repository.get_latest_run(builder.JOB_BUILD)
        assert exit_code == 1
        assert run['outcome'] == 'failed'
        assert 'SpendCeilingReachedError' in run['notes']
        assert run['spend_json'] == {'requests': {'alchemy': 1}, 'compute_units': {'alchemy': 20}}
        assert len(sender.calls) == 1
        # The alert prints units, not notes: a run refused while pinning has
        # no section unit, so the run itself is the unit, and the class is
        # what tells the operator this is the ceiling and not an outage.
        assert 'SpendCeilingReachedError' in sender.calls[0]['message']
        assert run['failed_units_json'] == [failed_unit('run/setup', 'SpendCeilingReachedError')]

    @pytest.mark.asyncio
    async def test_a_refusal_inside_a_section_is_that_sections_error_class(
        self, onchain_repository, demo_url, sender, monkeypatch
    ):
        """Mid-run, the builder's per-section failure unit carries it (A3), so
        the build is `partial` with the skip named, not lost."""

        units = [failed_unit('zzz/onchain_health', 'SpendCeilingReachedError')]

        async def partial_run(*args, ledger=None, **kwargs):
            ledger.meter('alchemy', bills_units=True).admit(26)
            result = kwargs['result']
            result.outcome = 'partial'
            result.failed_units.extend(units)
            return result

        monkeypatch.setattr(build_job, 'run_build', partial_run)
        await build_job.main(test_mode=True)

        run = onchain_repository.get_latest_run(builder.JOB_BUILD)
        assert run['spend_json']['compute_units'] == {'alchemy': 26}
        assert len(sender.calls) == 1
        assert 'SpendCeilingReachedError' in sender.calls[0]['message']
        assert run['failed_units_json'] == units

    @pytest.mark.asyncio
    async def test_run_build_draws_its_roles_from_the_callers_ledger_and_result(
        self, onchain_repository, monkeypatch
    ):
        """The two objects `main` owns must be the ones `run_build` writes to,
        or a raise mid-run snapshots an empty ledger and an empty notes list
        and the row looks like an outage rather than a ceiling."""
        from src.service.onchain import chain as chain_module
        from src.service.onchain.spend import SpendLedger

        async def refuse_to_pin(*args, **kwargs):
            raise RuntimeError('stop before any network read')

        monkeypatch.setattr(chain_module, 'pin_block_and_window', refuse_to_pin)
        ledger = SpendLedger()
        result = builder.RunResult(run_id=1, outcome='failed')
        with pytest.raises(RuntimeError, match='stop before'):
            await build_job.run_build(
                onchain_repository, 1,
                runtime_mode=RuntimeMode.from_test_mode(True), project_key=None,
                ledger=ledger, result=result,
            )
        # Both roles were built on THIS ledger (test mode: both public)...
        assert list(ledger.meters) == ['public']
        # ...and the routing note landed on THIS result before the raise.
        assert any(note.startswith('state via public, logs via public') for note in result.notes)
