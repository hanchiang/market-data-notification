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
import os

import psycopg
import pytest

from src.service.onchain.repository import OnchainRepository

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
