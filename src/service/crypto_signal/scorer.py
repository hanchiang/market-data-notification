import datetime
from collections import defaultdict
from statistics import mean

from src.service.crypto_signal.models import (
    CryptoSignalCandidate,
    CryptoSignalCoinSnapshot,
    CryptoSignalDigestView,
    CryptoSignalSnapshot,
)


# Phase 1 is an operator-review ranking heuristic, not a trading system. Price
# persistence sets direction; volume, attention, breadth, and sentiment only
# confirm, contextualize, or explain that direction.
EXPECTED_OBSERVATIONS_BY_WINDOW = {
    '3d': 6,
    '7d': 14,
    '30d': 60,
}

_WINDOW_LENGTHS = {
    '3d': datetime.timedelta(days=3),
    '7d': datetime.timedelta(days=7),
    '30d': datetime.timedelta(days=30),
}

_BULLISH_ATTENTION_TAGS = {
    'spotlight_trending',
    'spotlight_gainer',
}

_BEARISH_ATTENTION_TAGS = {
    'spotlight_trending',
    'spotlight_loser',
}

# Require at least two observations so a single 24h move cannot masquerade as a
# window trend.
_MIN_OBSERVATIONS_TO_SCORE = 2


def get_window_start(
    latest_snapshot: CryptoSignalSnapshot,
    window_label: str,
) -> datetime.datetime:
    if window_label not in _WINDOW_LENGTHS:
        raise RuntimeError(f'Unsupported crypto signal window: {window_label}')
    return latest_snapshot.run.run_timestamp_utc - _WINDOW_LENGTHS[window_label]


def build_digest_view(
    latest_snapshot: CryptoSignalSnapshot,
    history: list[CryptoSignalSnapshot],
    watchlist_coin_ids: set[int],
    window_label: str,
    tracked_universe_coin_ids: set[int] | None = None,
    limit: int = 3,
    min_dynamic_price_usd: float = 1.0,
    min_dynamic_volume_24h: float = 50_000_000.0,
) -> CryptoSignalDigestView:
    tracked_universe_coin_ids = (
        set() if tracked_universe_coin_ids is None else tracked_universe_coin_ids
    )
    latest_coins_by_id = {coin.coin_id: coin for coin in latest_snapshot.coins}
    history_by_coin_id: dict[int, list[CryptoSignalCoinSnapshot]] = defaultdict(list)
    for snapshot in history:
        for coin in snapshot.coins:
            history_by_coin_id[coin.coin_id].append(coin)

    candidates = [
        _build_candidate(
            latest_coin=latest_coin,
            history=history_by_coin_id.get(latest_coin.coin_id, [latest_coin]),
            latest_snapshot=latest_snapshot,
            window_label=window_label,
        )
        for latest_coin in latest_coins_by_id.values()
    ]
    candidates_by_coin_id = {
        candidate.coin_id: candidate for candidate in candidates
    }

    # Strong/weak sections are the operator-ranked output, so they apply the
    # dynamic-candidate tradability floor. Watchlist continuity is handled
    # separately below and tracked-universe coins bypass the dynamic floor.
    strong_candidates = [
        candidate
        for candidate in candidates
        if candidate.score >= 2
        and _is_rankable_candidate(
            candidate=candidate,
            tracked_universe_coin_ids=tracked_universe_coin_ids,
            min_dynamic_price_usd=min_dynamic_price_usd,
            min_dynamic_volume_24h=min_dynamic_volume_24h,
        )
    ]
    weak_candidates = [
        candidate
        for candidate in candidates
        if candidate.score <= -2
        and _is_rankable_candidate(
            candidate=candidate,
            tracked_universe_coin_ids=tracked_universe_coin_ids,
            min_dynamic_price_usd=min_dynamic_price_usd,
            min_dynamic_volume_24h=min_dynamic_volume_24h,
        )
    ]
    watchlist_candidates = [
        candidates_by_coin_id[coin_id]
        for coin_id in watchlist_coin_ids
        if coin_id in candidates_by_coin_id
    ]
    for coin_id in sorted(watchlist_coin_ids):
        if coin_id in candidates_by_coin_id:
            continue
        recent_history = history_by_coin_id.get(coin_id)
        if not recent_history:
            continue
        # Keep the watchlist section stable even when the latest live snapshot
        # omitted the coin, as long as there is still recent retained history
        # inside the requested analysis window.
        watchlist_candidates.append(
            _build_candidate(
                latest_coin=recent_history[-1],
                history=recent_history,
                latest_snapshot=latest_snapshot,
                window_label=window_label,
            )
        )

    strong_candidates.sort(key=lambda candidate: (-candidate.score, candidate.symbol))
    weak_candidates.sort(key=lambda candidate: (candidate.score, candidate.symbol))
    watchlist_candidates.sort(
        key=lambda candidate: (-abs(candidate.score), candidate.symbol)
    )

    market_regime_label, market_regime_reason = _classify_market_regime(latest_snapshot)

    return CryptoSignalDigestView(
        latest_snapshot=latest_snapshot,
        window_label=window_label,
        market_regime_label=market_regime_label,
        market_regime_reason=market_regime_reason,
        strong_candidates=strong_candidates[:limit],
        weak_candidates=weak_candidates[:limit],
        watchlist_candidates=watchlist_candidates[:limit],
    )


def _build_candidate(
    latest_coin: CryptoSignalCoinSnapshot,
    history: list[CryptoSignalCoinSnapshot],
    latest_snapshot: CryptoSignalSnapshot,
    window_label: str,
) -> CryptoSignalCandidate:
    price_changes = [
        coin.price_change_24h
        for coin in history
        if coin.price_change_24h is not None
    ]
    volume_changes = [
        coin.volume_change_pct_24h
        for coin in history
        if coin.volume_change_pct_24h is not None
    ]
    observation_count = len(history)

    if observation_count < _MIN_OBSERVATIONS_TO_SCORE:
        price_persistence_score = 0
        volume_confirmation_score = 0
        attention_persistence_score = 0
        breadth_alignment_score = 0
    else:
        price_persistence_score = _score_price_persistence(price_changes)
        trend_sign = _sign(price_persistence_score)
        volume_confirmation_score = _score_volume_confirmation(
            trend_sign=trend_sign,
            volume_changes=volume_changes,
        )
        attention_persistence_score = _score_attention_persistence(
            trend_sign=trend_sign,
            history=history,
        )
        breadth_alignment_score = _score_breadth_alignment(
            trend_sign=trend_sign,
            history=history,
        )
    total_score = (
        price_persistence_score
        + volume_confirmation_score
        + attention_persistence_score
        + breadth_alignment_score
    )

    # Flags add explanatory context to the rendered digest, but they are not
    # additional score components in the phase-1 heuristic.
    flags = []
    expected_observations = EXPECTED_OBSERVATIONS_BY_WINDOW[window_label]
    if observation_count < max(2, expected_observations // 2):
        flags.append('thin-history')
    if latest_snapshot.run.sentiment_now_value is not None:
        if latest_snapshot.run.sentiment_now_value >= 55:
            flags.append('risk-on')
        elif latest_snapshot.run.sentiment_now_value <= 45:
            flags.append('risk-off')
    if latest_coin.is_watchlist:
        flags.append('watchlist')

    reason_tags = _build_reason_tags(
        price_persistence_score=price_persistence_score,
        volume_confirmation_score=volume_confirmation_score,
        attention_persistence_score=attention_persistence_score,
        breadth_alignment_score=breadth_alignment_score,
        flags=flags,
    )

    return CryptoSignalCandidate(
        coin_id=latest_coin.coin_id,
        symbol=latest_coin.symbol,
        name=latest_coin.name,
        latest_price_usd=latest_coin.price_usd,
        latest_volume_24h=latest_coin.volume_24h,
        latest_price_change_24h=latest_coin.price_change_24h,
        window_price_change_pct=_calculate_window_price_change_pct(history),
        latest_volume_change_pct_24h=latest_coin.volume_change_pct_24h,
        latest_context_tags=latest_coin.context_tags,
        score=total_score,
        price_persistence_score=price_persistence_score,
        volume_confirmation_score=volume_confirmation_score,
        attention_persistence_score=attention_persistence_score,
        breadth_alignment_score=breadth_alignment_score,
        observation_count=observation_count,
        reason_tags=reason_tags,
        flags=tuple(flags),
        is_watchlist=latest_coin.is_watchlist,
    )


def _calculate_window_price_change_pct(
    history: list[CryptoSignalCoinSnapshot],
) -> float | None:
    # Callers pass history ordered oldest-to-newest by run timestamp. The window
    # move is a first-to-last price return, separate from 24h persistence scoring.
    priced_history = [
        coin for coin in history
        if coin.price_usd is not None and coin.price_usd > 0
    ]
    if len(priced_history) < 2:
        return None

    first_price = priced_history[0].price_usd
    latest_price = priced_history[-1].price_usd
    if first_price is None or latest_price is None:
        return None
    return ((latest_price - first_price) / first_price) * 100


def _score_price_persistence(price_changes: list[float]) -> int:
    if len(price_changes) == 0:
        return 0

    positive_hits = len([change for change in price_changes if change > 0])
    negative_hits = len([change for change in price_changes if change < 0])
    balance = (positive_hits - negative_hits) / len(price_changes)
    average_change = mean(price_changes)
    # Mix direction consistency with average move size so one outsized candle
    # does not dominate the signal if the rest of the window disagrees.
    average_component = _clamp(average_change / 15.0, -1.0, 1.0)
    return int(round(_clamp((balance * 0.7 + average_component * 0.3) * 4, -4, 4)))


def _score_volume_confirmation(
    trend_sign: int,
    volume_changes: list[float],
) -> int:
    if trend_sign == 0 or len(volume_changes) == 0:
        return 0

    # Volume only confirms an existing price direction in phase 1; it is not
    # allowed to create a directional signal on its own.
    return trend_sign * 2 if mean(volume_changes) >= 15 else 0


def _score_attention_persistence(
    trend_sign: int,
    history: list[CryptoSignalCoinSnapshot],
) -> int:
    if trend_sign == 0 or len(history) == 0:
        return 0

    # `spotlight_trending` is directional only in combination with the
    # surrounding price signal, so phase 1 counts it toward both bullish and
    # bearish persistence depending on `trend_sign`.
    relevant_tags = (
        _BULLISH_ATTENTION_TAGS if trend_sign > 0 else _BEARISH_ATTENTION_TAGS
    )
    spotlight_hits = sum(
        1
        for coin in history
        for tag in coin.context_tags
        if tag in relevant_tags
    )
    if spotlight_hits >= 4:
        return trend_sign * 2
    if spotlight_hits >= 2:
        return trend_sign
    return 0


def _score_breadth_alignment(
    trend_sign: int,
    history: list[CryptoSignalCoinSnapshot],
) -> int:
    if trend_sign == 0 or len(history) == 0:
        return 0

    if trend_sign > 0:
        return (
            1
            if any('sector_leader_strongest' in coin.context_tags for coin in history)
            else 0
        )
    return (
        -1
        if any('sector_loser_weakest' in coin.context_tags for coin in history)
        else 0
    )


def _is_rankable_candidate(
    candidate: CryptoSignalCandidate,
    tracked_universe_coin_ids: set[int],
    min_dynamic_price_usd: float,
    min_dynamic_volume_24h: float,
) -> bool:
    # Tracked-universe names stay eligible even when they fail the dynamic
    # floor because the filter is for operator ranking, not persistence scope.
    if candidate.coin_id in tracked_universe_coin_ids:
        return True
    # Keep persistence broad, but apply a lightweight tradability floor before
    # ranking dynamic names into the operator-facing strong/weak sections.
    if candidate.latest_price_usd is None or candidate.latest_volume_24h is None:
        return False
    return (
        candidate.latest_price_usd >= min_dynamic_price_usd
        and candidate.latest_volume_24h >= min_dynamic_volume_24h
    )


def _classify_market_regime(
    latest_snapshot: CryptoSignalSnapshot,
) -> tuple[str, str]:
    sentiment_value = latest_snapshot.run.sentiment_now_value
    strongest_change = latest_snapshot.run.strongest_sector_avg_price_change_24h
    weakest_change = latest_snapshot.run.weakest_sector_avg_price_change_24h

    if sentiment_value is None:
        return 'Mixed', 'sentiment data unavailable'
    if sentiment_value >= 55 and (strongest_change is None or strongest_change > 0):
        return (
            'Risk-on bias',
            f'sentiment {sentiment_value:.0f} with strongest sector {strongest_change or 0:+.1f}%',
        )
    if sentiment_value <= 45 and (weakest_change is None or weakest_change < 0):
        return (
            'Risk-off bias',
            f'sentiment {sentiment_value:.0f} with weakest sector {weakest_change or 0:+.1f}%',
        )
    return (
        'Mixed',
        f'sentiment {sentiment_value:.0f}, strongest {strongest_change or 0:+.1f}%, weakest {weakest_change or 0:+.1f}%',
    )


def _sign(value: int, fallback: float | None = None) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    if fallback is None:
        return 0
    if fallback > 0:
        return 1
    if fallback < 0:
        return -1
    return 0


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def _build_reason_tags(
    price_persistence_score: int,
    volume_confirmation_score: int,
    attention_persistence_score: int,
    breadth_alignment_score: int,
    flags: list[str],
) -> tuple[str, ...]:
    reason_tags = []
    if price_persistence_score > 0:
        reason_tags.append('price-up-persistent')
    elif price_persistence_score < 0:
        reason_tags.append('price-down-persistent')
    if volume_confirmation_score != 0:
        reason_tags.append('vol-confirm')
    if attention_persistence_score != 0:
        reason_tags.append('spotlight-repeat')
    if breadth_alignment_score != 0:
        reason_tags.append('breadth-align')
    if 'risk-on' in flags:
        reason_tags.append('risk-on')
    if 'risk-off' in flags:
        reason_tags.append('risk-off')
    if 'thin-history' in flags:
        reason_tags.append('thin-history')
    return tuple(reason_tags)
