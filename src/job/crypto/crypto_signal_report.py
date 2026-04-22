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
from src.service.crypto_signal.repository import CryptoSignalRepository
from src.service.crypto_signal.scorer import build_digest_view, get_window_start


logger = logging.getLogger('Crypto signal report')


async def main() -> None:
    parser = argparse.ArgumentParser(description='Render a crypto signal report from SQLite history')
    parser.add_argument('--window', choices=['3d', '7d', '30d'], default='7d')
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--send_telegram', type=int, choices=[0, 1], default=0)
    parser.add_argument('--test_mode', type=int, choices=[0, 1], default=0)
    args = parser.parse_args()

    runtime_mode = RuntimeMode.from_test_mode(args.test_mode == 1)
    repository = CryptoSignalRepository()
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
    view = build_digest_view(
        latest_snapshot=latest_snapshot,
        history=history,
        watchlist_coin_ids=watchlist_coin_ids,
        window_label=args.window,
        limit=args.limit,
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
    asyncio.run(main())
