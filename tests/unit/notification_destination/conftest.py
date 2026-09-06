"""Restore the telegram module's global maps around every test in this package.

`init_telegram_bots()` mutates `chat_id_to_telegram_client` and the two
`market_data_type_to_*` maps IN PLACE. `monkeypatch.setattr` undoes a rebinding,
not a mutation, so a test that calls the real init leaves its entries in the
module for the rest of the session -- and any later test reaching a real sender
would find a live-looking client under whatever id the environment supplied.
Snapshot and restore, so no test's network safety depends on what an earlier one
left behind.
"""
import pytest

from src.notification_destination import telegram_notification

_GLOBAL_MAPS = (
    'chat_id_to_telegram_client',
    'market_data_type_to_admin_chat_id',
    'market_data_type_to_chat_id',
)


@pytest.fixture(autouse=True)
def restore_telegram_module_globals():
    saved = {
        name: dict(getattr(telegram_notification, name)) for name in _GLOBAL_MAPS
    }
    yield
    for name, original in saved.items():
        current = getattr(telegram_notification, name)
        current.clear()
        current.update(original)
