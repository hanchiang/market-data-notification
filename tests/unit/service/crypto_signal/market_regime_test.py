import datetime

from src.service.crypto_signal.market_regime import build_market_regime_summary
from src.service.crypto_signal.models import CryptoSignalMarketRegimeMetric


def test_build_market_regime_summary_detects_leverage_building():
    summary = build_market_regime_summary(
        [
            _metric('open_interest_usd', 100.0, '2026-04-20T00:00:00Z'),
            _metric('funding_rate', 0.005, '2026-04-20T00:00:00Z'),
            _metric('open_interest_usd', 108.0, '2026-04-27T00:00:00Z'),
            _metric('funding_rate', 0.010, '2026-04-27T00:00:00Z'),
        ]
    )

    assert summary.label == 'Leverage building'
    assert summary.reason == 'OI +8.0%, avg funding +0.0075%'


def test_build_market_regime_summary_detects_crowded_long_pressure():
    summary = build_market_regime_summary(
        [
            _metric('open_interest_usd', 100.0, '2026-04-20T00:00:00Z'),
            _metric('funding_rate', 0.040, '2026-04-20T00:00:00Z'),
            _metric('open_interest_usd', 101.0, '2026-04-27T00:00:00Z'),
            _metric('funding_rate', 0.050, '2026-04-27T00:00:00Z'),
        ]
    )

    assert summary.label == 'Crowded long pressure'
    assert summary.reason == 'OI +1.0%, avg funding +0.0450%'


def test_build_market_regime_summary_detects_deleveraging():
    summary = build_market_regime_summary(
        [
            _metric('open_interest_usd', 100.0, '2026-04-20T00:00:00Z'),
            _metric('open_interest_usd', 90.0, '2026-04-27T00:00:00Z'),
        ]
    )

    assert summary.label == 'Deleveraging'
    assert summary.reason == 'OI -10.0%, funding unavailable'


def test_build_market_regime_summary_handles_insufficient_history():
    summary = build_market_regime_summary(
        [_metric('open_interest_usd', 100.0, '2026-04-20T00:00:00Z')]
    )

    assert summary.label == 'Insufficient regime history'
    assert summary.reason == 'open interest history unavailable'


def _metric(
    metric_name: str,
    metric_value: float,
    timestamp: str,
) -> CryptoSignalMarketRegimeMetric:
    return CryptoSignalMarketRegimeMetric(
        metric_name=metric_name,
        metric_value=metric_value,
        unit='usd' if metric_name == 'open_interest_usd' else 'percent',
        source_timestamp_utc=datetime.datetime.fromisoformat(
            timestamp.replace('Z', '+00:00')
        ),
    )
