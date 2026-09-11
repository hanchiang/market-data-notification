"""The spend ledger counts every attempt, and the ceiling refuses before the send.

The earlier ledger charged per call site and recorded 2 public requests for a
run that sent dozens (run 1, 2026-09-08). The property pinned here is the one
that closes that gap: the count is taken where the library client reserves
budget, so a retry, a refused request and a timeout are all attempts the
operator can see. A fake transport is scripted at the session, not the server,
so the client's own retry loop is what drives the meter.
"""
from datetime import datetime, timedelta, timezone

import pytest
from market_data_library.core.onchain.evm import (
    ALCHEMY_CU_COSTS,
    Endpoint,
    EvmBudgetError,
    EvmClient,
    EvmRateLimitError,
    RetryPolicy,
    alchemy_budget,
    public_rpc_budget,
)

from src.runtime.runtime_mode import RuntimeMode
from src.service.onchain import chain
from src.service.onchain.spend import (
    MeteredBudget,
    SpendCeilingReachedError,
    SpendLedger,
    SpendMeter,
)


async def _no_sleep(_seconds: float) -> None:
    return None


def _metered_alchemy(ledger: SpendLedger, **meter_options):
    budget = alchemy_budget(
        sleep=_no_sleep,
        random_uniform=lambda a, b: 0.0,
        retry_policy=RetryPolicy(max_attempts=3),
    )
    return ledger.budget_for(budget, bills_units=True, **meter_options)


class _Response:
    def __init__(self, status, text):
        self.status = status
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _ScriptedSession:
    """`aiohttp.ClientSession.post` stand-in: one canned (status, body) per call."""

    def __init__(self, script):
        self.script = list(script)
        self.posts = 0

    def post(self, url, data=None):
        self.posts += 1
        status, text = self.script.pop(0)
        return _Response(status, text)

    async def close(self):
        return None


def _client_with(session, budget, kind='alchemy'):
    client = EvmClient(Endpoint(kind=kind, url='https://example.invalid/v2/key'), budget)

    async def _session():
        return session

    client._ensure_session = _session  # type: ignore[method-assign]
    return client


OK_BODY = '[{"jsonrpc": "2.0", "id": 1, "result": "0x10"}]'


class TestEveryAttemptIsCounted:
    @pytest.mark.asyncio
    async def test_two_429s_then_a_200_count_as_three_attempts(self):
        """The whole point: a retry is an attempt the account was billed for."""
        ledger = SpendLedger()
        session = _ScriptedSession([(429, '{}'), (429, '{}'), (200, OK_BODY)])
        client = _client_with(session, _metered_alchemy(ledger))

        await client.block_number()

        meter = ledger.meters['alchemy']
        assert session.posts == 3
        assert meter.requests == 3
        assert meter.units == 3 * ALCHEMY_CU_COSTS['eth_blockNumber']

    @pytest.mark.asyncio
    async def test_a_request_that_fails_after_retries_is_still_counted(self):
        ledger = SpendLedger()
        session = _ScriptedSession([(429, '{}')] * 3)
        client = _client_with(session, _metered_alchemy(ledger))

        with pytest.raises(EvmRateLimitError):
            await client.block_number()

        assert ledger.meters['alchemy'].requests == 3

    @pytest.mark.asyncio
    async def test_the_public_endpoint_gets_requests_and_no_units(self):
        """It publishes no cost model, so a unit figure would be invented."""
        ledger = SpendLedger()
        budget = ledger.budget_for(
            public_rpc_budget(sleep=_no_sleep, random_uniform=lambda a, b: 0.0)
        )
        client = _client_with(_ScriptedSession([(200, OK_BODY)]), budget, kind='public')

        await client.block_number()

        assert ledger.snapshot() == {'requests': {'public': 1}, 'compute_units': {}}

    @pytest.mark.asyncio
    async def test_a_batch_too_large_for_any_window_is_refused_and_not_counted(self):
        """The library refuses it before a send, so it never reached the account."""
        ledger = SpendLedger()
        budget = _metered_alchemy(ledger)
        with pytest.raises(EvmBudgetError):
            await budget.reserve(budget.max_admissible_cost() + 1)
        assert 'alchemy' not in ledger.meters or ledger.meters['alchemy'].requests == 0


class TestCeiling:
    def test_the_meter_refuses_the_call_that_would_cross_the_ceiling(self):
        meter = SpendMeter(kind='alchemy', bills_units=True, ceiling_units=100)
        meter.admit(60)
        with pytest.raises(SpendCeilingReachedError) as info:
            meter.admit(41)
        assert meter.requests == 1
        assert meter.units == 60
        assert info.value.spent == 60 and info.value.ceiling == 100

    def test_exactly_reaching_the_ceiling_is_admitted(self):
        """`>` not `>=`: the ceiling is a spend the job may reach, not exceed."""
        meter = SpendMeter(kind='alchemy', bills_units=True, ceiling_units=100)
        meter.admit(60)
        meter.admit(40)
        assert meter.units == 100

    def test_month_to_date_from_earlier_runs_counts_against_the_ceiling(self):
        meter = SpendMeter(
            kind='alchemy', bills_units=True, ceiling_units=100, spent_before_run=90
        )
        with pytest.raises(SpendCeilingReachedError):
            meter.admit(11)

    def test_no_ceiling_means_no_refusal(self):
        meter = SpendMeter(kind='public')
        for _ in range(1000):
            meter.admit(1)
        assert meter.requests == 1000

    @pytest.mark.asyncio
    async def test_a_refusal_happens_before_the_window_is_touched(self):
        """Refuse-then-count, never count-then-refuse: a refused call did not
        consume window quota, and a ledger that said it did would make the
        next run wait for a request that was never sent."""
        ledger = SpendLedger()
        budget = _metered_alchemy(ledger, ceiling_units=10)
        acquired = []
        budget._inner.limiter.acquire = _record(acquired)  # type: ignore[attr-defined]
        with pytest.raises(SpendCeilingReachedError):
            await budget.reserve(26)
        assert acquired == []

    @pytest.mark.asyncio
    async def test_the_client_surfaces_the_refusal_without_sending(self):
        ledger = SpendLedger()
        session = _ScriptedSession([(200, OK_BODY)])
        client = _client_with(session, _metered_alchemy(ledger, ceiling_units=5))
        with pytest.raises(SpendCeilingReachedError):
            await client.block_number()
        assert session.posts == 0


def _record(sink):
    async def acquire(quota_cost=1):
        sink.append(quota_cost)

    return acquire


class TestOneMeterPerKind:
    def test_two_roles_on_the_archive_endpoint_share_one_meter(self, monkeypatch):
        """When the log plane is switched to the archive endpoint, both roles
        draw on the same account; two meters would let each spend the whole
        ceiling."""
        monkeypatch.setattr(
            chain, 'get_archive_endpoint',
            lambda: Endpoint(kind='alchemy', url='https://example.invalid/key'),
        )
        monkeypatch.setenv('ONCHAIN_LOG_ENDPOINT', 'archive')
        ledger = SpendLedger()
        live = RuntimeMode.from_test_mode(False)
        state = chain.state_role(live, ledger, alchemy_spent_this_month=7)
        logs = chain.log_role(live, ledger, alchemy_spent_this_month=7)
        assert state.budget.meter is logs.budget.meter
        assert state.budget.meter.spent_before_run == 7
        assert len(ledger.meters) == 1
        # The meter is the ceiling; the wrapped budget is the rate limiter.
        # Two budgets would each pace at the account's full window quota.
        assert state.budget is logs.budget
        assert state.budget._inner is logs.budget._inner
        assert state.budget.limiter is logs.budget.limiter

    def test_a_second_caller_with_different_options_is_refused_not_ignored(self):
        # The fail-open shape: something touches the meter first with the
        # defaults (an `add_requests`, a reordered role), then the archive
        # role asks for its ceiling and would silently get none.
        ledger = SpendLedger()
        ledger.add_requests('alchemy', 1)
        with pytest.raises(ValueError, match='different'):
            _metered_alchemy(ledger, ceiling_units=200)
        # And two ceilings for one account.
        ledger = SpendLedger()
        _metered_alchemy(ledger, ceiling_units=100)
        with pytest.raises(ValueError, match='different'):
            _metered_alchemy(ledger, ceiling_units=200)
        # Options that match, or none at all, are not a clash.
        assert _metered_alchemy(ledger, ceiling_units=100).meter.ceiling_units == 100
        ledger.add_requests('alchemy', 1)

    def test_a_ceiling_on_a_meter_that_does_not_bill_is_rejected(self):
        with pytest.raises(ValueError, match='bills units'):
            SpendMeter(kind='public', ceiling_units=10)

    def test_delegated_attributes_reach_the_wrapped_budget(self):
        budget = _metered_alchemy(SpendLedger())
        assert isinstance(budget, MeteredBudget)
        assert budget.endpoint_kind == 'alchemy'
        assert budget.cost_of(['eth_call']) == ALCHEMY_CU_COSTS['eth_call']


class TestSnapshot:
    def test_it_is_the_run_row_shape(self):
        ledger = SpendLedger()
        ledger.meter('alchemy', bills_units=True).admit(26)
        ledger.meter('public').admit(1)
        ledger.add_requests('dexscreener', 3)
        ledger.add_requests('blockscout', 0)
        assert ledger.snapshot() == {
            'requests': {'alchemy': 1, 'public': 1, 'dexscreener': 3},
            'compute_units': {'alchemy': 26},
        }

    def test_an_untouched_ledger_is_empty(self):
        assert SpendLedger().snapshot() == {'requests': {}, 'compute_units': {}}


class TestMonthToDate:
    """`units_spent_this_month` is the only thing that makes the ceiling
    monthly: a meter lives for one run."""

    def _run_started_at(self, repository, started_at, units):
        with repository.connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO onchain.run (job, started_at, outcome, spend_json) '
                'VALUES (%s, %s, %s, %s::jsonb)',
                (
                    'onchain.build',
                    started_at,
                    'ok',
                    '{"requests": {"alchemy": 1}, "compute_units": {"alchemy": %d}}' % units,
                ),
            )
        repository.commit()

    def test_it_sums_this_utc_month_and_ignores_last_month(self, onchain_repository):
        now = datetime.now(timezone.utc)
        this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month = this_month - timedelta(seconds=1)
        self._run_started_at(onchain_repository, this_month, 100)
        self._run_started_at(onchain_repository, now, 250)
        self._run_started_at(onchain_repository, last_month, 9999)
        assert onchain_repository.units_spent_this_month('alchemy') == 350

    def test_rows_without_the_kind_or_without_spend_count_nothing(self, onchain_repository):
        run_id = onchain_repository.start_run('onchain.build')
        onchain_repository.finish_run(run_id, outcome='ok', spend={'requests': {'public': 4}})
        onchain_repository.start_run('onchain.watch')
        assert onchain_repository.units_spent_this_month('alchemy') == 0
