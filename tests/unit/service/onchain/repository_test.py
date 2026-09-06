"""Store behaviour against a real Postgres: the DDL, the entity model's foreign
keys, exact amounts, the advisory lock's independence from the monitor's, and
the run ledger.
"""
import psycopg
import pytest

from src.service.project_monitor.repository import (
    ADVISORY_LOCK_KEY as MONITOR_LOCK_KEY,
)
from src.service.onchain.config import ADVISORY_LOCK_KEY, ONCHAIN_SCHEMA
from src.service.onchain.repository import (
    SCHEMA_STATEMENTS,
    LockNotAcquiredError,
    OnchainRepository,
)

DESIGNED_TABLES = {
    'entity', 'source', 'source_entity', 'run', 'build', 'section',
    'section_diff', 'evidence', 'transfer', 'fetch_cursor', 'position_event',
    'threshold_version',
}


def _tables(repository):
    return {
        row['relname']
        for row in repository.fetch_all(
            'SELECT c.relname FROM pg_class c JOIN pg_namespace n '
            "ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r'",
            (ONCHAIN_SCHEMA,),
        )
    }


class TestSchema:
    def test_the_ddl_creates_every_table_the_entity_model_names(
        self, onchain_repository
    ):
        assert _tables(onchain_repository) == DESIGNED_TABLES

    def test_the_ddl_is_idempotent(self, onchain_repository):
        """`CREATE SCHEMA/TABLE IF NOT EXISTS` runs at every connect, so it has
        to survive a database that already has the schema AND rows in it."""
        market = onchain_repository.upsert_entity(level='market', key='market')
        onchain_repository.commit()
        onchain_repository.create_schema()
        onchain_repository.create_schema()
        assert onchain_repository.get_entity_by_key('market')['id'] == market

    def test_every_designed_link_is_a_real_foreign_key(self, onchain_repository):
        links = {
            (row['child'], row['parent'])
            for row in onchain_repository.fetch_all(
                'SELECT conrelid::regclass::text AS child, '
                'confrelid::regclass::text AS parent FROM pg_constraint '
                "WHERE contype = 'f' AND connamespace = %s::regnamespace",
                (ONCHAIN_SCHEMA,),
            )
        }
        s = ONCHAIN_SCHEMA
        for child, parent in [
            (f'{s}.entity', f'{s}.entity'),
            (f'{s}.source_entity', f'{s}.source'),
            (f'{s}.source_entity', f'{s}.entity'),
            (f'{s}.build', f'{s}.run'),
            (f'{s}.build', f'{s}.entity'),
            (f'{s}.section', f'{s}.build'),
            (f'{s}.section_diff', f'{s}.section'),
            (f'{s}.evidence', f'{s}.run'),
            (f'{s}.evidence', f'{s}.entity'),
            (f'{s}.transfer', f'{s}.entity'),
            (f'{s}.fetch_cursor', f'{s}.entity'),
            (f'{s}.position_event', f'{s}.entity'),
        ]:
            assert (child, parent) in links, f'{child} -> {parent} is not declared'

    def test_amount_columns_are_numeric_78_0(self, onchain_repository):
        types = {
            (row['t'], row['col']): row['type']
            for row in onchain_repository.fetch_all(
                'SELECT c.relname AS t, a.attname AS col, '
                'format_type(a.atttypid, a.atttypmod) AS type '
                'FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace '
                'JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 '
                "AND NOT a.attisdropped WHERE n.nspname = %s "
                "AND a.atttypid = 'numeric'::regtype",
                (ONCHAIN_SCHEMA,),
            )
        }
        assert types[('transfer', 'amount')] == 'numeric(78,0)'
        assert types[('position_event', 'liquidity_delta')] == 'numeric(78,0)'

    def test_every_statement_names_the_schema(self):
        """A `search_path` is per-session state a future caller can forget, and
        the monitor's tables sit one schema away in the same database."""
        for statement in SCHEMA_STATEMENTS:
            if statement.strip().startswith('CREATE SCHEMA'):
                continue
            assert f'{ONCHAIN_SCHEMA}.' in statement, statement[:80]


class TestEntityModel:
    def test_amounts_survive_a_round_trip_at_uint256_scale(self, onchain_repository):
        """`numeric(78,0)`, never float: a supply figure wrong in its last places
        is worse than one that is missing, because it looks fine."""
        huge = (1 << 255) + 12345
        token = onchain_repository.upsert_entity(level='token', key='token:1:0xa')
        onchain_repository.insert_transfers(
            token,
            [{'block': 1, 'tx_hash': '0xa', 'log_index': 0, 'from_addr': '0x0',
              'to_addr': '0x1', 'amount': huge}],
        )
        onchain_repository.commit()
        stored = onchain_repository.fetch_all(
            f'SELECT amount FROM {ONCHAIN_SCHEMA}.transfer'
        )[0]['amount']
        assert int(stored) == huge

    def test_transfers_are_insert_or_ignore(self, onchain_repository):
        token = onchain_repository.upsert_entity(level='token', key='token:1:0xa')
        row = {'block': 1, 'tx_hash': '0xa', 'log_index': 0, 'from_addr': '0x0',
               'to_addr': '0x1', 'amount': 5}
        assert onchain_repository.insert_transfers(token, [row]) == 1
        assert onchain_repository.insert_transfers(token, [row]) == 0
        onchain_repository.commit()

    def test_upsert_entity_is_keyed_by_key_not_by_name(self, onchain_repository):
        first = onchain_repository.upsert_entity(
            level='project', key='project:zzz', display_name='ZZZ'
        )
        second = onchain_repository.upsert_entity(
            level='project', key='project:zzz', display_name='ZZZ (renamed)'
        )
        assert first == second
        assert onchain_repository.get_entity_by_key('project:zzz')['display_name'] == (
            'ZZZ (renamed)'
        )

    def test_an_unknown_entity_level_is_rejected(self, onchain_repository):
        with pytest.raises(ValueError, match='unknown entity level'):
            onchain_repository.upsert_entity(level='galaxy', key='x')

    def test_an_evidence_row_cannot_reference_a_missing_run(self, onchain_repository):
        """The foreign key is the rule: a row whose run id is invented cannot be
        traced from an alert, so the store refuses it."""
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            onchain_repository.insert_evidence(
                run_id=999999, kind='jsonrpc', method_or_url='eth_call',
                body={}, endpoint_kind='public',
            )
        onchain_repository.rollback()

    def test_a_fetch_cursor_never_moves_backwards(self, onchain_repository):
        token = onchain_repository.upsert_entity(level='token', key='token:1:0xa')
        onchain_repository.set_fetch_cursor(token, 'transfer', 500)
        onchain_repository.set_fetch_cursor(token, 'transfer', 200)
        onchain_repository.commit()
        assert onchain_repository.get_fetch_cursor(token, 'transfer') == 500

    def test_the_two_cursor_streams_are_independent(self, onchain_repository):
        """The `transfer` stream is keyed by the token entity and `position` by
        the pool entity; one entity can legitimately carry both."""
        entity = onchain_repository.upsert_entity(level='pool', key='pool:1:v3:0xa')
        onchain_repository.set_fetch_cursor(entity, 'transfer', 10)
        onchain_repository.set_fetch_cursor(entity, 'position', 20)
        onchain_repository.commit()
        assert onchain_repository.get_fetch_cursor(entity, 'transfer') == 10
        assert onchain_repository.get_fetch_cursor(entity, 'position') == 20


class TestSingleWriter:
    def test_a_second_build_cannot_take_the_lock(
        self, onchain_repository, second_onchain_repository
    ):
        with onchain_repository.advisory_lock():
            with pytest.raises(LockNotAcquiredError):
                with second_onchain_repository.advisory_lock():
                    pass
        # Released on exit, so the next run gets it.
        with second_onchain_repository.advisory_lock():
            pass

    def test_the_key_differs_from_the_monitors_so_record_and_build_run_together(
        self, onchain_repository, second_onchain_repository
    ):
        """The design's reason for a distinct key, checked as behaviour rather
        than as an inequality of two constants."""
        assert ADVISORY_LOCK_KEY != MONITOR_LOCK_KEY
        with onchain_repository.advisory_lock():
            with second_onchain_repository.connection.cursor() as cursor:
                cursor.execute(
                    'SELECT pg_try_advisory_lock(%s) AS acquired', (MONITOR_LOCK_KEY,)
                )
                assert cursor.fetchone()['acquired'] is True
                cursor.execute('SELECT pg_advisory_unlock(%s)', (MONITOR_LOCK_KEY,))
            second_onchain_repository.commit()


class TestRunLedger:
    def test_a_run_records_its_outcome_failed_units_and_spend(self, onchain_repository):
        run_id = onchain_repository.start_run('onchain.build')
        assert onchain_repository.get_run(run_id)['outcome'] == 'running'
        onchain_repository.finish_run(
            run_id,
            outcome='partial',
            failed_units=[{'unit': 'zzz/onchain_health', 'error_class': 'EvmRpcError'}],
            spend={'alchemy': {'cu': 1600}},
        )
        row = onchain_repository.get_run(run_id)
        assert row['outcome'] == 'partial'
        assert row['failed_units_json'][0]['unit'] == 'zzz/onchain_health'
        assert row['spend_json']['alchemy']['cu'] == 1600
        assert row['finished_at'] is not None

    def test_one_build_per_project_per_run(self, onchain_repository):
        project = onchain_repository.upsert_entity(level='project', key='project:zzz')
        run_id = onchain_repository.start_run('onchain.build')
        onchain_repository.start_build(run_id=run_id, project_id=project)
        with pytest.raises(psycopg.errors.UniqueViolation):
            onchain_repository.start_build(run_id=run_id, project_id=project)
        onchain_repository.rollback()

    def test_the_baseline_skips_a_failed_section_and_the_current_one(
        self, onchain_repository
    ):
        """A3: a section whose collector failed leaves its PREVIOUS build as the
        baseline for the next diff."""
        project = onchain_repository.upsert_entity(level='project', key='project:zzz')
        ids = []
        for status, fields in (
            ('ok', {'owner': '0xa'}),
            ('failed', {}),
            ('ok', {'owner': '0xb'}),
        ):
            run_id = onchain_repository.start_run('onchain.build')
            build_id = onchain_repository.start_build(
                run_id=run_id, project_id=project
            )
            ids.append(
                onchain_repository.insert_section(
                    build_id=build_id, name='contract_safety', status=status,
                    fields=fields,
                )
            )
        onchain_repository.commit()

        baseline = onchain_repository.get_latest_section(
            project, 'contract_safety', before_section_id=ids[2]
        )
        assert baseline['id'] == ids[0]
        assert baseline['fields_json'] == {'owner': '0xa'}


class TestReadOnly:
    def test_a_read_only_connection_is_refused_by_the_server(
        self, onchain_repository, onchain_database_url
    ):
        """`BEGIN READ ONLY` on the server, not a convention a future edit can
        forget: the report job and the dashboard routes open this way."""
        onchain_repository.upsert_entity(level='market', key='market')
        onchain_repository.commit()

        reader = OnchainRepository(onchain_database_url, read_only=True)
        try:
            assert reader.get_entity_by_key('market') is not None
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                reader.upsert_entity(level='chain', key='chain:1')
        finally:
            reader.close()
