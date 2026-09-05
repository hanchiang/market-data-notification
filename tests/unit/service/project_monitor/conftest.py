"""Fixtures for the project monitor tests.

Every store test runs against a **real Postgres**, never a substitute. The store
decision rejected a two-engine split precisely because two schemas kept aligned
by hand is how a wrong number survives unnoticed -- and a SQLite stand-in under
test would reintroduce exactly that split, one dialect at a time
(`ON CONFLICT`, `numeric(78,0)`, advisory locks, `jsonb`).

When Postgres is absent these tests **fail loudly rather than skip**. A skip
would make the store's coverage invisible in a green run, which is the failure
mode this whole slice's evidence rules exist to prevent.

Two stacks serve that Postgres and they are not interchangeable. CI runs pytest
inside `docker-compose.test.yml`, which reaches its own tmpfs server by hostname.
A local run uses `DEFAULT_TEST_DATABASE_URL` below -- port 55432, the operator
stack's `project_monitor_postgres`, which hosts `project_monitor_test` beside the
operator database. The test compose publishes 5432 with a different password, so
starting it does NOT satisfy the local default; that mismatch is why the failure
message below names the operator stack.
"""
import json
import os
from pathlib import Path

import psycopg
import pytest

from src.service.project_monitor.repository import ProjectMonitorRepository

FIXTURE_DIR = Path(__file__).parent / 'fixtures'

DEFAULT_TEST_DATABASE_URL = (
    'postgresql://postgres:devpass@127.0.0.1:55432/project_monitor_test'
)

TABLES = (
    'manifest_diff', 'manifest_snapshot', 'raw_response', 'backfill_log_raw_response',
    'reading', 'sample', 'mint', 'flow', 'event', 'epoch_boundary', 'contract',
    'project', 'run',
)


def _database_url() -> str:
    return os.getenv('PROJECT_MONITOR_TEST_DATABASE_URL', DEFAULT_TEST_DATABASE_URL)


@pytest.fixture
def database_url():
    """The connection string the fixtures use, for tests that drive an
    entrypoint which opens its own connection."""
    return _database_url()


@pytest.fixture
def repository():
    url = _database_url()
    try:
        repo = ProjectMonitorRepository(url)
    except psycopg.OperationalError as exc:
        pytest.fail(
            'the project monitor tests require a real Postgres and must never '
            'fall back to another engine. Start the operator stack '
            '(`docker compose up -d project_monitor_postgres`), which serves '
            'this default URL, or set PROJECT_MONITOR_TEST_DATABASE_URL. Note '
            '`docker-compose.test.yml` is the CI stack on a different port and '
            f'password and will not satisfy the default. Connection error: {exc}'
        )
    # Truncate rather than drop: the DDL is what the production path runs, so
    # re-running it every test would also re-test it every test and hide a
    # schema statement that only works on an empty database.
    with repo.connection.cursor() as cursor:
        cursor.execute(f'TRUNCATE {", ".join(TABLES)} RESTART IDENTITY CASCADE')
    repo.commit()
    yield repo
    repo.close()


@pytest.fixture
def second_repository():
    """A second connection, for the single-writer and isolation tests.

    A separate connection, not a separate cursor: an advisory lock is
    session-scoped, so a second cursor on the same connection would acquire it
    happily and prove nothing.
    """
    repo = ProjectMonitorRepository(_database_url())
    yield repo
    repo.close()


def load_fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


@pytest.fixture
def sample_fixture():
    return load_fixture('sample_raw_responses.json')


@pytest.fixture
def expected_fixture():
    return load_fixture('sample_expected.json')


@pytest.fixture
def log_window_fixture():
    return load_fixture('log_window.json')


@pytest.fixture
def buyback_fixture():
    """The single-block window holding the ONE `InverseBonded` execution in the
    chain's history (block 20,076,087), found 2026-08-30 by scanning
    `eth_getLogs` over blocks 0..49,977,432 in 1.5M-block windows. Without it
    the buyback ABI has no ground-truth event to decode against."""
    return load_fixture('buyback_window.json')


@pytest.fixture
def haircut_fixture():
    """Six real epochs from the operator store, for the Morpho-haircut tests.

    Fixture rather than a query against the operator database: the suite runs
    against a truncated `project_monitor_test`, and in CI against a tmpfs server
    that has never seen a backfill, so a store-backed assertion on epoch 133
    would be green on one machine and absent everywhere else. Rebuilt by
    `scripts/capture_project_monitor_haircut_fixture.py`; the file's own
    `provenance` block names the queries.
    """
    return load_fixture('haircut_epochs.json')


@pytest.fixture
def issuance_fixture():
    """The one premiumSeller execution named in the dapp-crawl trace, captured
    as its own single-block window. Separate from `log_window.json` because the
    live capture near head contains no issuance mint -- the desk had not
    executed inside it -- and AC6 asks for one."""
    return load_fixture('issuance_window.json')
