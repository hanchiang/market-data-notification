import datetime
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from src.config import config
from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE, RuntimeMode
from src.service.crypto_signal.models import (
    CryptoSignalCoinSnapshot,
    CryptoSignalMarketRegimeMetric,
    CryptoSignalMarketRegimeSnapshot,
    CryptoSignalRunRecord,
    CryptoSignalSnapshot,
)


SNAPSHOT_VERSION = 1

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
        'cadence': 'Sampling cadence for the stored metrics.',
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
                    cadence TEXT NOT NULL,
                    source_payload_version INTEGER NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    UNIQUE (
                        observed_at_utc,
                        runtime_mode,
                        provider,
                        asset_symbol,
                        venue_scope,
                        instrument_scope,
                        cadence
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
                    cadence,
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
                    cadence
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
                    snapshot.cadence,
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
                  AND cadence = ?
                """,
                (
                    self._format_timestamp(snapshot.observed_at_utc),
                    snapshot.runtime_mode,
                    snapshot.provider,
                    snapshot.asset_symbol,
                    snapshot.venue_scope,
                    snapshot.instrument_scope,
                    snapshot.cadence,
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
        cadence: str | None = None,
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
        if cadence is not None:
            scope_filters.append('snapshot.cadence = ?')
            params.append(cadence)
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
                        snapshot.cadence
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
            cadence=row['cadence'] if 'cadence' in row.keys() else None,
            created_at_utc=self._parse_timestamp(row['created_at_utc']),
        )

    @staticmethod
    def _dedupe_market_regime_metric_rows(
        rows: list[sqlite3.Row],
    ) -> list[sqlite3.Row]:
        latest_by_fact_key: dict[tuple, sqlite3.Row] = {}
        for row in rows:
            fact_key = (
                row['provider'],
                row['asset_symbol'],
                row['venue_scope'],
                row['instrument_scope'],
                row['cadence'],
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


def iter_snapshot_coin_ids(
    snapshots: Iterable[CryptoSignalSnapshot],
) -> list[int]:
    seen_coin_ids: dict[int, None] = {}
    for snapshot in snapshots:
        for coin in snapshot.coins:
            seen_coin_ids.setdefault(coin.coin_id, None)
    return list(seen_coin_ids.keys())
