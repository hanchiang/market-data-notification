from statistics import mean

from src.service.crypto_signal.models import (
    CryptoSignalMarketRegimeMetric,
    CryptoSignalMarketRegimeSummary,
)


OPEN_INTEREST_METRIC = 'open_interest_usd'
FUNDING_RATE_METRIC = 'funding_rate'


def build_market_regime_summary(
    metrics: list[CryptoSignalMarketRegimeMetric],
) -> CryptoSignalMarketRegimeSummary:
    open_interest_values = _metric_values(metrics, OPEN_INTEREST_METRIC)
    funding_values = _metric_values(metrics, FUNDING_RATE_METRIC)

    if len(open_interest_values) < 2:
        return CryptoSignalMarketRegimeSummary(
            label='Insufficient regime history',
            reason='open interest history unavailable',
        )

    oi_start = open_interest_values[0]
    oi_end = open_interest_values[-1]
    oi_change_pct = _pct_change(start=oi_start, end=oi_end)
    avg_funding = mean(funding_values) if funding_values else None

    if oi_change_pct is None:
        return CryptoSignalMarketRegimeSummary(
            label='Insufficient regime history',
            reason='open interest baseline unavailable',
        )

    funding_reason = (
        'funding unavailable'
        if avg_funding is None
        else f'avg funding {avg_funding:+.4f}%'
    )
    reason = f'OI {oi_change_pct:+.1f}%, {funding_reason}'

    if avg_funding is not None and avg_funding >= 0.03:
        return CryptoSignalMarketRegimeSummary(
            label='Crowded long pressure',
            reason=reason,
        )
    if oi_change_pct >= 5:
        return CryptoSignalMarketRegimeSummary(
            label='Leverage building',
            reason=reason,
        )
    if oi_change_pct <= -5:
        return CryptoSignalMarketRegimeSummary(
            label='Deleveraging',
            reason=reason,
        )
    return CryptoSignalMarketRegimeSummary(label='Mixed', reason=reason)


def _metric_values(
    metrics: list[CryptoSignalMarketRegimeMetric],
    metric_name: str,
) -> list[float]:
    sorted_metrics = sorted(
        (
            metric
            for metric in metrics
            if metric.metric_name == metric_name and metric.metric_value is not None
        ),
        key=lambda metric: metric.source_timestamp_utc,
    )
    return [float(metric.metric_value) for metric in sorted_metrics]


def _pct_change(start: float, end: float) -> float | None:
    if start == 0:
        return None
    return ((end - start) / abs(start)) * 100
