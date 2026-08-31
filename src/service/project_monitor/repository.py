"""Postgres store for the project monitor. psycopg v3, sync, no ORM.

DDL is idempotent `CREATE TABLE IF NOT EXISTS`, run at connect: a single-writer
job with one reader does not yet earn Alembic, and a migration tool whose first
migration is the whole schema buys nothing.

**It creates tables, never databases.** Unlike SQLite, where opening a new path
creates the file, Postgres refuses a connection to a database that does not
exist, and `CREATE DATABASE` cannot be issued from inside the database it
creates. So the databases come from the server's own first-init -- the
production one from the container's `POSTGRES_DB`, the `_test` one from a script
mounted at `/docker-entrypoint-initdb.d/`. Both run **only while the data
directory is empty**, so a database added after the store has data is a manual
`CREATE DATABASE`, not a redeploy. That will bite someone; it is written down
here because this is the file they will open.

Amounts are `numeric(78,0)`: wei-scale integers up to a uint256, exact, with
`decimals` recorded per reading. Never float -- a treasury figure that is off in
the last places is worse than one that is missing, because it looks fine.
"""
import json
import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger('Project monitor repository')

# One constant key for the single-writer advisory lock. Any value works as long
# as nothing else in this database picks the same one; it is derived from the
# job name so a second store in this database would have to collide deliberately.
ADVISORY_LOCK_KEY = 0x704D6F6E  # 'pMon'

SCHEMA_STATEMENTS: Sequence[str] = (
    """
    CREATE TABLE IF NOT EXISTS run (
        id           bigserial PRIMARY KEY,
        started_at   timestamptz NOT NULL DEFAULT now(),
        finished_at  timestamptz,
        job          text NOT NULL,
        outcome      text NOT NULL,
        error_class  text,
        notes        text
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sample (
        id              bigserial PRIMARY KEY,
        run_id          bigint REFERENCES run(id),
        project         text NOT NULL,
        block           bigint NOT NULL,
        block_timestamp bigint NOT NULL,
        read_at         timestamptz NOT NULL DEFAULT now(),
        epoch_number    bigint,
        kind            text NOT NULL,
        endpoint_kind   text NOT NULL,
        UNIQUE (project, block, kind)
    )
    """,
    # The verbatim JSON-RPC response, with the request that produced it. The
    # endpoint URL is deliberately absent (R2, R4): `endpoint_kind` is the
    # provenance, because the URL is a credential.
    """
    CREATE TABLE IF NOT EXISTS raw_response (
        id            bigserial PRIMARY KEY,
        sample_id     bigint NOT NULL REFERENCES sample(id) ON DELETE CASCADE,
        seq           integer NOT NULL,
        method        text NOT NULL,
        params_json   jsonb NOT NULL,
        body_json     jsonb NOT NULL,
        endpoint_kind text NOT NULL,
        UNIQUE (sample_id, seq)
    )
    """,
    # Step 3's analogue of `raw_response`, for the one part of R2 the sample
    # table cannot carry: a backfill log sweep pins no single block, so there is
    # no `sample_id` to hang the raw body on. What identifies a row instead is
    # the query that produced it (`query_name`, matching `params_json`'s own
    # address/topics) and the `[from_block, to_block]` span that ONE JSON-RPC
    # call actually covered -- `fetch_window` narrows that span on a
    # too-wide-window error, so it can differ per call even within one step-3
    # run. That triple is also the re-run guard: a second backfill reissues the
    # same calls, and `ON CONFLICT DO NOTHING` on it keeps that harmless instead
    # of duplicating rows, the same role `(tx_hash, log_index)` plays for
    # mint/flow/event. The endpoint URL is absent for the same reason as
    # `raw_response`: `endpoint_kind` is the provenance, the URL is a credential.
    """
    CREATE TABLE IF NOT EXISTS backfill_log_raw_response (
        id            bigserial PRIMARY KEY,
        project       text NOT NULL,
        query_name    text NOT NULL,
        from_block    bigint NOT NULL,
        to_block      bigint NOT NULL,
        method        text NOT NULL,
        params_json   jsonb NOT NULL,
        body_json     jsonb NOT NULL,
        endpoint_kind text NOT NULL,
        fetched_at    timestamptz NOT NULL DEFAULT now(),
        UNIQUE (project, query_name, from_block, to_block)
    )
    """,
    # `state` is what keeps a peripheral failure from becoming a null that reads
    # like a zero: 'ok', 'not_deployed' (the contract had no code at this
    # block -- a defined observation, not a failure) or 'failed' with its class.
    """
    CREATE TABLE IF NOT EXISTS reading (
        id          bigserial PRIMARY KEY,
        sample_id   bigint NOT NULL REFERENCES sample(id) ON DELETE CASCADE,
        name        text NOT NULL,
        contract    text NOT NULL,
        tier        text NOT NULL,
        raw_hex     text,
        value_int   numeric(78,0),
        value_json  jsonb,
        decimals    integer,
        state       text NOT NULL,
        error_class text,
        UNIQUE (sample_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mint (
        id         bigserial PRIMARY KEY,
        project    text NOT NULL,
        block      bigint NOT NULL,
        tx_hash    text NOT NULL,
        log_index  integer NOT NULL,
        recipient  text NOT NULL,
        amount     numeric(78,0) NOT NULL,
        decimals   integer NOT NULL,
        class      text NOT NULL,
        UNIQUE (tx_hash, log_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS flow (
        id           bigserial PRIMARY KEY,
        project      text NOT NULL,
        block        bigint NOT NULL,
        tx_hash      text NOT NULL,
        log_index    integer NOT NULL,
        direction    text NOT NULL,
        counterparty text NOT NULL,
        amount       numeric(78,0) NOT NULL,
        decimals     integer NOT NULL,
        label        text NOT NULL,
        rule         text NOT NULL,
        UNIQUE (tx_hash, log_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event (
        id          bigserial PRIMARY KEY,
        project     text NOT NULL,
        block       bigint NOT NULL,
        tx_hash     text NOT NULL,
        log_index   integer NOT NULL,
        contract    text NOT NULL,
        name        text NOT NULL,
        fields_json jsonb NOT NULL,
        UNIQUE (tx_hash, log_index)
    )
    """,
    """
    -- Keyed by the boundary BLOCK, not by the epoch number. A rebase log gives
    -- the exact block a new epoch starts at, but carries no epoch number; the
    -- number is only known once a sample at that block reads `Staking.epoch()`.
    -- Keying by number would have forced backfill to invent one -- an ordinal
    -- index that silently disagrees with the chain's own counter.
    CREATE TABLE IF NOT EXISTS epoch_boundary (
        project      text NOT NULL,
        first_block  bigint NOT NULL,
        epoch_number bigint,
        rebase_tx    text,
        PRIMARY KEY (project, first_block)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contract (
        project      text NOT NULL,
        name         text NOT NULL,
        address      text NOT NULL,
        deploy_block bigint,
        PRIMARY KEY (project, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS project (
        project text NOT NULL,
        key     text NOT NULL,
        value   text,
        PRIMARY KEY (project, key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS manifest_snapshot (
        id                 bigserial PRIMARY KEY,
        project            text NOT NULL,
        fetched_at         timestamptz NOT NULL DEFAULT now(),
        bundle_filename    text NOT NULL,
        build_hash         text,
        bundle_sha256      text NOT NULL,
        registry_json      jsonb NOT NULL,
        extractor_verified boolean NOT NULL DEFAULT false
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS manifest_diff (
        id          bigserial PRIMARY KEY,
        snapshot_id bigint NOT NULL REFERENCES manifest_snapshot(id) ON DELETE CASCADE,
        kind        text NOT NULL,
        name        text NOT NULL,
        old_address text,
        new_address text
    )
    """,
    "CREATE INDEX IF NOT EXISTS sample_epoch_idx ON sample (project, epoch_number)",
    "CREATE INDEX IF NOT EXISTS mint_block_idx ON mint (project, block)",
    "CREATE INDEX IF NOT EXISTS flow_block_idx ON flow (project, block)",
    "CREATE INDEX IF NOT EXISTS backfill_log_raw_response_block_idx "
    "ON backfill_log_raw_response (project, from_block, to_block)",
)


class LockNotAcquiredError(RuntimeError):
    """Another run holds the single-writer lock."""


class ProjectMonitorRepository:
    """Every write for a sample happens inside one transaction (R8).

    The connection is opened with `autocommit=False`, so the transaction is the
    default state and a commit is an explicit act. That is the right default
    here: the failure the store must survive is a half-written sample.
    """

    def __init__(self, database_url: str, *, connect_timeout: int = 10) -> None:
        self.database_url = database_url
        self.connection = psycopg.connect(
            database_url, autocommit=False, connect_timeout=connect_timeout,
            row_factory=dict_row,
        )
        self.create_schema()

    def close(self) -> None:
        if not self.connection.closed:
            self.connection.close()

    def __enter__(self) -> 'ProjectMonitorRepository':
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # -- schema ----------------------------------------------------------

    def create_schema(self) -> None:
        with self.connection.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)
        self.connection.commit()

    # -- single writer ---------------------------------------------------

    @contextmanager
    def advisory_lock(self) -> Iterator[None]:
        """Session-scoped advisory lock guarding live-vs-live and backfill-vs-live.

        A Postgres advisory lock rather than a lock file, and strictly better at
        the same job: the server releases it when the connection dies, so a
        killed run leaves nothing to clean up by hand. Cron does not serialise a
        run that outlasts its hour, which is the case this exists for.
        """
        with self.connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s) AS acquired', (ADVISORY_LOCK_KEY,))
            row = cursor.fetchone()
            if not row or not row['acquired']:
                raise LockNotAcquiredError(
                    'another project_monitor run holds the advisory lock'
                )
        # The lock is session-scoped, so it must not be taken inside the sample
        # transaction: a rollback would not release it, and a commit would not
        # either. Committing here leaves the connection clean for the sample.
        self.connection.commit()
        try:
            yield
        finally:
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_unlock(%s)', (ADVISORY_LOCK_KEY,))
                self.connection.commit()
            except psycopg.Error:
                # The connection is already gone, which released the lock anyway.
                logger.warning('could not release the advisory lock; connection closed')

    # -- runs ------------------------------------------------------------

    def start_run(self, job: str) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO run (job, outcome) VALUES (%s, 'running') RETURNING id",
                (job,),
            )
            run_id = cursor.fetchone()['id']
        self.connection.commit()
        return run_id

    def finish_run(
        self,
        run_id: int,
        *,
        outcome: str,
        error_class: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Written before any alert is attempted, so an alert failure cannot
        cost us the record of what the run did."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                'UPDATE run SET outcome = %s, error_class = %s, notes = %s, '
                'finished_at = now() WHERE id = %s',
                (outcome, error_class, notes, run_id),
            )
        self.connection.commit()

    # -- samples ---------------------------------------------------------

    def insert_sample(
        self,
        *,
        run_id: int,
        project: str,
        block: int,
        block_timestamp: int,
        epoch_number: Optional[int],
        kind: str,
        endpoint_kind: str,
    ) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO sample (run_id, project, block, block_timestamp, '
                'epoch_number, kind, endpoint_kind) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id',
                (run_id, project, block, block_timestamp, epoch_number, kind, endpoint_kind),
            )
            return cursor.fetchone()['id']

    def insert_raw_responses(self, sample_id: int, raw_responses: Iterable[Any]) -> None:
        with self.connection.cursor() as cursor:
            for seq, raw in enumerate(raw_responses):
                cursor.execute(
                    'INSERT INTO raw_response (sample_id, seq, method, params_json, '
                    'body_json, endpoint_kind) VALUES (%s, %s, %s, %s, %s, %s)',
                    (
                        sample_id,
                        seq,
                        raw.method,
                        json.dumps(raw.params),
                        json.dumps(raw.body),
                        raw.endpoint_kind,
                    ),
                )

    def insert_backfill_log_raw_responses(
        self, project: str, entries: Iterable[Any]
    ) -> int:
        """The backfill's raw `eth_getLogs` bodies (R2), keyed by query + block
        span rather than a sample id -- see `backfill_log_raw_response`'s DDL
        comment for why. `entries` is `(query_name, RawResponse)` pairs: step 3
        passes `recorder.read_log_window`'s `raw_responses_by_query`, step 2
        pairs its own query name with what `fetch_window` returned.

        Both steps write here and both query `net_mints`, so a span they both
        read lands on one key and first-write-wins keeps one body. That is
        intended -- see the decision stated at step 2's call site in
        `backfill.py`. Returns the number of rows actually inserted, so a re-run
        can report "0 new" instead of silently looking like it fetched again.
        """
        inserted = 0
        with self.connection.cursor() as cursor:
            for query_name, raw in entries:
                # `raw.params` is `[filterObject]` (see `LogFilter.to_params`);
                # `fromBlock`/`toBlock` are the request's own hex block bounds,
                # not `from_block`/`to_block` passed in from the caller -- those
                # narrow per sub-window and this is the one place that survives.
                filter_params = raw.params[0]
                cursor.execute(
                    'INSERT INTO backfill_log_raw_response '
                    '(project, query_name, from_block, to_block, method, '
                    'params_json, body_json, endpoint_kind) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s) '
                    'ON CONFLICT (project, query_name, from_block, to_block) '
                    'DO NOTHING',
                    (
                        project,
                        query_name,
                        int(filter_params['fromBlock'], 16),
                        int(filter_params['toBlock'], 16),
                        raw.method,
                        json.dumps(raw.params),
                        json.dumps(raw.body),
                        raw.endpoint_kind,
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    def insert_readings(self, sample_id: int, readings: Iterable[Dict[str, Any]]) -> None:
        with self.connection.cursor() as cursor:
            for reading in readings:
                cursor.execute(
                    'INSERT INTO reading (sample_id, name, contract, tier, raw_hex, '
                    'value_int, value_json, decimals, state, error_class) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                    (
                        sample_id,
                        reading['name'],
                        reading['contract'],
                        reading['tier'],
                        reading.get('raw_hex'),
                        reading.get('value_int'),
                        json.dumps(reading['value_json'])
                        if reading.get('value_json') is not None
                        else None,
                        reading.get('decimals'),
                        reading['state'],
                        reading.get('error_class'),
                    ),
                )

    # -- log-derived rows ------------------------------------------------
    #
    # All three are insert-or-ignore on (tx_hash, log_index). Backfill windows
    # and live windows can overlap, and the unique constraint is the guard that
    # makes an overlap harmless rather than double-counted.

    def insert_mints(self, rows: Iterable[Dict[str, Any]]) -> int:
        return self._insert_ignoring_duplicates(
            'mint',
            ('project', 'block', 'tx_hash', 'log_index', 'recipient', 'amount',
             'decimals', 'class'),
            rows,
        )

    def insert_flows(self, rows: Iterable[Dict[str, Any]]) -> int:
        return self._insert_ignoring_duplicates(
            'flow',
            ('project', 'block', 'tx_hash', 'log_index', 'direction', 'counterparty',
             'amount', 'decimals', 'label', 'rule'),
            rows,
        )

    def insert_events(self, rows: Iterable[Dict[str, Any]]) -> int:
        prepared = [dict(row) for row in rows]
        for row in prepared:
            row['fields_json'] = json.dumps(row['fields_json'])
        return self._insert_ignoring_duplicates(
            'event',
            ('project', 'block', 'tx_hash', 'log_index', 'contract', 'name', 'fields_json'),
            prepared,
        )

    def _insert_ignoring_duplicates(
        self, table: str, columns: Sequence[str], rows: Iterable[Dict[str, Any]]
    ) -> int:
        quoted = ', '.join(f'"{c}"' for c in columns)
        placeholders = ', '.join(['%s'] * len(columns))
        statement = (
            f'INSERT INTO {table} ({quoted}) VALUES ({placeholders}) '
            'ON CONFLICT (tx_hash, log_index) DO NOTHING'
        )
        inserted = 0
        with self.connection.cursor() as cursor:
            for row in rows:
                cursor.execute(statement, tuple(row[c] for c in columns))
                inserted += cursor.rowcount
        return inserted

    # -- cursor ----------------------------------------------------------

    def get_live_cursor(self, project: str) -> Optional[int]:
        """`max(block)` over live samples -- never the last row inserted.

        Backfill writes older rows later, so "the most recent row" and "the
        furthest forward the live pass has reached" are different numbers, and
        using the wrong one would rewind the log window over ground already
        covered.
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT max(block) AS cursor FROM sample "
                "WHERE project = %s AND kind = 'live'",
                (project,),
            )
            row = cursor.fetchone()
        return row['cursor'] if row and row['cursor'] is not None else None

    def get_project_value(self, project: str, key: str) -> Optional[str]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                'SELECT value FROM project WHERE project = %s AND key = %s',
                (project, key),
            )
            row = cursor.fetchone()
        return row['value'] if row else None

    def set_project_value(self, project: str, key: str, value: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO project (project, key, value) VALUES (%s, %s, %s) '
                'ON CONFLICT (project, key) DO UPDATE SET value = EXCLUDED.value',
                (project, key, value),
            )

    def set_contract_deploy_block(
        self, project: str, name: str, address: str, deploy_block: Optional[int]
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO contract (project, name, address, deploy_block) '
                'VALUES (%s, %s, %s, %s) ON CONFLICT (project, name) DO UPDATE SET '
                'address = EXCLUDED.address, deploy_block = EXCLUDED.deploy_block',
                (project, name, address, deploy_block),
            )

    def get_deploy_blocks(self, project: str) -> Dict[str, Optional[int]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                'SELECT name, deploy_block FROM contract WHERE project = %s', (project,)
            )
            return {row['name']: row['deploy_block'] for row in cursor.fetchall()}

    def upsert_epoch_boundary(
        self,
        project: str,
        first_block: int,
        rebase_tx: Optional[str],
        epoch_number: Optional[int] = None,
    ) -> None:
        """Record a boundary block, filling in its epoch number if we know it.

        `COALESCE` on update so a later sample can supply the number a log-only
        discovery could not, without a re-run clearing one already learned.
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO epoch_boundary (project, first_block, epoch_number, rebase_tx) '
                'VALUES (%s, %s, %s, %s) ON CONFLICT (project, first_block) DO UPDATE SET '
                'epoch_number = COALESCE(epoch_boundary.epoch_number, EXCLUDED.epoch_number)',
                (project, first_block, epoch_number, rebase_tx),
            )

    # -- manifest --------------------------------------------------------

    def get_latest_manifest_snapshot(self, project: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                'SELECT * FROM manifest_snapshot WHERE project = %s '
                'ORDER BY fetched_at DESC, id DESC LIMIT 1',
                (project,),
            )
            return cursor.fetchone()

    def insert_manifest_snapshot(
        self,
        *,
        project: str,
        bundle_filename: str,
        build_hash: Optional[str],
        bundle_sha256: str,
        registry: Dict[str, str],
        extractor_verified: bool,
    ) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO manifest_snapshot (project, bundle_filename, build_hash, '
                'bundle_sha256, registry_json, extractor_verified) '
                'VALUES (%s, %s, %s, %s, %s, %s) RETURNING id',
                (
                    project,
                    bundle_filename,
                    build_hash,
                    bundle_sha256,
                    json.dumps(registry),
                    extractor_verified,
                ),
            )
            return cursor.fetchone()['id']

    def insert_manifest_diffs(self, snapshot_id: int, diffs: Iterable[Dict[str, Any]]) -> None:
        with self.connection.cursor() as cursor:
            for diff in diffs:
                cursor.execute(
                    'INSERT INTO manifest_diff (snapshot_id, kind, name, old_address, '
                    'new_address) VALUES (%s, %s, %s, %s, %s)',
                    (
                        snapshot_id,
                        diff['kind'],
                        diff['name'],
                        diff.get('old_address'),
                        diff.get('new_address'),
                    ),
                )

    # -- reads for the report -------------------------------------------

    def fetch_all(self, statement: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(statement, tuple(params))
            return list(cursor.fetchall())

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()
