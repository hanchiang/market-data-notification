"""Restore the telegram module's global maps around every test in this package.

`init_telegram_bots()` mutates `chat_id_to_telegram_client` and the two
`market_data_type_to_*` maps IN PLACE, and rebinds six bot globals.
`monkeypatch.setattr` undoes a rebinding it performed, not either of these, so a
test that calls the real init leaves both behind for the rest of the session --
and any later test reaching a real sender would find a live-looking client under
whatever id the environment supplied. The bots matter separately from the maps:
`_resolve_crypto_signal_target` reads `crypto_admin_bot` directly and consults no
map, so `send_crypto_signal_message` is reachable through the bots alone.

Snapshot and restore both, so no test's network safety depends on what an
earlier one left behind.
"""
import pytest

from src.notification_destination import telegram_notification

# Mutated in place, so restoring means clear()/update(), not setattr.
_GLOBAL_MAPS = (
    'chat_id_to_telegram_client',
    'market_data_type_to_admin_chat_id',
    'market_data_type_to_chat_id',
)
# Rebound by `init_telegram_bots`, so plain attributes restore them.
_BOT_GLOBALS = (
    'stocks_bot',
    'stocks_admin_bot',
    'stocks_dev_bot',
    'crypto_bot',
    'crypto_admin_bot',
    'crypto_dev_bot',
)


@pytest.fixture(autouse=True)
def restore_telegram_module_globals():
    saved_maps = {
        name: dict(getattr(telegram_notification, name)) for name in _GLOBAL_MAPS
    }
    saved_bots = {
        name: getattr(telegram_notification, name) for name in _BOT_GLOBALS
    }
    yield
    for name, original in saved_maps.items():
        current = getattr(telegram_notification, name)
        current.clear()
        current.update(original)
    for name, original in saved_bots.items():
        setattr(telegram_notification, name, original)
