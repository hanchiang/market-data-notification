"""Store one raw response as evidence (requirement P10, criterion A12).

Two rules the store cannot enforce on its own and this module does:

* **The run id and span id come from the logging context**, not from the caller,
  so an evidence row and the log lines written beside it carry the same ids by
  construction rather than by a caller remembering to pass them.
* **A keyed endpoint's URL is never stored**, and the check does not trust the
  caller's own label. `endpoint_kind` ('alchemy', 'public', 'dexscreener',
  'blockscout') is the provenance, but a row mislabelled `public` while carrying
  the archive URL would sail past a label check -- so the URL is also compared
  against the configured archive endpoint by value. Refused loudly rather than
  redacted quietly: nothing in phase 1a fetches HTTP from a keyed endpoint, so
  reaching that branch is a bug, and a silent redaction would hide it.
* **Every row names an entity.** The entity model (P1) says every evidence row
  references exactly one entity, so `entity_id` is required here and NOT NULL in
  the store. A run-setup read is a read about the chain entity.
"""
import logging
from typing import Any, Optional

from src.service.onchain.config import get_archive_endpoint
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


def _points_at_the_keyed_endpoint(url: str) -> bool:
    """Whether this URL is the configured archive endpoint, whatever it is
    labelled.

    Compared by prefix against the endpoint's own URL, and neither the URL nor
    this function's inputs are ever logged: the point is to refuse the write,
    not to report what the credential was.
    """
    archive = get_archive_endpoint()
    if archive is None:
        return False
    origin = archive.url.split('?', 1)[0].rstrip('/')
    return bool(origin) and url.split('?', 1)[0].startswith(origin)


def store_response(
    repository: OnchainRepository,
    *,
    entity_id: int,
    kind: str,
    method_or_url: str,
    body: Any,
    endpoint_kind: str,
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
    if kind == KIND_HTTP and _points_at_the_keyed_endpoint(method_or_url):
        # The label said otherwise, which is exactly why the label is not the
        # check. The message names neither the URL nor the label's value.
        raise KeyedEndpointUrlError(
            'refusing to store an HTTP URL that points at the configured '
            'archive endpoint: the URL is the credential, whatever the '
            'endpoint kind claims'
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
