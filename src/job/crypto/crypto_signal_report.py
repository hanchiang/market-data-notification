import argparse
import asyncio
import logging

from src.config import config
from src.job.crypto.crypto_signal_formatter import build_crypto_signal_message
from src.notification_destination.telegram_notification import (
    init_telegram_bots,
    send_crypto_signal_message,
)
from src.runtime.runtime_mode import RuntimeMode
from src.service.crypto_signal.market_regime import (
    FUNDING_RATE_METRIC,
    OPEN_INTEREST_METRIC,
    build_market_regime_summary,
)
from src.service.crypto_signal.market_regime_collector import (
    AGGREGATE_INSTRUMENT_SCOPE,
    AGGREGATE_VENUE_SCOPE,
    COINALYZE_PROVIDER,
)
from src.service.crypto_signal.repository import CryptoSignalRepository
from src.service.crypto_signal.scorer import build_digest_view, get_window_start


logger = logging.getLogger('Crypto signal report')


def _load_market_regime_summary(
    *,
    repository: CryptoSignalRepository,
    latest_snapshot,
    window_label: str,
):
    try:
        provider = config.get_crypto_signal_market_regime_provider()
        if provider != COINALYZE_PROVIDER:
            return None
        # Match the scheduled operator digest: report output should describe
        # the BTC perpetual basket, not whichever raw venue rows are present.
        metrics = repository.get_market_regime_metrics(
            runtime_mode=latest_snapshot.run.runtime_mode,
            start_timestamp_utc=get_window_start(
                latest_snapshot,
                window_label=window_label,
            ),
            end_timestamp_utc=latest_snapshot.run.run_timestamp_utc,
            metric_names=[OPEN_INTEREST_METRIC, FUNDING_RATE_METRIC],
            provider=COINALYZE_PROVIDER,
            venue_scope=AGGREGATE_VENUE_SCOPE,
            instrument_scope=AGGREGATE_INSTRUMENT_SCOPE,
            interval=config.get_crypto_signal_market_regime_interval(),
        )
    except Exception:
        logger.warning(
            'Failed to load crypto signal market-regime summary',
            exc_info=True,
        )
        return None
    if len(metrics) == 0:
        return None
    return build_market_regime_summary(metrics)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description='Render a crypto signal report from SQLite history',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  ENV=dev PYTHONPATH="$(pwd)" poetry run python src/job/crypto/crypto_signal_report.py --window 7d --limit 3 --send_telegram=0 --test_mode=1
  ENV=dev PYTHONPATH="$(pwd)" poetry run python src/job/crypto/crypto_signal_report.py --window 7d --limit 3 --send_telegram=1 --test_mode=1

Phase-1 safety:
  Render with --send_telegram=0 first. Only use --send_telegram=1 after
  confirming CRYPTO_SIGNAL_RECIPIENT_ID or CRYPTO_TELEGRAM_ADMIN_ID is a
  private/admin recipient, not the public crypto channel.
""",
    )
    parser.add_argument('--window', choices=['3d', '7d', '30d'], default='7d')
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--send_telegram', type=int, choices=[0, 1], default=0)
    parser.add_argument('--test_mode', type=int, choices=[0, 1], default=0)
    args = parser.parse_args()

    runtime_mode = RuntimeMode.from_test_mode(args.test_mode == 1)
    repository = CryptoSignalRepository(runtime_mode=runtime_mode)
    latest_snapshot = repository.get_latest_snapshot()
    if latest_snapshot is None:
        logger.info('No crypto signal snapshots are available')
        return

    history = repository.get_snapshots_since(
        get_window_start(latest_snapshot, window_label=args.window)
    )
    watchlist_coin_ids = {
        coin_id for _symbol, coin_id in config.get_crypto_signal_watchlist()
    }
    tracked_universe_coin_ids = {
        coin_id for _symbol, coin_id in config.get_crypto_signal_tracked_universe()
    }
    view = build_digest_view(
        latest_snapshot=latest_snapshot,
        history=history,
        watchlist_coin_ids=watchlist_coin_ids,
        tracked_universe_coin_ids=tracked_universe_coin_ids,
        window_label=args.window,
        limit=args.limit,
        min_dynamic_price_usd=config.get_crypto_signal_dynamic_candidate_min_price_usd(),
        min_dynamic_volume_24h=config.get_crypto_signal_dynamic_candidate_min_volume_24h(),
        market_regime_summary=_load_market_regime_summary(
            repository=repository,
            latest_snapshot=latest_snapshot,
            window_label=args.window,
        ),
    )
    message = build_crypto_signal_message(view)
    print(message)
    logger.info(message)

    if args.send_telegram == 1:
        init_telegram_bots()
        await send_crypto_signal_message(
            message=message,
            chat_id=config.get_crypto_signal_recipient_id(),
            runtime_mode=runtime_mode,
        )


if __name__ == '__main__':
    # Usage:
    # - Inspect stored history without hitting live providers or sending Telegram:
    #   ENV=dev PYTHONPATH="$(pwd)" poetry run python src/job/crypto/crypto_signal_report.py --window 7d --limit 3 --send_telegram=0 --test_mode=1
    # - Send the same rendered report to the private/admin signal recipient:
    #   ENV=dev PYTHONPATH="$(pwd)" poetry run python src/job/crypto/crypto_signal_report.py --window 7d --limit 3 --send_telegram=1 --test_mode=1
    asyncio.run(main())
