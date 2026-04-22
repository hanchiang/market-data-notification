import datetime
from dataclasses import dataclass, field


@dataclass(slots=True)
class CryptoSignalRunRecord:
    run_timestamp_utc: datetime.datetime
    runtime_mode: str
    source_name: str
    snapshot_version: int
    sentiment_now_value: float | None
    sentiment_now_label: str | None
    sentiment_yesterday_value: float | None
    sentiment_last_week_value: float | None
    sentiment_7d_avg: float | None
    sentiment_30d_avg: float | None
    strongest_sector_id: str | None
    strongest_sector_name: str | None
    strongest_sector_avg_price_change_24h: float | None
    strongest_sector_market_change_24h: float | None
    strongest_sector_volume_change_24h: float | None
    strongest_sector_gainers_num: int | None
    strongest_sector_losers_num: int | None
    weakest_sector_id: str | None
    weakest_sector_name: str | None
    weakest_sector_avg_price_change_24h: float | None
    weakest_sector_market_change_24h: float | None
    weakest_sector_volume_change_24h: float | None
    weakest_sector_gainers_num: int | None
    weakest_sector_losers_num: int | None
    run_id: int | None = None
    created_at_utc: datetime.datetime | None = None


@dataclass(slots=True)
class CryptoSignalCoinSnapshot:
    coin_id: int
    symbol: str
    name: str
    price_usd: float | None
    price_change_24h: float | None
    volume_24h: float | None
    volume_change_pct_24h: float | None
    is_watchlist: bool
    context_tags: tuple[str, ...]
    run_id: int | None = None
    created_at_utc: datetime.datetime | None = None


@dataclass(slots=True)
class CryptoSignalSnapshot:
    run: CryptoSignalRunRecord
    coins: list[CryptoSignalCoinSnapshot] = field(default_factory=list)


@dataclass(slots=True)
class CryptoSignalCandidate:
    coin_id: int
    symbol: str
    name: str
    latest_price_usd: float | None
    latest_price_change_24h: float | None
    latest_volume_change_pct_24h: float | None
    latest_context_tags: tuple[str, ...]
    score: int
    price_persistence_score: int
    volume_confirmation_score: int
    attention_persistence_score: int
    breadth_alignment_score: int
    observation_count: int
    reason_tags: tuple[str, ...]
    flags: tuple[str, ...]
    is_watchlist: bool


@dataclass(slots=True)
class CryptoSignalDigestView:
    latest_snapshot: CryptoSignalSnapshot
    window_label: str
    market_regime_label: str
    market_regime_reason: str
    strong_candidates: list[CryptoSignalCandidate] = field(default_factory=list)
    weak_candidates: list[CryptoSignalCandidate] = field(default_factory=list)
    watchlist_candidates: list[CryptoSignalCandidate] = field(default_factory=list)
