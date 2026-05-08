import datetime
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from src.config import config
from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE, RuntimeMode
from src.service.crypto_signal.models import (
    CryptoSignalCandidateCohort,
    CryptoSignalCandidateOutcome,
    CryptoSignalCoinSnapshot,
    CryptoSignalDigestView,
    CryptoSignalMarketRegimeMetric,
    CryptoSignalMarketRegimeSnapshot,
    CryptoSignalRunRecord,
    CryptoSignalSnapshot,
)


SNAPSHOT_VERSION = 1
BTC_COIN_ID = 1
ETH_COIN_ID = 1027
OUTCOME_WINDOWS = {
    '24h': datetime.timedelta(days=1),
    '3d': datetime.timedelta(days=3),
    '7d': datetime.timedelta(days=7),
}
OUTCOME_MAX_FOLLOW_UP_LAG = datetime.timedelta(hours=12)
OUTCOME_STATUS_PENDING = 'pending'
OUTCOME_STATUS_RESOLVED = 'resolved'
OUTCOME_STATUS_MISSING = 'missing'

SCHEMA_DOCS = {
    'crypto_signal_metadata': {
        'key': 'Metadata key. Phase 1 uses schema_version.',
        'value': 'Metadata value stored as text.',
    },
    'crypto_signal_runs': {
        'run_id': 'Synthetic run identifier used by child coin snapshot rows.',
        'run_timestamp_utc': 'Canonical UTC observation timestamp for one crypto job run.',
        'runtime_mode': 'Runtime mode label used when the job captured this snapshot.',
        'source_name': 'Human-readable provider summary for the snapshot source.',
        'snapshot_version': 'Snapshot schema version for forward compatibility.',
        'sentiment_now_value': 'Alternative.me fear and greed value for Now.',
        'sentiment_now_label': 'Alternative.me sentiment label for Now.',
        'sentiment_yesterday_value': 'Alternative.me fear and greed value for Yesterday.',
        'sentiment_last_week_value': 'Alternative.me fear and greed value for Last week.',
        'sentiment_7d_avg': 'Seven-day fear and greed average when available.',
        'sentiment_30d_avg': 'Thirty-day fear and greed average when available.',
        'strongest_sector_id': 'CMC sector id for the strongest 24h sector.',
        'strongest_sector_name': 'CMC strongest-sector display name.',
        'strongest_sector_avg_price_change_24h': 'Average 24h price change for the strongest sector.',
        'strongest_sector_market_change_24h': 'Aggregate 24h market-cap change for the strongest sector.',
        'strongest_sector_volume_change_24h': 'Aggregate 24h volume change for the strongest sector.',
        'strongest_sector_gainers_num': 'Reported count of gainers inside the strongest sector.',
        'strongest_sector_losers_num': 'Reported count of losers inside the strongest sector.',
        'weakest_sector_id': 'CMC sector id for the weakest 24h sector.',
        'weakest_sector_name': 'CMC weakest-sector display name.',
        'weakest_sector_avg_price_change_24h': 'Average 24h price change for the weakest sector.',
        'weakest_sector_market_change_24h': 'Aggregate 24h market-cap change for the weakest sector.',
        'weakest_sector_volume_change_24h': 'Aggregate 24h volume change for the weakest sector.',
        'weakest_sector_gainers_num': 'Reported count of gainers inside the weakest sector.',
        'weakest_sector_losers_num': 'Reported count of losers inside the weakest sector.',
        'created_at_utc': 'UTC write timestamp for the persisted run row.',
    },
    'crypto_signal_coin_snapshots': {
        'run_id': 'Foreign key to crypto_signal_runs.',
        'coin_id': 'CMC coin identifier.',
        'symbol': 'Coin ticker symbol at observation time.',
        'name': 'Coin display name at observation time.',
        'price_usd': 'Latest USD price captured by the digest job.',
        'price_change_24h': 'Observed 24h price change percentage.',
        'volume_24h': 'Observed 24h volume in USD terms.',
        'volume_change_pct_24h': 'Observed 24h volume change percentage.',
        'is_watchlist': '1 when the configured watchlist included this coin for the run.',
        'context_tags_json': 'Stable JSON array describing how the coin entered the snapshot.',
        'created_at_utc': 'UTC write timestamp for the persisted coin row.',
    },
    'crypto_signal_market_regime_snapshots': {
        'snapshot_id': 'Synthetic market-regime snapshot identifier.',
        'observed_at_utc': 'UTC observation timestamp for the regime collector run.',
        'runtime_mode': 'Runtime mode label used when the regime snapshot was captured.',
        'provider': 'Provider name, such as coinalyze or binance.',
        'asset_symbol': 'Benchmark asset symbol, initially BTC.',
        'venue_scope': 'Exchange or aggregate venue scope.',
        'instrument_scope': 'Provider instrument scope or symbol.',
        'interval': 'Sampling interval for the stored metrics.',
        'source_payload_version': 'Provider payload mapping version.',
        'created_at_utc': 'UTC write timestamp for the persisted regime row.',
    },
    'crypto_signal_market_regime_metrics': {
        'snapshot_id': 'Foreign key to crypto_signal_market_regime_snapshots.',
        'metric_name': 'Stable metric name, such as open_interest_usd or funding_rate.',
        'metric_value': 'Numeric metric value when provider data is available.',
        'unit': 'Metric unit, such as usd or percent.',
        'source_timestamp_utc': 'Provider source timestamp for the metric value.',
        'created_at_utc': 'UTC write timestamp for the persisted metric row.',
    },
    'crypto_signal_candidate_cohorts': {
        'cohort_id': 'Synthetic emitted-candidate cohort identifier.',
        'signal_run_timestamp_utc': 'UTC timestamp for the operator signal run.',
        'runtime_mode': 'Runtime mode label used when the signal rendered.',
        'window_label': 'Primary signal window used for ranking, such as 7d.',
        'section': 'Rendered section that emitted the candidate.',
        'coin_id': 'CMC coin identifier for the emitted candidate.',
        'symbol': 'Candidate ticker symbol at render time.',
        'name': 'Candidate display name at render time.',
        'baseline_price_usd': 'Candidate price at render time.',
        'score': 'Rendered candidate score.',
        'reason_tags_json': 'Stable JSON array of rendered reason tags.',
        'market_regime_label': 'Market-regime label rendered with the message.',
        'market_regime_reason': 'Market-regime reason rendered with the message.',
        'created_at_utc': 'UTC write timestamp for the cohort row.',
    },
    'crypto_signal_candidate_outcomes': {
        'cohort_id': 'Foreign key to crypto_signal_candidate_cohorts.',
        'outcome_window': 'Forward window such as 24h, 3d, or 7d.',
        'target_timestamp_utc': 'UTC target timestamp for the forward outcome.',
        'status': 'resolved or missing.',
        'candidate_price_usd': 'Candidate price used for outcome calculation.',
        'btc_price_usd': 'BTC benchmark price at the outcome timestamp.',
        'eth_price_usd': 'ETH benchmark price at the outcome timestamp.',
        'absolute_return_pct': 'Candidate forward return from baseline price.',
        'btc_relative_return_pct': 'Candidate return minus BTC return.',
        'eth_relative_return_pct': 'Candidate return minus ETH return.',
        'missing_reason': 'Reason an outcome could not be resolved.',
    },
}


class CryptoSignalRepository:
    def __init__(
        self,
        db_path: str | None = None,
        runtime_mode: RuntimeMode | None = None,
    ) -> None:
        active_runtime_mode = (
            DEFAULT_RUNTIME_MODE if runtime_mode is None else runtime_mode
        )
        self.db_path = db_path or config.get_crypto_signal_db_path(
            runtime_mode=active_runtime_mode
        )

    def init_schema(self) -> None:
        db_parent = Path(self.db_path).parent
        db_parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_signal_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_signal_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_timestamp_utc TEXT NOT NULL UNIQUE,
                    runtime_mode TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    snapshot_version INTEGER NOT NULL,
                    sentiment_now_value REAL NULL,
                    sentiment_now_label TEXT NULL,
                    sentiment_yesterday_value REAL NULL,
                    sentiment_last_week_value REAL NULL,
                    sentiment_7d_avg REAL NULL,
                    sentiment_30d_avg REAL NULL,
                    strongest_sector_id TEXT NULL,
                    strongest_sector_name TEXT NULL,
                    strongest_sector_avg_price_change_24h REAL NULL,
                    strongest_sector_market_change_24h REAL NULL,
                    strongest_sector_volume_change_24h REAL NULL,
                    strongest_sector_gainers_num INTEGER NULL,
                    strongest_sector_losers_num INTEGER NULL,
                    weakest_sector_id TEXT NULL,
                    weakest_sector_name TEXT NULL,
                    weakest_sector_avg_price_change_24h REAL NULL,
                    weakest_sector_market_change_24h REAL NULL,
                    weakest_sector_volume_change_24h REAL NULL,
                    weakest_sector_gainers_num INTEGER NULL,
                    weakest_sector_losers_num INTEGER NULL,
                    created_at_utc TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_signal_coin_snapshots (
                    run_id INTEGER NOT NULL,
                    coin_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    price_usd REAL NULL,
                    price_change_24h REAL NULL,
                    volume_24h REAL NULL,
                    volume_change_pct_24h REAL NULL,
                    is_watchlist INTEGER NOT NULL,
                    context_tags_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    PRIMARY KEY (run_id, coin_id),
                    FOREIGN KEY (run_id) REFERENCES crypto_signal_runs(run_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crypto_signal_coin_snapshots_coin_id_run_id
                ON crypto_signal_coin_snapshots (coin_id, run_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crypto_signal_coin_snapshots_symbol_run_id
                ON crypto_signal_coin_snapshots (symbol, run_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_signal_market_regime_snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at_utc TEXT NOT NULL,
                    runtime_mode TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    asset_symbol TEXT NOT NULL,
                    venue_scope TEXT NOT NULL,
                    instrument_scope TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    source_payload_version INTEGER NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    UNIQUE (
                        observed_at_utc,
                        runtime_mode,
                        provider,
                        asset_symbol,
                        venue_scope,
                        instrument_scope,
                        interval
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_signal_market_regime_metrics (
                    snapshot_id INTEGER NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value REAL NULL,
                    unit TEXT NOT NULL,
                    source_timestamp_utc TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    PRIMARY KEY (snapshot_id, metric_name, source_timestamp_utc),
                    FOREIGN KEY (snapshot_id)
                        REFERENCES crypto_signal_market_regime_snapshots(snapshot_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crypto_signal_market_regime_metrics_name_time
                ON crypto_signal_market_regime_metrics (metric_name, source_timestamp_utc)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_signal_candidate_cohorts (
                    cohort_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_run_timestamp_utc TEXT NOT NULL,
                    runtime_mode TEXT NOT NULL,
                    window_label TEXT NOT NULL,
                    section TEXT NOT NULL,
                    coin_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    baseline_price_usd REAL NULL,
                    latest_price_change_24h REAL NULL,
                    window_price_change_pct REAL NULL,
                    score INTEGER NOT NULL,
                    price_persistence_score INTEGER NOT NULL,
                    volume_confirmation_score INTEGER NOT NULL,
                    attention_persistence_score INTEGER NOT NULL,
                    breadth_alignment_score INTEGER NOT NULL,
                    observation_count INTEGER NOT NULL,
                    reason_tags_json TEXT NOT NULL,
                    flags_json TEXT NOT NULL,
                    market_regime_label TEXT NOT NULL,
                    market_regime_reason TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    UNIQUE (
                        signal_run_timestamp_utc,
                        runtime_mode,
                        window_label,
                        section,
                        coin_id
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crypto_signal_candidate_cohorts_unresolved
                ON crypto_signal_candidate_cohorts (
                    runtime_mode,
                    signal_run_timestamp_utc,
                    coin_id
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crypto_signal_candidate_outcomes (
                    cohort_id INTEGER NOT NULL,
                    outcome_window TEXT NOT NULL,
                    target_timestamp_utc TEXT NOT NULL,
                    status TEXT NOT NULL,
                    candidate_price_usd REAL NULL,
                    btc_price_usd REAL NULL,
                    eth_price_usd REAL NULL,
                    absolute_return_pct REAL NULL,
                    btc_relative_return_pct REAL NULL,
                    eth_relative_return_pct REAL NULL,
                    missing_reason TEXT NULL,
                    resolved_run_timestamp_utc TEXT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (cohort_id, outcome_window),
                    FOREIGN KEY (cohort_id)
                        REFERENCES crypto_signal_candidate_cohorts(cohort_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_crypto_signal_candidate_outcomes_due
                ON crypto_signal_candidate_outcomes (
                    status,
                    target_timestamp_utc
                )
                """
            )
            # Current phase-1 reads filter one run via the (run_id, coin_id)
            # primary key, then sort a small per-run coin set in memory. Add a
            # (run_id, symbol, coin_id) index only if that per-run sort becomes
            # visible at a larger retained universe size.
            connection.execute(
                """
                INSERT INTO crypto_signal_metadata (key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(SNAPSHOT_VERSION),),
            )
            connection.commit()

    def save_snapshot(self, snapshot: CryptoSignalSnapshot) -> CryptoSignalSnapshot:
        self.init_schema()
        created_at_utc = self._utcnow()

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO crypto_signal_runs (
                    run_timestamp_utc,
                    runtime_mode,
                    source_name,
                    snapshot_version,
                    sentiment_now_value,
                    sentiment_now_label,
                    sentiment_yesterday_value,
                    sentiment_last_week_value,
                    sentiment_7d_avg,
                    sentiment_30d_avg,
                    strongest_sector_id,
                    strongest_sector_name,
                    strongest_sector_avg_price_change_24h,
                    strongest_sector_market_change_24h,
                    strongest_sector_volume_change_24h,
                    strongest_sector_gainers_num,
                    strongest_sector_losers_num,
                    weakest_sector_id,
                    weakest_sector_name,
                    weakest_sector_avg_price_change_24h,
                    weakest_sector_market_change_24h,
                    weakest_sector_volume_change_24h,
                    weakest_sector_gainers_num,
                    weakest_sector_losers_num,
                    created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._format_timestamp(snapshot.run.run_timestamp_utc),
                    snapshot.run.runtime_mode,
                    snapshot.run.source_name,
                    snapshot.run.snapshot_version,
                    snapshot.run.sentiment_now_value,
                    snapshot.run.sentiment_now_label,
                    snapshot.run.sentiment_yesterday_value,
                    snapshot.run.sentiment_last_week_value,
                    snapshot.run.sentiment_7d_avg,
                    snapshot.run.sentiment_30d_avg,
                    snapshot.run.strongest_sector_id,
                    snapshot.run.strongest_sector_name,
                    snapshot.run.strongest_sector_avg_price_change_24h,
                    snapshot.run.strongest_sector_market_change_24h,
                    snapshot.run.strongest_sector_volume_change_24h,
                    snapshot.run.strongest_sector_gainers_num,
                    snapshot.run.strongest_sector_losers_num,
                    snapshot.run.weakest_sector_id,
                    snapshot.run.weakest_sector_name,
                    snapshot.run.weakest_sector_avg_price_change_24h,
                    snapshot.run.weakest_sector_market_change_24h,
                    snapshot.run.weakest_sector_volume_change_24h,
                    snapshot.run.weakest_sector_gainers_num,
                    snapshot.run.weakest_sector_losers_num,
                    self._format_timestamp(created_at_utc),
                ),
            )
            run_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO crypto_signal_coin_snapshots (
                    run_id,
                    coin_id,
                    symbol,
                    name,
                    price_usd,
                    price_change_24h,
                    volume_24h,
                    volume_change_pct_24h,
                    is_watchlist,
                    context_tags_json,
                    created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    self._serialize_coin_snapshot(
                        coin=coin,
                        run_id=run_id,
                        created_at_utc=created_at_utc,
                    )
                    for coin in snapshot.coins
                ],
            )
            connection.commit()

        snapshot.run.run_id = run_id
        snapshot.run.created_at_utc = created_at_utc
        for coin in snapshot.coins:
            coin.run_id = run_id
            coin.created_at_utc = created_at_utc
        return snapshot

    def save_candidate_cohorts_from_view(
        self,
        view: CryptoSignalDigestView,
    ) -> list[CryptoSignalCandidateCohort]:
        self.init_schema()
        created_at_utc = self._utcnow()
        cohorts = self._build_candidate_cohorts(view=view)
        if len(cohorts) == 0:
            return []

        with self._connect() as connection:
            for cohort in cohorts:
                self._upsert_candidate_cohort(
                    connection=connection,
                    cohort=cohort,
                    created_at_utc=created_at_utc,
                )
                cohort_id = self._select_candidate_cohort_id(
                    connection=connection,
                    cohort=cohort,
                )
                cohort.cohort_id = cohort_id
                cohort.created_at_utc = created_at_utc
                self._upsert_candidate_outcome_placeholders(
                    connection=connection,
                    cohort=cohort,
                    created_at_utc=created_at_utc,
                )
            connection.commit()
        return cohorts

    def get_unresolved_candidate_follow_up_entries(
        self,
        runtime_mode: str,
        current_timestamp_utc: datetime.datetime,
    ) -> list[tuple[str, int]]:
        if not Path(self.db_path).exists():
            return []
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT DISTINCT cohort.symbol, cohort.coin_id
                    FROM crypto_signal_candidate_cohorts AS cohort
                    INNER JOIN crypto_signal_candidate_outcomes AS outcome
                      ON outcome.cohort_id = cohort.cohort_id
                    WHERE cohort.runtime_mode = ?
                      AND outcome.status NOT IN (?, ?)
                      AND outcome.target_timestamp_utc <= ?
                    ORDER BY cohort.symbol ASC, cohort.coin_id ASC
                    """,
                    (
                        runtime_mode,
                        OUTCOME_STATUS_RESOLVED,
                        OUTCOME_STATUS_MISSING,
                        self._format_timestamp(current_timestamp_utc),
                    ),
                ).fetchall()
            except sqlite3.OperationalError as error:
                if 'no such table' in str(error):
                    return []
                raise
        return [(row['symbol'], int(row['coin_id'])) for row in rows]

    def resolve_due_candidate_outcomes(
        self,
        runtime_mode: str,
        current_timestamp_utc: datetime.datetime,
    ) -> list[CryptoSignalCandidateOutcome]:
        if not Path(self.db_path).exists():
            return []
        self.init_schema()
        updated_at_utc = self._utcnow()

        with self._connect() as connection:
            due_rows = connection.execute(
                """
                SELECT
                    outcome.*,
                    cohort.signal_run_timestamp_utc,
                    cohort.runtime_mode,
                    cohort.coin_id,
                    cohort.baseline_price_usd
                FROM crypto_signal_candidate_outcomes AS outcome
                INNER JOIN crypto_signal_candidate_cohorts AS cohort
                  ON cohort.cohort_id = outcome.cohort_id
                WHERE cohort.runtime_mode = ?
                  AND outcome.status NOT IN (?, ?)
                  AND outcome.target_timestamp_utc <= ?
                ORDER BY outcome.target_timestamp_utc ASC, cohort.coin_id ASC
                """,
                (
                    runtime_mode,
                    OUTCOME_STATUS_RESOLVED,
                    OUTCOME_STATUS_MISSING,
                    self._format_timestamp(current_timestamp_utc),
                ),
            ).fetchall()
            resolved_outcomes = []
            for row in due_rows:
                outcome = self._resolve_candidate_outcome_row(
                    connection=connection,
                    row=row,
                    updated_at_utc=updated_at_utc,
                )
                self._update_candidate_outcome(
                    connection=connection,
                    outcome=outcome,
                    updated_at_utc=updated_at_utc,
                )
                resolved_outcomes.append(outcome)
            connection.commit()
        return resolved_outcomes

    def save_market_regime_snapshot(
        self,
        snapshot: CryptoSignalMarketRegimeSnapshot,
    ) -> CryptoSignalMarketRegimeSnapshot:
        self.init_schema()
        created_at_utc = self._utcnow()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO crypto_signal_market_regime_snapshots (
                    observed_at_utc,
                    runtime_mode,
                    provider,
                    asset_symbol,
                    venue_scope,
                    instrument_scope,
                    interval,
                    source_payload_version,
                    created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    observed_at_utc,
                    runtime_mode,
                    provider,
                    asset_symbol,
                    venue_scope,
                    instrument_scope,
                    interval
                ) DO UPDATE SET
                    source_payload_version=excluded.source_payload_version,
                    created_at_utc=excluded.created_at_utc
                """,
                (
                    self._format_timestamp(snapshot.observed_at_utc),
                    snapshot.runtime_mode,
                    snapshot.provider,
                    snapshot.asset_symbol,
                    snapshot.venue_scope,
                    snapshot.instrument_scope,
                    snapshot.interval,
                    snapshot.source_payload_version,
                    self._format_timestamp(created_at_utc),
                ),
            )
            snapshot_row = connection.execute(
                """
                SELECT snapshot_id
                FROM crypto_signal_market_regime_snapshots
                WHERE observed_at_utc = ?
                  AND runtime_mode = ?
                  AND provider = ?
                  AND asset_symbol = ?
                  AND venue_scope = ?
                  AND instrument_scope = ?
                  AND interval = ?
                """,
                (
                    self._format_timestamp(snapshot.observed_at_utc),
                    snapshot.runtime_mode,
                    snapshot.provider,
                    snapshot.asset_symbol,
                    snapshot.venue_scope,
                    snapshot.instrument_scope,
                    snapshot.interval,
                ),
            ).fetchone()
            snapshot_id = int(snapshot_row['snapshot_id'])
            connection.executemany(
                """
                INSERT INTO crypto_signal_market_regime_metrics (
                    snapshot_id,
                    metric_name,
                    metric_value,
                    unit,
                    source_timestamp_utc,
                    created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (snapshot_id, metric_name, source_timestamp_utc)
                DO UPDATE SET
                    metric_value=excluded.metric_value,
                    unit=excluded.unit,
                    created_at_utc=excluded.created_at_utc
                """,
                [
                    self._serialize_market_regime_metric(
                        metric=metric,
                        snapshot_id=snapshot_id,
                        created_at_utc=created_at_utc,
                    )
                    for metric in snapshot.metrics
                ],
            )
            connection.commit()

        snapshot.snapshot_id = snapshot_id
        snapshot.created_at_utc = created_at_utc
        for metric in snapshot.metrics:
            metric.snapshot_id = snapshot_id
            metric.created_at_utc = created_at_utc
        return snapshot

    def get_market_regime_metrics(
        self,
        runtime_mode: str,
        start_timestamp_utc: datetime.datetime,
        end_timestamp_utc: datetime.datetime,
        metric_names: list[str],
        asset_symbol: str = 'BTC',
        provider: str | None = None,
        venue_scope: str | None = None,
        instrument_scope: str | None = None,
        interval: str | None = None,
    ) -> list[CryptoSignalMarketRegimeMetric]:
        if not metric_names or not Path(self.db_path).exists():
            return []
        placeholders = ','.join('?' for _ in metric_names)
        scope_filters = []
        params = [
            runtime_mode,
            asset_symbol,
            self._format_timestamp(start_timestamp_utc),
            self._format_timestamp(end_timestamp_utc),
        ]
        if provider is not None:
            scope_filters.append('snapshot.provider = ?')
            params.append(provider)
        if venue_scope is not None:
            scope_filters.append('snapshot.venue_scope = ?')
            params.append(venue_scope)
        if instrument_scope is not None:
            scope_filters.append('snapshot.instrument_scope = ?')
            params.append(instrument_scope)
        if interval is not None:
            scope_filters.append('snapshot.interval = ?')
            params.append(interval)
        params.extend(metric_names)
        scope_filter_sql = (
            ''
            if not scope_filters
            else ' AND ' + ' AND '.join(scope_filters)
        )
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    f"""
                    SELECT
                        metric.*,
                        snapshot.observed_at_utc AS snapshot_observed_at_utc,
                        snapshot.provider,
                        snapshot.asset_symbol,
                        snapshot.venue_scope,
                        snapshot.instrument_scope,
                        snapshot.interval
                    FROM crypto_signal_market_regime_metrics AS metric
                    INNER JOIN crypto_signal_market_regime_snapshots AS snapshot
                      ON snapshot.snapshot_id = metric.snapshot_id
                    WHERE snapshot.runtime_mode = ?
                      AND snapshot.asset_symbol = ?
                      AND metric.source_timestamp_utc >= ?
                      AND metric.source_timestamp_utc <= ?
                      {scope_filter_sql}
                      AND metric.metric_name IN ({placeholders})
                    ORDER BY metric.source_timestamp_utc ASC, metric.metric_name ASC
                    """,
                    params,
                ).fetchall()
            except sqlite3.OperationalError as error:
                if 'no such table' in str(error):
                    return []
                raise
        return [
            self._build_market_regime_metric(row)
            for row in self._dedupe_market_regime_metric_rows(rows)
        ]

    def save_or_merge_snapshot(
        self,
        snapshot: CryptoSignalSnapshot,
    ) -> CryptoSignalSnapshot:
        self.init_schema()
        created_at_utc = self._utcnow()

        with self._connect() as connection:
            existing_run = connection.execute(
                """
                SELECT run_id, created_at_utc
                FROM crypto_signal_runs
                WHERE run_timestamp_utc = ?
                """,
                (self._format_timestamp(snapshot.run.run_timestamp_utc),),
            ).fetchone()
            if existing_run is None:
                # Keep the normal write path authoritative for first creation so
                # bootstrap merges and live writes share the same row shape.
                return self.save_snapshot(snapshot)

            run_id = int(existing_run['run_id'])
            # Bootstrap history is assembled coin-by-coin, but the persisted
            # model is one run per timestamp. Merge additional coins into the
            # existing run instead of creating duplicate run rows.
            connection.executemany(
                """
                INSERT INTO crypto_signal_coin_snapshots (
                    run_id,
                    coin_id,
                    symbol,
                    name,
                    price_usd,
                    price_change_24h,
                    volume_24h,
                    volume_change_pct_24h,
                    is_watchlist,
                    context_tags_json,
                    created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, coin_id) DO UPDATE SET
                    symbol=excluded.symbol,
                    name=excluded.name,
                    price_usd=excluded.price_usd,
                    price_change_24h=excluded.price_change_24h,
                    volume_24h=excluded.volume_24h,
                    volume_change_pct_24h=excluded.volume_change_pct_24h,
                    is_watchlist=excluded.is_watchlist,
                    context_tags_json=excluded.context_tags_json,
                    created_at_utc=excluded.created_at_utc
                """,
                [
                    self._serialize_coin_snapshot(
                        coin=coin,
                        run_id=run_id,
                        created_at_utc=created_at_utc,
                    )
                    for coin in snapshot.coins
                ],
            )
            connection.commit()

        existing_created_at = self._parse_timestamp(existing_run['created_at_utc'])
        snapshot.run.run_id = run_id
        snapshot.run.created_at_utc = existing_created_at
        for coin in snapshot.coins:
            coin.run_id = run_id
            coin.created_at_utc = created_at_utc
        return snapshot

    def get_coin_observation_counts_since(
        self,
        coin_ids: list[int],
        start_timestamp_utc: datetime.datetime,
    ) -> dict[int, int]:
        unique_coin_ids = list(dict.fromkeys(coin_ids))
        if len(unique_coin_ids) == 0:
            return {}
        counts = {coin_id: 0 for coin_id in unique_coin_ids}
        if not Path(self.db_path).exists():
            return counts

        placeholders = ','.join('?' for _ in unique_coin_ids)
        params = [self._format_timestamp(start_timestamp_utc), *unique_coin_ids]
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    f"""
                    SELECT coin.coin_id, COUNT(*) AS observation_count
                    FROM crypto_signal_coin_snapshots AS coin
                    INNER JOIN crypto_signal_runs AS run
                      ON run.run_id = coin.run_id
                    WHERE run.run_timestamp_utc >= ?
                      AND coin.coin_id IN ({placeholders})
                    GROUP BY coin.coin_id
                    """,
                    params,
                ).fetchall()
            except sqlite3.OperationalError as error:
                if 'no such table' in str(error):
                    return counts
                raise
        counts.update(
            {
                int(row['coin_id']): int(row['observation_count'])
                for row in rows
            }
        )
        return counts

    def get_latest_snapshot(self) -> CryptoSignalSnapshot | None:
        if not Path(self.db_path).exists():
            return None
        with self._connect() as connection:
            try:
                # The local report path must be able to inspect an existing DB
                # through a read-only connection, so read helpers cannot call
                # init_schema() or perform metadata writes here.
                run_row = connection.execute(
                    """
                    SELECT *
                    FROM crypto_signal_runs
                    ORDER BY run_timestamp_utc DESC
                    LIMIT 1
                    """
                ).fetchone()
            except sqlite3.OperationalError as error:
                if 'no such table' in str(error):
                    return None
                raise
            if run_row is None:
                return None
            return self._build_snapshot_from_row(connection, run_row)

    def get_snapshots_since(
        self,
        start_timestamp_utc: datetime.datetime,
    ) -> list[CryptoSignalSnapshot]:
        if not Path(self.db_path).exists():
            return []
        with self._connect() as connection:
            try:
                run_rows = connection.execute(
                    """
                    SELECT *
                    FROM crypto_signal_runs
                    WHERE run_timestamp_utc >= ?
                    ORDER BY run_timestamp_utc ASC
                    """,
                    (self._format_timestamp(start_timestamp_utc),),
                ).fetchall()
            except sqlite3.OperationalError as error:
                if 'no such table' in str(error):
                    return []
                raise
            return [
                self._build_snapshot_from_row(connection, run_row)
                for run_row in run_rows
            ]

    def _build_snapshot_from_row(
        self,
        connection: sqlite3.Connection,
        run_row: sqlite3.Row,
    ) -> CryptoSignalSnapshot:
        coin_rows = connection.execute(
            """
            SELECT *
            FROM crypto_signal_coin_snapshots
            WHERE run_id = ?
            ORDER BY symbol ASC, coin_id ASC
            """,
            (run_row['run_id'],),
        ).fetchall()
        return CryptoSignalSnapshot(
            run=self._build_run_record(run_row),
            coins=[self._build_coin_snapshot(row) for row in coin_rows],
        )

    def _build_candidate_cohorts(
        self,
        view: CryptoSignalDigestView,
    ) -> list[CryptoSignalCandidateCohort]:
        cohorts = []
        latest_coin_ids = {
            coin.coin_id for coin in view.latest_snapshot.coins
        }
        candidates_by_section = [
            ('strong', view.strong_candidates),
            ('weak', view.weak_candidates),
            ('watchlist', view.watchlist_candidates),
        ]
        for section, candidates in candidates_by_section:
            for candidate in candidates:
                if candidate.coin_id not in latest_coin_ids:
                    # Watchlist rows can be carried from recent history for
                    # operator continuity. Calibration needs a fresh baseline at
                    # the signal timestamp, so stale display-only rows are not
                    # frozen into outcome cohorts.
                    continue
                cohorts.append(
                    CryptoSignalCandidateCohort(
                        signal_run_timestamp_utc=view.latest_snapshot.run.run_timestamp_utc,
                        runtime_mode=view.latest_snapshot.run.runtime_mode,
                        window_label=view.window_label,
                        section=section,
                        coin_id=candidate.coin_id,
                        symbol=candidate.symbol,
                        name=candidate.name,
                        baseline_price_usd=candidate.latest_price_usd,
                        latest_price_change_24h=candidate.latest_price_change_24h,
                        window_price_change_pct=candidate.window_price_change_pct,
                        score=candidate.score,
                        price_persistence_score=candidate.price_persistence_score,
                        volume_confirmation_score=candidate.volume_confirmation_score,
                        attention_persistence_score=candidate.attention_persistence_score,
                        breadth_alignment_score=candidate.breadth_alignment_score,
                        observation_count=candidate.observation_count,
                        reason_tags=candidate.reason_tags,
                        flags=candidate.flags,
                        market_regime_label=view.market_regime_label,
                        market_regime_reason=view.market_regime_reason,
                    )
                )
        return cohorts

    def _upsert_candidate_cohort(
        self,
        connection: sqlite3.Connection,
        cohort: CryptoSignalCandidateCohort,
        created_at_utc: datetime.datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO crypto_signal_candidate_cohorts (
                signal_run_timestamp_utc,
                runtime_mode,
                window_label,
                section,
                coin_id,
                symbol,
                name,
                baseline_price_usd,
                latest_price_change_24h,
                window_price_change_pct,
                score,
                price_persistence_score,
                volume_confirmation_score,
                attention_persistence_score,
                breadth_alignment_score,
                observation_count,
                reason_tags_json,
                flags_json,
                market_regime_label,
                market_regime_reason,
                created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                signal_run_timestamp_utc,
                runtime_mode,
                window_label,
                section,
                coin_id
            ) DO NOTHING
            """,
            (
                self._format_timestamp(cohort.signal_run_timestamp_utc),
                cohort.runtime_mode,
                cohort.window_label,
                cohort.section,
                cohort.coin_id,
                cohort.symbol,
                cohort.name,
                cohort.baseline_price_usd,
                cohort.latest_price_change_24h,
                cohort.window_price_change_pct,
                cohort.score,
                cohort.price_persistence_score,
                cohort.volume_confirmation_score,
                cohort.attention_persistence_score,
                cohort.breadth_alignment_score,
                cohort.observation_count,
                json.dumps(list(cohort.reason_tags), separators=(',', ':')),
                json.dumps(list(cohort.flags), separators=(',', ':')),
                cohort.market_regime_label,
                cohort.market_regime_reason,
                self._format_timestamp(created_at_utc),
            ),
        )

    def _select_candidate_cohort_id(
        self,
        connection: sqlite3.Connection,
        cohort: CryptoSignalCandidateCohort,
    ) -> int:
        row = connection.execute(
            """
            SELECT cohort_id
            FROM crypto_signal_candidate_cohorts
            WHERE signal_run_timestamp_utc = ?
              AND runtime_mode = ?
              AND window_label = ?
              AND section = ?
              AND coin_id = ?
            """,
            (
                self._format_timestamp(cohort.signal_run_timestamp_utc),
                cohort.runtime_mode,
                cohort.window_label,
                cohort.section,
                cohort.coin_id,
            ),
        ).fetchone()
        return int(row['cohort_id'])

    def _upsert_candidate_outcome_placeholders(
        self,
        connection: sqlite3.Connection,
        cohort: CryptoSignalCandidateCohort,
        created_at_utc: datetime.datetime,
    ) -> None:
        if cohort.cohort_id is None:
            raise ValueError('candidate cohort must be persisted before outcomes')
        connection.executemany(
            """
            INSERT INTO crypto_signal_candidate_outcomes (
                cohort_id,
                outcome_window,
                target_timestamp_utc,
                status,
                created_at_utc,
                updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (cohort_id, outcome_window) DO NOTHING
            """,
            [
                (
                    cohort.cohort_id,
                    outcome_window,
                    self._format_timestamp(
                        cohort.signal_run_timestamp_utc + outcome_delta
                    ),
                    OUTCOME_STATUS_PENDING,
                    self._format_timestamp(created_at_utc),
                    self._format_timestamp(created_at_utc),
                )
                for outcome_window, outcome_delta in OUTCOME_WINDOWS.items()
            ],
        )

    def _resolve_candidate_outcome_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        updated_at_utc: datetime.datetime,
    ) -> CryptoSignalCandidateOutcome:
        target_timestamp_utc = self._parse_timestamp(row['target_timestamp_utc'])
        outcome_run = self._find_first_run_at_or_after(
            connection=connection,
            runtime_mode=row['runtime_mode'],
            target_timestamp_utc=target_timestamp_utc,
        )
        if outcome_run is None:
            return CryptoSignalCandidateOutcome(
                cohort_id=row['cohort_id'],
                outcome_window=row['outcome_window'],
                target_timestamp_utc=target_timestamp_utc,
                status=OUTCOME_STATUS_MISSING,
                missing_reason='missing_follow_up_run',
                updated_at_utc=updated_at_utc,
            )
        resolved_run_timestamp_utc = self._parse_timestamp(
            outcome_run['run_timestamp_utc']
        )
        if resolved_run_timestamp_utc - target_timestamp_utc > OUTCOME_MAX_FOLLOW_UP_LAG:
            return CryptoSignalCandidateOutcome(
                cohort_id=row['cohort_id'],
                outcome_window=row['outcome_window'],
                target_timestamp_utc=target_timestamp_utc,
                status=OUTCOME_STATUS_MISSING,
                missing_reason='stale_follow_up_run',
                resolved_run_timestamp_utc=resolved_run_timestamp_utc,
                updated_at_utc=updated_at_utc,
            )

        prices = self._get_prices_for_run(
            connection=connection,
            run_id=int(outcome_run['run_id']),
            coin_ids=[int(row['coin_id']), BTC_COIN_ID, ETH_COIN_ID],
        )
        baseline_price_usd = row['baseline_price_usd']
        candidate_price_usd = prices.get(int(row['coin_id']))
        btc_price_usd = prices.get(BTC_COIN_ID)
        eth_price_usd = prices.get(ETH_COIN_ID)
        baseline_run = self._find_run_by_timestamp(
            connection=connection,
            runtime_mode=row['runtime_mode'],
            run_timestamp_utc=self._parse_timestamp(row['signal_run_timestamp_utc']),
        )
        baseline_prices = (
            {}
            if baseline_run is None
            else self._get_prices_for_run(
                connection=connection,
                run_id=int(baseline_run['run_id']),
                coin_ids=[BTC_COIN_ID, ETH_COIN_ID],
            )
        )
        btc_baseline_price_usd = baseline_prices.get(BTC_COIN_ID)
        eth_baseline_price_usd = baseline_prices.get(ETH_COIN_ID)

        missing_reasons = []
        if baseline_price_usd is None:
            missing_reasons.append('missing_candidate_baseline_price')
        if candidate_price_usd is None:
            missing_reasons.append('missing_candidate_follow_up_price')
        if btc_baseline_price_usd is None or btc_price_usd is None:
            missing_reasons.append('missing_btc_benchmark_price')
        if eth_baseline_price_usd is None or eth_price_usd is None:
            missing_reasons.append('missing_eth_benchmark_price')
        if missing_reasons:
            return CryptoSignalCandidateOutcome(
                cohort_id=row['cohort_id'],
                outcome_window=row['outcome_window'],
                target_timestamp_utc=target_timestamp_utc,
                status=OUTCOME_STATUS_MISSING,
                candidate_price_usd=candidate_price_usd,
                btc_price_usd=btc_price_usd,
                eth_price_usd=eth_price_usd,
                missing_reason=','.join(missing_reasons),
                resolved_run_timestamp_utc=resolved_run_timestamp_utc,
                updated_at_utc=updated_at_utc,
            )

        absolute_return_pct = _calculate_return_pct(
            baseline_price_usd,
            candidate_price_usd,
        )
        btc_return_pct = _calculate_return_pct(
            btc_baseline_price_usd,
            btc_price_usd,
        )
        eth_return_pct = _calculate_return_pct(
            eth_baseline_price_usd,
            eth_price_usd,
        )
        return CryptoSignalCandidateOutcome(
            cohort_id=row['cohort_id'],
            outcome_window=row['outcome_window'],
            target_timestamp_utc=target_timestamp_utc,
            status=OUTCOME_STATUS_RESOLVED,
            candidate_price_usd=candidate_price_usd,
            btc_price_usd=btc_price_usd,
            eth_price_usd=eth_price_usd,
            absolute_return_pct=absolute_return_pct,
            btc_relative_return_pct=(
                None
                if absolute_return_pct is None or btc_return_pct is None
                else absolute_return_pct - btc_return_pct
            ),
            eth_relative_return_pct=(
                None
                if absolute_return_pct is None or eth_return_pct is None
                else absolute_return_pct - eth_return_pct
            ),
            resolved_run_timestamp_utc=resolved_run_timestamp_utc,
            updated_at_utc=updated_at_utc,
        )

    def _update_candidate_outcome(
        self,
        connection: sqlite3.Connection,
        outcome: CryptoSignalCandidateOutcome,
        updated_at_utc: datetime.datetime,
    ) -> None:
        connection.execute(
            """
            UPDATE crypto_signal_candidate_outcomes
            SET status = ?,
                candidate_price_usd = ?,
                btc_price_usd = ?,
                eth_price_usd = ?,
                absolute_return_pct = ?,
                btc_relative_return_pct = ?,
                eth_relative_return_pct = ?,
                missing_reason = ?,
                resolved_run_timestamp_utc = ?,
                updated_at_utc = ?
            WHERE cohort_id = ?
              AND outcome_window = ?
            """,
            (
                outcome.status,
                outcome.candidate_price_usd,
                outcome.btc_price_usd,
                outcome.eth_price_usd,
                outcome.absolute_return_pct,
                outcome.btc_relative_return_pct,
                outcome.eth_relative_return_pct,
                outcome.missing_reason,
                (
                    None
                    if outcome.resolved_run_timestamp_utc is None
                    else self._format_timestamp(outcome.resolved_run_timestamp_utc)
                ),
                self._format_timestamp(updated_at_utc),
                outcome.cohort_id,
                outcome.outcome_window,
            ),
        )

    def _find_first_run_at_or_after(
        self,
        connection: sqlite3.Connection,
        runtime_mode: str,
        target_timestamp_utc: datetime.datetime,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT *
            FROM crypto_signal_runs
            WHERE runtime_mode = ?
              AND run_timestamp_utc >= ?
            ORDER BY run_timestamp_utc ASC
            LIMIT 1
            """,
            (runtime_mode, self._format_timestamp(target_timestamp_utc)),
        ).fetchone()

    def _find_run_by_timestamp(
        self,
        connection: sqlite3.Connection,
        runtime_mode: str,
        run_timestamp_utc: datetime.datetime,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT *
            FROM crypto_signal_runs
            WHERE runtime_mode = ?
              AND run_timestamp_utc = ?
            LIMIT 1
            """,
            (runtime_mode, self._format_timestamp(run_timestamp_utc)),
        ).fetchone()

    def _get_prices_for_run(
        self,
        connection: sqlite3.Connection,
        run_id: int,
        coin_ids: list[int],
    ) -> dict[int, float]:
        placeholders = ','.join('?' for _ in coin_ids)
        rows = connection.execute(
            f"""
            SELECT coin_id, price_usd
            FROM crypto_signal_coin_snapshots
            WHERE run_id = ?
              AND coin_id IN ({placeholders})
              AND price_usd IS NOT NULL
            """,
            [run_id, *coin_ids],
        ).fetchall()
        return {
            int(row['coin_id']): float(row['price_usd'])
            for row in rows
        }

    def _build_run_record(self, row: sqlite3.Row) -> CryptoSignalRunRecord:
        return CryptoSignalRunRecord(
            run_id=row['run_id'],
            run_timestamp_utc=self._parse_timestamp(row['run_timestamp_utc']),
            runtime_mode=row['runtime_mode'],
            source_name=row['source_name'],
            snapshot_version=row['snapshot_version'],
            sentiment_now_value=row['sentiment_now_value'],
            sentiment_now_label=row['sentiment_now_label'],
            sentiment_yesterday_value=row['sentiment_yesterday_value'],
            sentiment_last_week_value=row['sentiment_last_week_value'],
            sentiment_7d_avg=row['sentiment_7d_avg'],
            sentiment_30d_avg=row['sentiment_30d_avg'],
            strongest_sector_id=row['strongest_sector_id'],
            strongest_sector_name=row['strongest_sector_name'],
            strongest_sector_avg_price_change_24h=row['strongest_sector_avg_price_change_24h'],
            strongest_sector_market_change_24h=row['strongest_sector_market_change_24h'],
            strongest_sector_volume_change_24h=row['strongest_sector_volume_change_24h'],
            strongest_sector_gainers_num=row['strongest_sector_gainers_num'],
            strongest_sector_losers_num=row['strongest_sector_losers_num'],
            weakest_sector_id=row['weakest_sector_id'],
            weakest_sector_name=row['weakest_sector_name'],
            weakest_sector_avg_price_change_24h=row['weakest_sector_avg_price_change_24h'],
            weakest_sector_market_change_24h=row['weakest_sector_market_change_24h'],
            weakest_sector_volume_change_24h=row['weakest_sector_volume_change_24h'],
            weakest_sector_gainers_num=row['weakest_sector_gainers_num'],
            weakest_sector_losers_num=row['weakest_sector_losers_num'],
            created_at_utc=self._parse_timestamp(row['created_at_utc']),
        )

    def _build_coin_snapshot(self, row: sqlite3.Row) -> CryptoSignalCoinSnapshot:
        return CryptoSignalCoinSnapshot(
            run_id=row['run_id'],
            coin_id=row['coin_id'],
            symbol=row['symbol'],
            name=row['name'],
            price_usd=row['price_usd'],
            price_change_24h=row['price_change_24h'],
            volume_24h=row['volume_24h'],
            volume_change_pct_24h=row['volume_change_pct_24h'],
            is_watchlist=bool(row['is_watchlist']),
            context_tags=tuple(json.loads(row['context_tags_json'])),
            created_at_utc=self._parse_timestamp(row['created_at_utc']),
        )

    def _serialize_coin_snapshot(
        self,
        coin: CryptoSignalCoinSnapshot,
        run_id: int,
        created_at_utc: datetime.datetime,
    ) -> tuple:
        return (
            run_id,
            coin.coin_id,
            coin.symbol,
            coin.name,
            coin.price_usd,
            coin.price_change_24h,
            coin.volume_24h,
            coin.volume_change_pct_24h,
            int(coin.is_watchlist),
            json.dumps(list(coin.context_tags), separators=(',', ':')),
            self._format_timestamp(created_at_utc),
        )

    def _serialize_market_regime_metric(
        self,
        metric: CryptoSignalMarketRegimeMetric,
        snapshot_id: int,
        created_at_utc: datetime.datetime,
    ) -> tuple:
        return (
            snapshot_id,
            metric.metric_name,
            metric.metric_value,
            metric.unit,
            self._format_timestamp(metric.source_timestamp_utc),
            self._format_timestamp(created_at_utc),
        )

    def _build_market_regime_metric(
        self,
        row: sqlite3.Row,
    ) -> CryptoSignalMarketRegimeMetric:
        return CryptoSignalMarketRegimeMetric(
            snapshot_id=row['snapshot_id'],
            metric_name=row['metric_name'],
            metric_value=row['metric_value'],
            unit=row['unit'],
            source_timestamp_utc=self._parse_timestamp(row['source_timestamp_utc']),
            provider=row['provider'] if 'provider' in row.keys() else None,
            asset_symbol=row['asset_symbol'] if 'asset_symbol' in row.keys() else None,
            venue_scope=row['venue_scope'] if 'venue_scope' in row.keys() else None,
            instrument_scope=(
                row['instrument_scope'] if 'instrument_scope' in row.keys() else None
            ),
            interval=row['interval'] if 'interval' in row.keys() else None,
            created_at_utc=self._parse_timestamp(row['created_at_utc']),
        )

    @staticmethod
    def _dedupe_market_regime_metric_rows(
        rows: list[sqlite3.Row],
    ) -> list[sqlite3.Row]:
        latest_by_fact_key: dict[tuple, sqlite3.Row] = {}
        for row in rows:
            # Scheduled collections can re-fetch overlapping historical points
            # under later snapshots. Read-side summaries use the latest stored
            # fact for each provider/scope/metric/source timestamp.
            fact_key = (
                row['provider'],
                row['asset_symbol'],
                row['venue_scope'],
                row['instrument_scope'],
                row['interval'],
                row['metric_name'],
                row['source_timestamp_utc'],
            )
            existing = latest_by_fact_key.get(fact_key)
            if existing is None or (
                _market_regime_row_version_key(row)
                > _market_regime_row_version_key(existing)
            ):
                latest_by_fact_key[fact_key] = row
        return sorted(
            latest_by_fact_key.values(),
            key=lambda row: (row['source_timestamp_utc'], row['metric_name']),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _utcnow() -> datetime.datetime:
        return datetime.datetime.now(tz=datetime.timezone.utc).replace(
            microsecond=0
        )

    @staticmethod
    def _format_timestamp(value: datetime.datetime) -> str:
        return (
            value.astimezone(datetime.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace('+00:00', 'Z')
        )

    @staticmethod
    def _parse_timestamp(value: str) -> datetime.datetime:
        return datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))


def _market_regime_row_version_key(row: sqlite3.Row) -> tuple[str, str, int]:
    return (
        row['snapshot_observed_at_utc'],
        row['created_at_utc'],
        int(row['snapshot_id']),
    )


def _calculate_return_pct(
    baseline_price_usd: float | None,
    outcome_price_usd: float | None,
) -> float | None:
    if baseline_price_usd is None or outcome_price_usd is None:
        return None
    if baseline_price_usd == 0:
        return None
    return ((outcome_price_usd - baseline_price_usd) / baseline_price_usd) * 100


def iter_snapshot_coin_ids(
    snapshots: Iterable[CryptoSignalSnapshot],
) -> list[int]:
    seen_coin_ids: dict[int, None] = {}
    for snapshot in snapshots:
        for coin in snapshot.coins:
            seen_coin_ids.setdefault(coin.coin_id, None)
    return list(seen_coin_ids.keys())
