"""Store one raw response as evidence (requirement P10, criterion A12).

Two rules the store cannot enforce on its own and this module does:

* **The run id and span id come from the logging context**, not from the caller,
  so an evidence row and the log lines written beside it carry the same ids by
  construction rather than by a caller remembering to pass them.
* **A keyed endpoint's URL is never stored.** The archive endpoint's URL is the
  credential. `endpoint_kind` ('alchemy', 'public', 'dexscreener', 'blockscout')
  is the provenance, and a keyed HTTP read is refused loudly rather than
  redacted quietly: nothing in phase 1a fetches HTTP from a keyed endpoint, so
  reaching that branch is a bug, and a silent redaction would hide it.
"""
import logging
from typing import Any, Optional

from src.service.onchain.observability import current_run_id, current_span_id
from src.service.onchain.repository import OnchainRepository

logger = logging.getLogger('Onchain evidence')

KIND_JSONRPC = 'jsonrpc'
KIND_HTTP = 'http'

# Endpoint kinds whose URL carries an API key. The monitor's `Endpoint.kind` uses
# 'alchemy' for the keyed archive endpoint and 'public' for the open node.
KEYED_ENDPOINT_KINDS = frozenset({'alchemy'})


class KeyedEndpointUrlError(RuntimeError):
    """An HTTP evidence row was about to store a keyed endpoint's URL."""


class MissingRunContextError(RuntimeError):
    """Evidence was written outside a run context, so it would have no run id."""


def store_response(
    repository: OnchainRepository,
    *,
    kind: str,
    method_or_url: str,
    body: Any,
    endpoint_kind: str,
    entity_id: Optional[int] = None,
    params: Optional[Any] = None,
    block: Optional[int] = None,
    run_id: Optional[int] = None,
    span_id: Optional[str] = None,
) -> int:
    """Insert one evidence row and return its id.

    `run_id` and `span_id` default to the logging context; passing them
    explicitly is for tests and for a caller that writes evidence on behalf of a
    span it is not inside.
    """
    if kind not in (KIND_JSONRPC, KIND_HTTP):
        raise ValueError(f'unknown evidence kind {kind!r}')

    effective_run_id = run_id if run_id is not None else current_run_id()
    if effective_run_id is None:
        raise MissingRunContextError(
            'evidence must be written inside a run context; a row with no run '
            'id cannot be traced from an alert (criterion A12)'
        )

    if kind == KIND_HTTP and endpoint_kind in KEYED_ENDPOINT_KINDS:
        raise KeyedEndpointUrlError(
            f'refusing to store an HTTP URL for keyed endpoint kind '
            f'{endpoint_kind!r}: the URL is the credential'
        )

    return repository.insert_evidence(
        run_id=effective_run_id,
        span_id=span_id if span_id is not None else current_span_id(),
        entity_id=entity_id,
        kind=kind,
        method_or_url=method_or_url,
        params=params,
        body=body,
        endpoint_kind=endpoint_kind,
        block=block,
    )
