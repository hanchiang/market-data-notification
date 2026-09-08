"""Fixtures for the onchain dossier tests.

At `tests/unit/` rather than beside the service tests, because the job tests
(`tests/unit/job/onchain/`) drive the same store through the entrypoints and a
second copy of the truncate list is how one of them silently stops truncating a
table the other added.

Store tests run against a **real Postgres**, never a substitute, and **fail
loudly rather than skip** when one is absent -- the project monitor's rule, for
the same reason: a skip would make the store's coverage invisible in a green run.
The schema uses `jsonb`, `numeric(78,0)`, `bigint[]`, advisory locks and
`ON CONFLICT`, none of which a SQLite stand-in would exercise the same way.

The connection string is the monitor's own test database, because the dossier's
schema lives inside the monitor's database by design (one store, one schema per
concern). `PROJECT_MONITOR_TEST_DATABASE_URL` therefore selects both suites'
server, and it must not be pointed at the operator database.
"""
import logging
import os
from pathlib import Path

import psycopg
import pytest

from src.service.onchain.repository import OnchainRepository


def _assert_no_root_handler_writes_under_home() -> None:
    """The F1 guard: `tests/unit/job/onchain/alerting_test.py` drives the real
    `build.main()`/`watch.main()` entrypoints, which install a
    `TimedRotatingFileHandler` on the ROOT logger at `ONCHAIN_LOG_DIR` (default
    the operator's `~/onchain-data/logs/onchain/` -- the exact directory A12's
    runbook procedure greps by run id). Without `_isolate_onchain_log_dir`
    below, every test run appends real files' worth of test run ids into that
    directory, permanently interleaving them with the operator's own runs.

    Checked at session teardown rather than only trusted from the env-var
    override, because the override only works for a caller that goes through
    `get_log_dir()` -- a call site that hardcodes the default path (or a future
    job that reads `Path.home()` directly) would still write home and this is
    the check that would catch it.
    """
    home = Path.home().resolve()
    for handler in logging.getLogger().handlers:
        base_filename = getattr(handler, 'baseFilename', None)
        if base_filename is None:
            continue
        if home in Path(base_filename).resolve().parents:
            pytest.fail(
                f'root logger handler {handler!r} writes under the operator\'s '
                f'home directory ({base_filename}); ONCHAIN_LOG_DIR must stay '
                'overridden for the whole test session'
            )


@pytest.fixture(scope='session', autouse=True)
def _isolate_onchain_log_dir(tmp_path_factory):
    """Session-scoped so every onchain job entrypoint call in the suite --
    however deep, however many tests -- resolves `ONCHAIN_LOG_DIR` to a scratch
    directory instead of the operator's real one. `configure_job_logging` is
    idempotent per job name (it returns the handler already installed for that
    job rather than opening a second one), so setting the env var once before
    the session's first call is what makes every later call in the session
    share this directory rather than falling back to the default.
    """
    directory = tmp_path_factory.mktemp('onchain-logs')
    previous = os.environ.get('ONCHAIN_LOG_DIR')
    os.environ['ONCHAIN_LOG_DIR'] = str(directory)
    try:
        yield directory
    finally:
        _assert_no_root_handler_writes_under_home()
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, '_onchain_job', None):
                root.removeHandler(handler)
                handler.close()
        if previous is None:
            os.environ.pop('ONCHAIN_LOG_DIR', None)
        else:
            os.environ['ONCHAIN_LOG_DIR'] = previous

DEFAULT_TEST_DATABASE_URL = (
    'postgresql://postgres:devpass@127.0.0.1:55432/project_monitor_test'
)

# Truncate order is irrelevant with CASCADE, but the list is explicit so a table
# added to the schema and forgotten here shows up as a test leaking rows into
# the next one rather than as a mystery.
TABLES = (
    'onchain.section_diff',
    'onchain.section',
    'onchain.build',
    'onchain.evidence',
    'onchain.transfer',
    'onchain.position_event',
    'onchain.fetch_cursor',
    'onchain.source_entity',
    'onchain.source',
    'onchain.entity',
    'onchain.run',
    'onchain.threshold_version',
)


def _database_url() -> str:
    return os.getenv('PROJECT_MONITOR_TEST_DATABASE_URL', DEFAULT_TEST_DATABASE_URL)


@pytest.fixture
def onchain_database_url():
    return _database_url()


@pytest.fixture
def onchain_repository():
    url = _database_url()
    try:
        repo = OnchainRepository(url)
    except psycopg.OperationalError as exc:
        pytest.fail(
            'the onchain dossier tests require a real Postgres and must never '
            'fall back to another engine. Start the operator stack '
            '(`docker compose up -d project_monitor_postgres`), which serves '
            'this default URL, or set PROJECT_MONITOR_TEST_DATABASE_URL. '
            f'Connection error: {exc}'
        )
    # Truncate rather than drop: the DDL is what the production path runs at
    # every connect, so re-running it per test would hide a statement that only
    # works on an empty database.
    with repo.connection.cursor() as cursor:
        cursor.execute(f'TRUNCATE {", ".join(TABLES)} RESTART IDENTITY CASCADE')
    repo.commit()
    yield repo
    repo.close()


@pytest.fixture
def second_onchain_repository():
    """A second CONNECTION, for the advisory-lock tests: the lock is
    session-scoped, so a second cursor on the same connection would acquire it
    happily and prove nothing."""
    repo = OnchainRepository(_database_url())
    yield repo
    repo.close()


# One registry document for every onchain test that needs one. A fixture rather
# than a constant each module copies: the registry is validated against
# `config.VERIFIED_UNISWAP_ADDRESSES`, so a drifted second copy would fail
# validation in one suite and pass in another, and the failure would read as a
# code change rather than a stale fixture.
ONCHAIN_REGISTRY_PAYLOAD = {
    'chains': [
        {
            'chain_id': 4663, 'key': 'robinhood', 'display_name': 'Robinhood Chain',
            'dexscreener_slug': 'robinhood',
            'explorer_api': 'https://robinhoodchain.blockscout.com/api/v2/',
            'uniswap': {
                'v3_factory': '0x1f7d7550b1b028f7571e69a784071f0205fd2efa',
                'v3_position_manager': '0x73991a25c818bf1f1128deaab1492d45638de0d3',
                'v4_pool_manager': '0x8366a39cc670b4001a1121b8f6a443a643e40951',
                'v4_position_manager': '0x58daec3116aae6d93017baaea7749052e8a04fa7',
                'v4_state_view': '0xf3334192d15450cdd385c8b70e03f9a6bd9e673b',
            },
            'lockers': [],
        }
    ],
    'projects': [
        {
            'key': 'touch-grass', 'display_name': 'Touch Grass', 'chain_id': 4663,
            'archetype': 'launchpad-fixed-supply',
            'pool_ref': '0x64c5dbbee60473344dc6f7b11391ff9c7bb7464c0d5ecfb0d311ff26df9f8c77',
            'sources': [],
        }
    ],
}


@pytest.fixture
def onchain_registry_payload():
    import copy

    # A deep copy per test: a test that adds a fifth project (criterion A1) must
    # not leave it in the document the next test parses.
    return copy.deepcopy(ONCHAIN_REGISTRY_PAYLOAD)
