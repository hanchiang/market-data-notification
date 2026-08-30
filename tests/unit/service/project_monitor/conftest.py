"""Fixtures for the project monitor tests.

Every store test runs against a **real Postgres**, never a substitute. The store
decision rejected a two-engine split precisely because two schemas kept aligned
by hand is how a wrong number survives unnoticed -- and a SQLite stand-in under
test would reintroduce exactly that split, one dialect at a time
(`ON CONFLICT`, `numeric(78,0)`, advisory locks, `jsonb`).

When Postgres is absent these tests **fail loudly rather than skip**. A skip
would make the store's coverage invisible in a green run, which is the failure
mode this whole slice's evidence rules exist to prevent. The compose stack
(`docker-compose.test.yml`) provides the service in CI and locally.
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
    'manifest_diff', 'manifest_snapshot', 'raw_response', 'reading', 'sample',
    'mint', 'flow', 'event', 'epoch_boundary', 'contract', 'project', 'run',
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
            'fall back to another engine. Start the compose stack '
            '(`docker compose -f docker-compose.test.yml up -d postgres`) or set '
            f'PROJECT_MONITOR_TEST_DATABASE_URL. Connection error: {exc}'
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
def issuance_fixture():
    """The one premiumSeller execution named in the dapp-crawl trace, captured
    as its own single-block window. Separate from `log_window.json` because the
    live capture near head contains no issuance mint -- the desk had not
    executed inside it -- and AC6 asks for one."""
    return load_fixture('issuance_window.json')
