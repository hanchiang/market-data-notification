"""The chain explorer as its own failure unit (design D8).

The explorer answered 403, 500 and 200 to identical requests on 2026-09-06, and
two of four creation-transaction reads returned 500 after retries during
sub-stage A. If it shared a failure unit with the section it feeds, the contract
safety section would read `failed` most nights and the chain-derived fields --
which are the ones that matter and which never depended on the explorer -- would
go with it.

So every explorer read goes through `ExplorerUnit`. It records its own state and
never raises into the collector:

* a **404** is an ANSWER: `unavailable`, the explorer has no record. That is the
  ordinary case for an unverified contract and it is not a failure of anything.
* a **403, a 5xx, an HTML body or a rate limit** is the explorer failing:
  the unit is `failed`, every field it feeds carries the error class, the section
  is `partial`, and the chain-only fields diff normally.

The unit is per BUILD, not per call: once the explorer has failed, later reads in
the same build are skipped rather than retried, because a deployment answering
500 answers 500 to the next request too and the section has already lost those
fields.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from market_data_library.core.crypto.blockscout import (
    BlockscoutAddress,
    BlockscoutContract,
    BlockscoutNotFound,
    BlockscoutService,
    BlockscoutTransaction,
)
from market_data_library.util.exception import BlockscoutApiError

logger = logging.getLogger('Onchain explorer')

STATE_OK = 'ok'
STATE_UNAVAILABLE = 'unavailable'
STATE_FAILED = 'failed'


@dataclass
class ExplorerUnit:
    """One build's worth of explorer reads and their shared fate."""

    service: BlockscoutService
    state: str = STATE_OK
    error_class: Optional[str] = None
    calls: int = 0
    bodies: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.state == STATE_FAILED

    async def address(self, address: str) -> Optional[BlockscoutAddress]:
        return await self._read('addresses', self.service.get_address, address)

    async def contract(self, address: str) -> Optional[BlockscoutContract]:
        return await self._read(
            'smart-contracts', self.service.get_smart_contract, address
        )

    async def transaction(self, tx_hash: str) -> Optional[BlockscoutTransaction]:
        return await self._read('transactions', self.service.get_transaction, tx_hash)

    async def _read(self, route: str, method: Any, identifier: str) -> Optional[Any]:
        """None means "no answer", and the unit's `state` says which kind."""
        if self.failed:
            return None
        try:
            result = await method(identifier)
        except BlockscoutApiError as exc:
            # The CLASS only. `str(exc)` carries the endpoint and, for some
            # failures, the upstream body -- neither belongs in a field the
            # report prints or an alert sends.
            self.state = STATE_FAILED
            self.error_class = type(exc).__name__
            logger.warning(
                'explorer unit failed on %s: %s', route, type(exc).__name__
            )
            return None
        self.calls += 1
        if isinstance(result, BlockscoutNotFound):
            return None
        self.bodies.append(
            {'route': route, 'identifier': identifier, 'body': getattr(result, 'raw', {})}
        )
        return result

    def field_state(self) -> str:
        """What an explorer-fed field reads when the explorer gave nothing:
        `failed` with an error class if the explorer broke, `unavailable` if it
        simply has no record."""
        return STATE_FAILED if self.failed else STATE_UNAVAILABLE
