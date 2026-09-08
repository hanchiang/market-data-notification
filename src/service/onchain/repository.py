"""Postgres store for the project dossier. psycopg v3, sync, no ORM.

Schema `onchain` inside the project monitor's database, on the monitor's own
rules: idempotent `CREATE SCHEMA IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS`
run at connect, no migration tool, `numeric(78,0)` for every chain amount, and a
transaction as the default state so the failure the store must survive is a
half-written build.

Three things differ from the monitor's store and each is deliberate:

* **Every statement names the schema.** `onchain.entity`, never `entity`. A
  `search_path` is per-session state that a future caller can forget to set, and
  the monitor's tables live one schema away in the same database.
* **A distinct advisory-lock key** (config `ADVISORY_LOCK_KEY`), so `record` and
  `build` run concurrently. The lock still serialises build against build.
* **Every table hangs off `entity`.** The requirement's entity model (P1) says
  every row is attached to exactly one entity, and the foreign keys are what
  make that true rather than aspirational.
"""
import json
import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import psycopg
from psycopg.rows import dict_row

from src.service.onchain.config import ADVISORY_LOCK_KEY, ONCHAIN_SCHEMA

logger = logging.getLogger('Onchain repository')

SCHEMA_STATEMENTS: Sequence[str] = (
    f'CREATE SCHEMA IF NOT EXISTS {ONCHAIN_SCHEMA}',
    # One row per thing the system keeps a record on. `key` is the stable
    # identifier the registry and the collectors both address a row by
    # (`market`, `chain:4663`, `project:touch-grass`, `pool:4663:v4:0x64c5...`,
    # `token:4663:0x1639...`), so an upsert is idempotent across runs and a
    # renamed display name never forks the entity.
    f"""
    CREATE TABLE IF NOT EXISTS {ONCHAIN_SCHEMA}.entity (
        id           bigserial PRIMARY KEY,
        level        text NOT NULL,
        parent_id    bigint REFERENCES {ONCHAIN_SCHEMA}.entity(id),
        key          text NOT NULL UNIQUE,
        display_name text,
        attrs_json   jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        created_at   timestamptz NOT NULL DEFAULT now(),
        updated_at   timestamptz NOT NULL DEFAULT now()
    )
    """,
    # `admission` is the requirement's source-admission status (P6). Phase 1a
    # writes `admitted` rows from the registry file only, plus the provider's
    # published links as `candidate` for phase 1b's structural hop -- which is
    # why a candidate row is legal here even though nothing reads one yet.
    f"""
    CREATE TABLE IF NOT EXISTS {ONCHAIN_SCHEMA}.source (
        id            bigserial PRIMARY KEY,
        class         text NOT NULL,
        url_or_handle text NOT NULL,
        admission     text NOT NULL,
        admitted_by   text,
        admitted_at   timestamptz,
        evidence_json jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        UNIQUE (class, url_or_handle)
    )
    """,
    # A source serves several entities (P1): one Telegram channel can carry
    # several projects, so the link is its own table rather than a column.
    f"""
    CREATE TABLE IF NOT EXISTS {ONCHAIN_SCHEMA}.source_entity (
        source_id bigint NOT NULL REFERENCES {ONCHAIN_SCHEMA}.source(id) ON DELETE CASCADE,
        entity_id bigint NOT NULL REFERENCES {ONCHAIN_SCHEMA}.entity(id) ON DELETE CASCADE,
        PRIMARY KEY (source_id, entity_id)
    )
    """,
    # The run ledger (P13), and the metric source the dashboard reads (P14).
    # `spend_json` carries CU and request counts per endpoint kind, which is how
    # the scaling envelope gets re-derived from measurement instead of estimate.
    #
    # `outcome` is one of RUN_OUTCOMES: 'running' until the job closes the row,
    # then 'ok', 'partial' (something failed but the run produced builds),
    # 'failed', or 'skipped' (the advisory lock was held). Only 'ok' and
    # 'skipped' suppress the admin alert.
    f"""
    CREATE TABLE IF NOT EXISTS {ONCHAIN_SCHEMA}.run (
        id                bigserial PRIMARY KEY,
        job               text NOT NULL,
        started_at        timestamptz NOT NULL DEFAULT now(),
        finished_at       timestamptz,
        outcome           text NOT NULL,
        failed_units_json jsonb NOT NULL DEFAULT '[]'::jsonb,
        spend_json        jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        notes             text
    )
    """,
    # One build per project per run: the unique constraint is the rule, not a
    # convention the build loop is trusted to keep. `outcome` uses the same
    # vocabulary as `run.outcome` minus 'skipped': a build is 'running', then
    # 'ok', 'partial' (at least one section `partial` or `failed`) or 'failed'.
    f"""
    CREATE TABLE IF NOT EXISTS {ONCHAIN_SCHEMA}.build (
        id                bigserial PRIMARY KEY,
        run_id            bigint NOT NULL REFERENCES {ONCHAIN_SCHEMA}.run(id),
        project_id        bigint NOT NULL REFERENCES {ONCHAIN_SCHEMA}.entity(id),
        block             bigint,
        block_timestamp   bigint,
        started_at        timestamptz NOT NULL DEFAULT now(),
        finished_at       timestamptz,
        outcome           text NOT NULL,
        failed_units_json jsonb NOT NULL DEFAULT '[]'::jsonb,
        threshold_version text,
        UNIQUE (run_id, project_id)
    )
    """,
    # `status` is what keeps a collector failure from reading as "no change"
    # (A3): 'ok', 'partial' (one or more fields in state `failed`), or 'failed'
    # with its error class. `fields_json` is canonical -- sorted keys, big
    # integers as strings -- so two builds diff by value, never by formatting.
    f"""
    CREATE TABLE IF NOT EXISTS {ONCHAIN_SCHEMA}.section (
        id           bigserial PRIMARY KEY,
        build_id     bigint NOT NULL REFERENCES {ONCHAIN_SCHEMA}.build(id) ON DELETE CASCADE,
        name         text NOT NULL,
        span_id      text,
        status       text NOT NULL,
        error_class  text,
        fields_json  jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        evidence_ids bigint[] NOT NULL DEFAULT '{{}}'::bigint[],
        created_at   timestamptz NOT NULL DEFAULT now(),
        UNIQUE (build_id, name)
    )
    """,
    # Keyed by the section it belongs to, so a section has at most one diff and
    # re-running a build cannot leave two. `previous_section_id` is nullable:
    # the first build of a section has no baseline and that is not a failure.
    f"""
    CREATE TABLE IF NOT EXISTS {ONCHAIN_SCHEMA}.section_diff (
        section_id          bigint PRIMARY KEY REFERENCES {ONCHAIN_SCHEMA}.section(id) ON DELETE CASCADE,
        previous_section_id bigint REFERENCES {ONCHAIN_SCHEMA}.section(id),
        changes_json        jsonb NOT NULL,
        flagged_json        jsonb NOT NULL DEFAULT '[]'::jsonb,
        threshold_version   text,
        created_at          timestamptz NOT NULL DEFAULT now()
    )
    """,
    # Provenance (P10). The endpoint URL is deliberately absent: `endpoint_kind`
    # is the provenance and the URL is a credential -- the monitor's `raw_response`
    # rule, and `evidence.store_response` refuses a keyed URL rather than
    # trusting a caller to remember.
    #
    # `entity_id` is NOT NULL because the entity model (P1) says every evidence
    # row references exactly one entity. A run-setup read (the head-block pin,
    # the 24-hour boundary search) is a read about the CHAIN entity, so there is
    # always one to name; tightening this later on a populated table is the
    # migration the no-migration-tool rule makes awkward.
    f"""
    CREATE TABLE IF NOT EXISTS {ONCHAIN_SCHEMA}.evidence (
        id            bigserial PRIMARY KEY,
        run_id        bigint NOT NULL REFERENCES {ONCHAIN_SCHEMA}.run(id),
        span_id       text,
        entity_id     bigint NOT NULL REFERENCES {ONCHAIN_SCHEMA}.entity(id),
        kind          text NOT NULL,
        method_or_url text NOT NULL,
        params_json   jsonb,
        body_json     jsonb NOT NULL,
        endpoint_kind text NOT NULL,
        block         bigint,
        read_at       timestamptz NOT NULL DEFAULT now()
    )
    """,
    # The token's Transfer history, insert-or-ignore on (tx_hash, log_index).
    # No timestamp column: logs carry none, and a header read per block would
    # cost more than every other read of a build combined -- windows are block
    # intervals and only the two build boundaries are resolved to a time.
    f"""
    CREATE TABLE IF NOT EXISTS {ONCHAIN_SCHEMA}.transfer (
        id        bigserial PRIMARY KEY,
        token_id  bigint NOT NULL REFERENCES {ONCHAIN_SCHEMA}.entity(id),
        block     bigint NOT NULL,
        tx_hash   text NOT NULL,
        log_index integer NOT NULL,
        from_addr text NOT NULL,
        to_addr   text NOT NULL,
        amount    numeric(78,0) NOT NULL,
        UNIQUE (tx_hash, log_index)
    )
    """,
    # Committed per fetched window, independent of the section, so a section
    # that fails late resumes from the last committed window rather than from
    # the pool's creation block.
    #
    # `entity_id`, not the design's `token_id`: the `transfer` stream is keyed
    # by the token entity and the `position` stream by the POOL entity, and one
    # column cannot honestly be named after one of them. See the round summary.
    f"""
    CREATE TABLE IF NOT EXISTS {ONCHAIN_SCHEMA}.fetch_cursor (
        entity_id  bigint NOT NULL REFERENCES {ONCHAIN_SCHEMA}.entity(id),
        stream     text NOT NULL,
        last_block bigint NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (entity_id, stream)
    )
    """,
    # v3 `Mint`/`Burn` and v4 `ModifyLiquidity` normalised to one shape, so the
    # custody read is one query per pool type rather than two code paths.
    # `kind` is the normalised event: 'mint' and 'burn' from a v3 pool,
    # 'modify_liquidity' from the v4 pool manager, 'nft_transfer' from either
    # position manager. `liquidity_delta` is signed, negative for a burn, so a
    # position nets to zero when it is closed.
    f"""
    CREATE TABLE IF NOT EXISTS {ONCHAIN_SCHEMA}.position_event (
        id               bigserial PRIMARY KEY,
        pool_id          bigint NOT NULL REFERENCES {ONCHAIN_SCHEMA}.entity(id),
        block            bigint NOT NULL,
        tx_hash          text NOT NULL,
        log_index        integer NOT NULL,
        kind             text NOT NULL,
        owner            text,
        nft_token_id     numeric(78,0),
        tick_lower       integer,
        tick_upper       integer,
        liquidity_delta  numeric(78,0),
        salt             text,
        UNIQUE (tx_hash, log_index)
    )
    """,
    # Written by the build the first time it scores anything under a version
    # (P12, A9). `first_seen_commit` is the checkout's HEAD at that moment: the
    # criterion is checked against version control, so the store has to name the
    # commit the check should look for.
    f"""
    CREATE TABLE IF NOT EXISTS {ONCHAIN_SCHEMA}.threshold_version (
        version           text PRIMARY KEY,
        first_seen_commit text,
        first_seen_at     timestamptz NOT NULL DEFAULT now(),
        table_json        jsonb NOT NULL
    )
    """,
    f'CREATE INDEX IF NOT EXISTS entity_parent_idx ON {ONCHAIN_SCHEMA}.entity (parent_id)',
    f'CREATE INDEX IF NOT EXISTS entity_level_idx ON {ONCHAIN_SCHEMA}.entity (level)',
    f'CREATE INDEX IF NOT EXISTS build_project_idx ON {ONCHAIN_SCHEMA}.build (project_id, id DESC)',
    f'CREATE INDEX IF NOT EXISTS section_name_idx ON {ONCHAIN_SCHEMA}.section (name, id DESC)',
    f'CREATE INDEX IF NOT EXISTS evidence_run_idx ON {ONCHAIN_SCHEMA}.evidence (run_id)',
    f'CREATE INDEX IF NOT EXISTS evidence_span_idx ON {ONCHAIN_SCHEMA}.evidence (span_id)',
    f'CREATE INDEX IF NOT EXISTS transfer_token_block_idx ON {ONCHAIN_SCHEMA}.transfer (token_id, block)',
    f'CREATE INDEX IF NOT EXISTS position_event_pool_block_idx ON {ONCHAIN_SCHEMA}.position_event (pool_id, block)',
    f'CREATE INDEX IF NOT EXISTS run_job_idx ON {ONCHAIN_SCHEMA}.run (job, started_at DESC)',
)

LEVELS = ('market', 'chain', 'project', 'pool', 'token')
RUN_OUTCOMES = ('running', 'ok', 'partial', 'failed', 'skipped')
BUILD_OUTCOMES = ('running', 'ok', 'partial', 'failed')
SECTION_STATUSES = ('ok', 'partial', 'failed')


class LockNotAcquiredError(RuntimeError):
    """Another onchain build holds the single-writer lock."""


class OnchainRepository:
    """Every write of one build happens inside one transaction.

    Opened with `autocommit=False`, so a commit is an explicit act.
    """

    def __init__(
        self,
        database_url: str,
        *,
        connect_timeout: int = 10,
        read_only: bool = False,
    ) -> None:
        self.database_url = database_url
        self.read_only = read_only
        self.connection = psycopg.connect(
            database_url,
            autocommit=False,
            connect_timeout=connect_timeout,
            row_factory=dict_row,
        )
        if read_only:
            # psycopg 3 turns this into `BEGIN READ ONLY`, so the SERVER refuses
            # a write on this connection -- not a convention a future edit can
            # forget. The report job and the dashboard routes open this way. The
            # DDL is skipped for the same reason: a read-only session cannot run
            # it, so opening read-only against a fresh database raises rather
            # than silently creating the schema.
            self.connection.read_only = True
            return
        self.create_schema()

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        if not self.connection.closed:
            self.connection.close()

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def __enter__(self) -> 'OnchainRepository':
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())

    def fetch_one(
        self, sql: str, params: Sequence[Any] = ()
    ) -> Optional[Dict[str, Any]]:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    # -- schema ----------------------------------------------------------

    def create_schema(self) -> None:
        with self.connection.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)
        self.connection.commit()

    # -- single writer ---------------------------------------------------

    @contextmanager
    def advisory_lock(self) -> Iterator[None]:
        """Session-scoped advisory lock guarding build against build.

        The key differs from the monitor's, which is the whole point: the two
        jobs share a database and must not serialise against each other.
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                'SELECT pg_try_advisory_lock(%s) AS acquired', (ADVISORY_LOCK_KEY,)
            )
            row = cursor.fetchone()
            if not row or not row['acquired']:
                raise LockNotAcquiredError(
                    'another onchain run holds the advisory lock'
                )
        # Session-scoped, so it must not be taken inside the build transaction:
        # a rollback would not release it and a commit would not either.
        self.connection.commit()
        try:
            yield
        finally:
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_unlock(%s)', (ADVISORY_LOCK_KEY,))
                self.connection.commit()
            except psycopg.Error:
                logger.warning('could not release the advisory lock; connection closed')

    # -- entities and sources --------------------------------------------

    def upsert_entity(
        self,
        *,
        level: str,
        key: str,
        display_name: Optional[str] = None,
        parent_id: Optional[int] = None,
        attrs: Optional[Dict[str, Any]] = None,
    ) -> int:
        if level not in LEVELS:
            raise ValueError(f'unknown entity level {level!r}')
        with self.connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {ONCHAIN_SCHEMA}.entity '
                '(level, key, display_name, parent_id, attrs_json) '
                'VALUES (%s, %s, %s, %s, %s) '
                'ON CONFLICT (key) DO UPDATE SET '
                '  level = EXCLUDED.level, '
                '  display_name = COALESCE(EXCLUDED.display_name, '
                f'    {ONCHAIN_SCHEMA}.entity.display_name), '
                '  parent_id = COALESCE(EXCLUDED.parent_id, '
                f'    {ONCHAIN_SCHEMA}.entity.parent_id), '
                '  attrs_json = EXCLUDED.attrs_json, '
                '  updated_at = now() '
                'RETURNING id',
                (level, key, display_name, parent_id, json.dumps(attrs or {})),
            )
            return int(cursor.fetchone()['id'])

    def get_entity_by_key(self, key: str) -> Optional[Dict[str, Any]]:
        return self.fetch_one(
            f'SELECT * FROM {ONCHAIN_SCHEMA}.entity WHERE key = %s', (key,)
        )

    def get_children(self, parent_id: int) -> List[Dict[str, Any]]:
        return self.fetch_all(
            f'SELECT * FROM {ONCHAIN_SCHEMA}.entity WHERE parent_id = %s ORDER BY id',
            (parent_id,),
        )

    def upsert_source(
        self,
        *,
        source_class: str,
        url_or_handle: str,
        admission: str,
        admitted_by: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Idempotent per (class, url).

        `admitted_at` is set by the database on the first admission and left
        alone afterwards: it records when the operator (or the registry) let the
        source in, and a nightly re-upsert must not keep moving it forward.

        `admission` itself is only ever RAISED from `candidate`. A source the
        operator later suspends or retires keeps that status, because the
        registry file re-upserts every night and would otherwise silently undo
        the operator's decision.
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {ONCHAIN_SCHEMA}.source '
                '(class, url_or_handle, admission, admitted_by, admitted_at, evidence_json) '
                'VALUES (%s, %s, %s, %s, CASE WHEN %s = \'admitted\' THEN now() END, %s) '
                'ON CONFLICT (class, url_or_handle) DO UPDATE SET '
                # The nightly re-upsert must never out-rank the operator. Only a
                # `candidate` row is raised by the registry; once a source has an
                # operator-set status ('admitted', 'suspended', 'retired' -- P6,
                # phase 2), the stored value stands and the file cannot revert a
                # suspension on the next build.
                f'  admission = CASE WHEN {ONCHAIN_SCHEMA}.source.admission '
                "    = 'candidate' THEN EXCLUDED.admission "
                f'    ELSE {ONCHAIN_SCHEMA}.source.admission END, '
                # `admitted_by` follows `admission`: the record of WHO set a
                # status is part of the status, so a suspension by the operator
                # must not end up attributed to the registry file.
                f'  admitted_by = CASE WHEN {ONCHAIN_SCHEMA}.source.admission '
                "    = 'candidate' THEN COALESCE(EXCLUDED.admitted_by, "
                f'      {ONCHAIN_SCHEMA}.source.admitted_by) '
                f'    ELSE {ONCHAIN_SCHEMA}.source.admitted_by END, '
                '  admitted_at = COALESCE('
                f'    {ONCHAIN_SCHEMA}.source.admitted_at, EXCLUDED.admitted_at), '
                '  evidence_json = EXCLUDED.evidence_json '
                'RETURNING id',
                (
                    source_class,
                    url_or_handle,
                    admission,
                    admitted_by,
                    admission,
                    json.dumps(evidence or {}),
                ),
            )
            return int(cursor.fetchone()['id'])

    def link_source_to_entity(self, source_id: int, entity_id: int) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {ONCHAIN_SCHEMA}.source_entity (source_id, entity_id) '
                'VALUES (%s, %s) ON CONFLICT DO NOTHING',
                (source_id, entity_id),
            )

    def get_sources_for_entity(self, entity_id: int) -> List[Dict[str, Any]]:
        return self.fetch_all(
            f'SELECT s.* FROM {ONCHAIN_SCHEMA}.source s '
            f'JOIN {ONCHAIN_SCHEMA}.source_entity se ON se.source_id = s.id '
            'WHERE se.entity_id = %s ORDER BY s.id',
            (entity_id,),
        )

    # -- run ledger ------------------------------------------------------

    def start_run(self, job: str) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {ONCHAIN_SCHEMA}.run (job, outcome) "
                "VALUES (%s, 'running') RETURNING id",
                (job,),
            )
            run_id = int(cursor.fetchone()['id'])
        self.connection.commit()
        return run_id

    def finish_run(
        self,
        run_id: int,
        *,
        outcome: str,
        failed_units: Optional[List[Any]] = None,
        spend: Optional[Dict[str, Any]] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Written before any alert is attempted, so an alert failure cannot
        cost us the record of what the run did (P13)."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                f'UPDATE {ONCHAIN_SCHEMA}.run SET outcome = %s, failed_units_json = %s, '
                'spend_json = %s, notes = %s, finished_at = now() WHERE id = %s',
                (
                    outcome,
                    json.dumps(failed_units or []),
                    json.dumps(spend or {}),
                    notes,
                    run_id,
                ),
            )
        self.connection.commit()

    def get_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        return self.fetch_one(
            f'SELECT * FROM {ONCHAIN_SCHEMA}.run WHERE id = %s', (run_id,)
        )

    def get_latest_run(self, job: str) -> Optional[Dict[str, Any]]:
        return self.fetch_one(
            f'SELECT * FROM {ONCHAIN_SCHEMA}.run WHERE job = %s '
            'ORDER BY started_at DESC LIMIT 1',
            (job,),
        )

    def get_runs(self, job: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
        if job is None:
            return self.fetch_all(
                f'SELECT * FROM {ONCHAIN_SCHEMA}.run ORDER BY started_at DESC LIMIT %s',
                (limit,),
            )
        return self.fetch_all(
            f'SELECT * FROM {ONCHAIN_SCHEMA}.run WHERE job = %s '
            'ORDER BY started_at DESC LIMIT %s',
            (job, limit),
        )

    # -- builds and sections ---------------------------------------------

    def start_build(
        self,
        *,
        run_id: int,
        project_id: int,
        block: Optional[int] = None,
        block_timestamp: Optional[int] = None,
    ) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {ONCHAIN_SCHEMA}.build '
                "(run_id, project_id, block, block_timestamp, outcome) "
                "VALUES (%s, %s, %s, %s, 'running') RETURNING id",
                (run_id, project_id, block, block_timestamp),
            )
            return int(cursor.fetchone()['id'])

    def finish_build(
        self,
        build_id: int,
        *,
        outcome: str,
        failed_units: Optional[List[Any]] = None,
        threshold_version: Optional[str] = None,
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                f'UPDATE {ONCHAIN_SCHEMA}.build SET outcome = %s, failed_units_json = %s, '
                'threshold_version = %s, finished_at = now() WHERE id = %s',
                (outcome, json.dumps(failed_units or []), threshold_version, build_id),
            )

    def insert_section(
        self,
        *,
        build_id: int,
        name: str,
        status: str,
        fields: Dict[str, Any],
        span_id: Optional[str] = None,
        error_class: Optional[str] = None,
        evidence_ids: Optional[Sequence[int]] = None,
    ) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {ONCHAIN_SCHEMA}.section '
                '(build_id, name, span_id, status, error_class, fields_json, evidence_ids) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id',
                (
                    build_id,
                    name,
                    span_id,
                    status,
                    error_class,
                    json.dumps(fields, sort_keys=True),
                    list(evidence_ids or []),
                ),
            )
            return int(cursor.fetchone()['id'])

    def get_latest_section(
        self, project_id: int, name: str, *, before_section_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """The most recent build of one section, whatever its age.

        `before_section_id` excludes the section currently being written, so a
        build diffs against its predecessor and not against itself. Only `ok`
        and `partial` sections are baselines: a `failed` section holds no fields
        to diff against (A3).
        """
        params: List[Any] = [project_id, name]
        clause = ''
        if before_section_id is not None:
            clause = 'AND s.id < %s '
            params.append(before_section_id)
        return self.fetch_one(
            f'SELECT s.* FROM {ONCHAIN_SCHEMA}.section s '
            f'JOIN {ONCHAIN_SCHEMA}.build b ON b.id = s.build_id '
            "WHERE b.project_id = %s AND s.name = %s AND s.status IN ('ok', 'partial') "
            f'{clause}'
            'ORDER BY s.id DESC LIMIT 1',
            params,
        )

    def get_sections_for_build(self, build_id: int) -> List[Dict[str, Any]]:
        return self.fetch_all(
            f'SELECT * FROM {ONCHAIN_SCHEMA}.section WHERE build_id = %s ORDER BY id',
            (build_id,),
        )

    def get_latest_build(self, project_id: int) -> Optional[Dict[str, Any]]:
        return self.fetch_one(
            f'SELECT * FROM {ONCHAIN_SCHEMA}.build WHERE project_id = %s '
            'ORDER BY id DESC LIMIT 1',
            (project_id,),
        )

    def insert_section_diff(
        self,
        *,
        section_id: int,
        previous_section_id: Optional[int],
        changes: Dict[str, Any],
        flagged: Optional[List[Any]] = None,
        threshold_version: Optional[str] = None,
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {ONCHAIN_SCHEMA}.section_diff '
                '(section_id, previous_section_id, changes_json, flagged_json, threshold_version) '
                'VALUES (%s, %s, %s, %s, %s) '
                'ON CONFLICT (section_id) DO UPDATE SET '
                '  previous_section_id = EXCLUDED.previous_section_id, '
                '  changes_json = EXCLUDED.changes_json, '
                '  flagged_json = EXCLUDED.flagged_json, '
                '  threshold_version = EXCLUDED.threshold_version',
                (
                    section_id,
                    previous_section_id,
                    json.dumps(changes, sort_keys=True),
                    json.dumps(flagged or []),
                    threshold_version,
                ),
            )

    def get_section_diff(self, section_id: int) -> Optional[Dict[str, Any]]:
        return self.fetch_one(
            f'SELECT * FROM {ONCHAIN_SCHEMA}.section_diff WHERE section_id = %s',
            (section_id,),
        )

    # -- evidence --------------------------------------------------------

    def insert_evidence(
        self,
        *,
        run_id: int,
        entity_id: int,
        kind: str,
        method_or_url: str,
        body: Any,
        endpoint_kind: str,
        span_id: Optional[str] = None,
        params: Optional[Any] = None,
        block: Optional[int] = None,
    ) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {ONCHAIN_SCHEMA}.evidence '
                '(run_id, span_id, entity_id, kind, method_or_url, params_json, '
                ' body_json, endpoint_kind, block) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id',
                (
                    run_id,
                    span_id,
                    entity_id,
                    kind,
                    method_or_url,
                    None if params is None else json.dumps(params),
                    json.dumps(body),
                    endpoint_kind,
                    block,
                ),
            )
            return int(cursor.fetchone()['id'])

    def get_evidence_for_run(self, run_id: int) -> List[Dict[str, Any]]:
        return self.fetch_all(
            f'SELECT * FROM {ONCHAIN_SCHEMA}.evidence WHERE run_id = %s ORDER BY id',
            (run_id,),
        )

    # -- log-derived rows ------------------------------------------------

    def insert_transfers(self, token_id: int, rows: Sequence[Dict[str, Any]]) -> int:
        """Insert-or-ignore on (tx_hash, log_index): a re-fetched window is
        harmless rather than a duplicate. Returns rows actually inserted, so a
        re-run can report "0 new" instead of looking like it fetched again."""
        inserted = 0
        with self.connection.cursor() as cursor:
            for row in rows:
                cursor.execute(
                    f'INSERT INTO {ONCHAIN_SCHEMA}.transfer '
                    '(token_id, block, tx_hash, log_index, from_addr, to_addr, amount) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                    (
                        token_id,
                        row['block'],
                        row['tx_hash'],
                        row['log_index'],
                        row['from_addr'],
                        row['to_addr'],
                        str(row['amount']),
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    def insert_position_events(
        self, pool_id: int, rows: Sequence[Dict[str, Any]]
    ) -> int:
        inserted = 0
        with self.connection.cursor() as cursor:
            for row in rows:
                cursor.execute(
                    f'INSERT INTO {ONCHAIN_SCHEMA}.position_event '
                    '(pool_id, block, tx_hash, log_index, kind, owner, nft_token_id, '
                    ' tick_lower, tick_upper, liquidity_delta, salt) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) '
                    # Attribution is the ONLY thing a re-fetch may rewrite. The
                    # chain facts -- block, kind, ticks, delta, salt -- came from
                    # the same log and cannot have changed, so overwriting them
                    # would be noise; owner and token id are derived, and a build
                    # that derives them better must be able to repair what an
                    # earlier one wrote. Without this, a store carrying rows from
                    # before the 2026-09-08 netting fix keeps them for ever.
                    'ON CONFLICT (tx_hash, log_index) DO UPDATE SET '
                    ' owner = EXCLUDED.owner, nft_token_id = EXCLUDED.nft_token_id',
                    (
                        pool_id,
                        row['block'],
                        row['tx_hash'],
                        row['log_index'],
                        row['kind'],
                        row.get('owner'),
                        None if row.get('nft_token_id') is None else str(row['nft_token_id']),
                        row.get('tick_lower'),
                        row.get('tick_upper'),
                        None
                        if row.get('liquidity_delta') is None
                        else str(row['liquidity_delta']),
                        row.get('salt'),
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    def get_fetch_cursor(self, entity_id: int, stream: str) -> Optional[int]:
        row = self.fetch_one(
            f'SELECT last_block FROM {ONCHAIN_SCHEMA}.fetch_cursor '
            'WHERE entity_id = %s AND stream = %s',
            (entity_id, stream),
        )
        return None if row is None else int(row['last_block'])

    def set_fetch_cursor(self, entity_id: int, stream: str, last_block: int) -> None:
        """Monotonic: a cursor never moves backwards, so a re-run over an older
        window cannot make the store re-fetch everything after it."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {ONCHAIN_SCHEMA}.fetch_cursor (entity_id, stream, last_block) '
                'VALUES (%s, %s, %s) '
                'ON CONFLICT (entity_id, stream) DO UPDATE SET '
                f'  last_block = GREATEST({ONCHAIN_SCHEMA}.fetch_cursor.last_block, '
                '                        EXCLUDED.last_block), '
                '  updated_at = now()',
                (entity_id, stream, last_block),
            )

    # -- derived reads over the transfer history -------------------------
    #
    # These aggregate in SQL rather than in Python because the holder set is
    # recomputed over the FULL history on every build, and a ZZZ-like project was
    # sized at ~1.8M transfer rows a month (design, Scaling envelope). Pulling
    # those rows into the process to sum them is the step that binds first; the
    # replacement, when it does, is an incremental balance table updated per
    # fetched window, and these method signatures are what it would keep.

    def holder_balances(
        self,
        token_id: int,
        *,
        exclude: Optional[Sequence[str]] = None,
        limit: Optional[int] = None,
    ) -> List[Tuple[str, int]]:
        """Net balance per address over the whole stored history, largest first.

        Net, not gross: every address that ever touched the token contributes
        `sum(received) - sum(sent)`, and only a positive result is a holder. An
        address that received and then sent everything nets to zero and is not in
        the result -- which is why the holder COUNT out of this is a count of
        current holders and not of everyone who ever held.
        """
        excluded = [address.lower() for address in (exclude or [])]
        rows = self.fetch_all(
            f"""
            WITH moves AS (
                SELECT to_addr AS addr, amount FROM {ONCHAIN_SCHEMA}.transfer
                    WHERE token_id = %s
                UNION ALL
                SELECT from_addr AS addr, -amount FROM {ONCHAIN_SCHEMA}.transfer
                    WHERE token_id = %s
            )
            SELECT addr, SUM(amount) AS balance FROM moves
            WHERE NOT (addr = ANY(%s))
            GROUP BY addr HAVING SUM(amount) > 0
            ORDER BY SUM(amount) DESC, addr
            {'LIMIT %s' if limit is not None else ''}
            """,
            (token_id, token_id, excluded) + ((limit,) if limit is not None else ()),
        )
        return [(row['addr'], int(row['balance'])) for row in rows]

    def transfer_row_count(self, token_id: int) -> int:
        row = self.fetch_one(
            f'SELECT count(*) AS n FROM {ONCHAIN_SCHEMA}.transfer WHERE token_id = %s',
            (token_id,),
        )
        return 0 if row is None else int(row['n'])

    def active_addresses(self, token_id: int, from_block: int, to_block: int) -> int:
        """Distinct addresses on either side of a transfer in the window.

        The counterpart to the provider's 24-hour VOLUME: volume without new
        counterparties is what wash trading looks like from outside.
        """
        row = self.fetch_one(
            f"""
            SELECT count(DISTINCT addr) AS n FROM (
                SELECT from_addr AS addr FROM {ONCHAIN_SCHEMA}.transfer
                    WHERE token_id = %s AND block BETWEEN %s AND %s
                UNION
                SELECT to_addr AS addr FROM {ONCHAIN_SCHEMA}.transfer
                    WHERE token_id = %s AND block BETWEEN %s AND %s
            ) both_sides
            """,
            (token_id, from_block, to_block, token_id, from_block, to_block),
        )
        return 0 if row is None else int(row['n'])

    def pool_counterparties(
        self,
        token_id: int,
        pool_addresses: Sequence[str],
        from_block: int,
        to_block: int,
    ) -> int:
        """Distinct addresses trading against the pool in the window.

        The counterpart to the provider's 24-hour TRADE COUNT. "Touching the
        pool" is defined per pool type by the caller, which is why the addresses
        are a parameter: a v4 pool has no address of its own, so its
        counterparties are found through the pool manager and the hook.
        """
        addresses = [address.lower() for address in pool_addresses if address]
        if not addresses:
            return 0
        row = self.fetch_one(
            f"""
            SELECT count(DISTINCT addr) AS n FROM (
                SELECT to_addr AS addr FROM {ONCHAIN_SCHEMA}.transfer
                    WHERE token_id = %s AND block BETWEEN %s AND %s
                      AND from_addr = ANY(%s) AND NOT (to_addr = ANY(%s))
                UNION
                SELECT from_addr AS addr FROM {ONCHAIN_SCHEMA}.transfer
                    WHERE token_id = %s AND block BETWEEN %s AND %s
                      AND to_addr = ANY(%s) AND NOT (from_addr = ANY(%s))
            ) counterparties
            """,
            (
                token_id, from_block, to_block, addresses, addresses,
                token_id, from_block, to_block, addresses, addresses,
            ),
        )
        return 0 if row is None else int(row['n'])

    def new_versus_returning(
        self, token_id: int, from_block: int, to_block: int
    ) -> Dict[str, int]:
        """Receivers in the window split by whether the token had ever reached
        them before it.

        The counterpart to the HOLDER COUNT: one person splitting a balance
        across twenty wallets raises the holder count and shows up here as twenty
        new addresses on one day, which is the shape wallet-splitting has.
        """
        row = self.fetch_one(
            f"""
            WITH first_seen AS (
                SELECT to_addr AS addr, min(block) AS first_block
                FROM {ONCHAIN_SCHEMA}.transfer WHERE token_id = %s GROUP BY to_addr
            ),
            in_window AS (
                SELECT DISTINCT to_addr AS addr FROM {ONCHAIN_SCHEMA}.transfer
                WHERE token_id = %s AND block BETWEEN %s AND %s
            )
            SELECT
                count(*) FILTER (WHERE first_seen.first_block >= %s) AS new_addresses,
                count(*) FILTER (WHERE first_seen.first_block <  %s) AS returning_addresses
            FROM in_window JOIN first_seen USING (addr)
            """,
            (token_id, token_id, from_block, to_block, from_block, from_block),
        )
        if row is None:
            return {'new': 0, 'returning': 0}
        return {
            'new': int(row['new_addresses'] or 0),
            'returning': int(row['returning_addresses'] or 0),
        }

    def burned_amount(
        self, token_id: int, sinks: Sequence[str], *, mint_source: str
    ) -> int:
        """Everything ever sent to a burn sink, minus anything spent back out of
        a sink that is a real address.

        `mint_source` -- the zero address -- is a sink for the first sum and is
        EXCLUDED from the second, and that asymmetry is the whole subtlety: a
        transfer *from* `0x0` is a mint, not an un-burn. Subtracting mints made
        the burn figure negative by the entire minted supply, which on a
        fixed-supply launchpad token is every token there is. `0x…dEaD`, by
        contrast, is an ordinary address a contract could in principle spend
        from, so an outflow from it genuinely un-burns.
        """
        to_sinks = [address.lower() for address in sinks]
        spendable = [address for address in to_sinks if address != mint_source.lower()]
        row = self.fetch_one(
            f"""
            SELECT
                COALESCE(SUM(amount) FILTER (WHERE to_addr = ANY(%s)), 0)
              - COALESCE(SUM(amount) FILTER (WHERE from_addr = ANY(%s)), 0) AS burned
            FROM {ONCHAIN_SCHEMA}.transfer WHERE token_id = %s
            """,
            (to_sinks, spendable, token_id),
        )
        return 0 if row is None else int(row['burned'] or 0)

    def get_position_events(self, pool_id: int) -> List[Dict[str, Any]]:
        return self.fetch_all(
            f'SELECT * FROM {ONCHAIN_SCHEMA}.position_event WHERE pool_id = %s '
            'ORDER BY block, log_index',
            (pool_id,),
        )

    def get_builds_for_project(
        self, project_id: int, limit: int = 30
    ) -> List[Dict[str, Any]]:
        return self.fetch_all(
            f'SELECT * FROM {ONCHAIN_SCHEMA}.build WHERE project_id = %s '
            'ORDER BY id DESC LIMIT %s',
            (project_id, limit),
        )

    def get_build(self, build_id: int) -> Optional[Dict[str, Any]]:
        return self.fetch_one(
            f'SELECT * FROM {ONCHAIN_SCHEMA}.build WHERE id = %s', (build_id,)
        )

    def get_projects(self) -> List[Dict[str, Any]]:
        return self.fetch_all(
            f"SELECT * FROM {ONCHAIN_SCHEMA}.entity WHERE level = 'project' "
            'ORDER BY key'
        )

    # -- thresholds ------------------------------------------------------

    def get_threshold_version(self, version: str) -> Optional[Dict[str, Any]]:
        return self.fetch_one(
            f'SELECT * FROM {ONCHAIN_SCHEMA}.threshold_version WHERE version = %s',
            (version,),
        )

    def record_threshold_version(
        self, *, version: str, first_seen_commit: Optional[str], table: Dict[str, Any]
    ) -> None:
        """First write wins: `first_seen_at` is the moment the version was first
        scored against, and A9 compares it with the commit that introduced it.
        A later run must not move it."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {ONCHAIN_SCHEMA}.threshold_version '
                '(version, first_seen_commit, table_json) VALUES (%s, %s, %s) '
                'ON CONFLICT (version) DO NOTHING',
                (version, first_seen_commit, json.dumps(table, sort_keys=True)),
            )
