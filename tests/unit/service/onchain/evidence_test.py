"""Evidence rows: provenance (P10) and the ids that make a failure traceable
end to end without a tracing backend (A12).
"""
import psycopg
import pytest

from src.service.onchain.evidence import (
    KIND_HTTP,
    KIND_JSONRPC,
    KeyedEndpointUrlError,
    MissingRunContextError,
    store_response,
)
from src.service.onchain.observability import collector_span, run_context


class TestProvenance:
    def test_a_row_carries_the_endpoint_kind_the_block_and_the_read_time(
        self, onchain_repository
    ):
        run_id = onchain_repository.start_run('onchain.build')
        token = onchain_repository.upsert_entity(level='token', key='token:4663:0xa')
        with run_context(run_id, 'onchain.build'):
            evidence_id = store_response(
                onchain_repository,
                entity_id=token,
                kind=KIND_JSONRPC,
                method_or_url='eth_call',
                params=[{'to': '0xa', 'data': '0x18160ddd'}],
                body={'jsonrpc': '2.0', 'result': '0x0'},
                endpoint_kind='alchemy',
                block=51234567,
            )
        onchain_repository.commit()

        [row] = onchain_repository.get_evidence_for_run(run_id)
        assert row['id'] == evidence_id
        assert row['endpoint_kind'] == 'alchemy'
        assert row['block'] == 51234567
        assert row['read_at'] is not None
        assert row['entity_id'] == token


class TestTracing:
    def test_the_run_and_span_ids_come_from_the_logging_context(
        self, onchain_repository
    ):
        """The same ids the log lines carry, by construction rather than by a
        caller remembering to pass them (A12)."""
        run_id = onchain_repository.start_run('onchain.build')
        chain = onchain_repository.upsert_entity(level='chain', key='chain:4663')
        with run_context(run_id, 'onchain.build'):
            with collector_span('touch-grass') as span:
                store_response(
                    onchain_repository, entity_id=chain, kind=KIND_JSONRPC,
                    method_or_url='eth_call', body={}, endpoint_kind='public',
                )
            store_response(
                onchain_repository, entity_id=chain, kind=KIND_HTTP,
                method_or_url='https://api.dexscreener.com/latest/dex/pairs/robinhood/0x1',
                body={}, endpoint_kind='dexscreener',
            )
        onchain_repository.commit()

        rows = onchain_repository.get_evidence_for_run(run_id)
        assert [r['span_id'] for r in rows] == [span, None]
        assert all(r['run_id'] == run_id for r in rows)

    def test_evidence_written_outside_a_run_is_refused(self, onchain_repository):
        """A row with no run id cannot be traced from an alert, so it is not
        written at all rather than written untraceable."""
        chain = onchain_repository.upsert_entity(level='chain', key='chain:4663')
        with pytest.raises(MissingRunContextError):
            store_response(
                onchain_repository, entity_id=chain, kind=KIND_JSONRPC,
                method_or_url='eth_call', body={}, endpoint_kind='public',
            )


class TestKeyedUrls:
    def test_an_http_read_on_a_keyed_endpoint_is_refused(self, onchain_repository):
        """The archive endpoint's URL is the credential. Refused loudly rather
        than redacted quietly: nothing in phase 1a fetches HTTP from a keyed
        endpoint, so reaching this branch is a bug worth surfacing."""
        run_id = onchain_repository.start_run('onchain.build')
        chain = onchain_repository.upsert_entity(level='chain', key='chain:4663')
        with run_context(run_id, 'onchain.build'):
            with pytest.raises(KeyedEndpointUrlError):
                store_response(
                    onchain_repository, entity_id=chain, kind=KIND_HTTP,
                    method_or_url='https://rhc.g.alchemy.com/v2/SECRETKEY',
                    body={}, endpoint_kind='alchemy',
                )
        assert onchain_repository.get_evidence_for_run(run_id) == []

    def test_a_jsonrpc_read_on_a_keyed_endpoint_stores_the_method_not_a_url(
        self, onchain_repository
    ):
        """The ordinary archive read: `method_or_url` is the RPC method, and
        `endpoint_kind` is the provenance."""
        run_id = onchain_repository.start_run('onchain.build')
        chain = onchain_repository.upsert_entity(level='chain', key='chain:4663')
        with run_context(run_id, 'onchain.build'):
            store_response(
                onchain_repository, entity_id=chain, kind=KIND_JSONRPC,
                method_or_url='eth_getStorageAt', body={}, endpoint_kind='alchemy',
            )
        onchain_repository.commit()
        [row] = onchain_repository.get_evidence_for_run(run_id)
        assert row['method_or_url'] == 'eth_getStorageAt'
        assert 'http' not in row['method_or_url']

    def test_a_public_http_url_is_stored(self, onchain_repository):
        """The provider and the explorer carry no key, so their URLs are
        provenance worth having."""
        run_id = onchain_repository.start_run('onchain.build')
        chain = onchain_repository.upsert_entity(level='chain', key='chain:4663')
        url = 'https://robinhoodchain.blockscout.com/api/v2/addresses/0x1'
        with run_context(run_id, 'onchain.build'):
            store_response(
                onchain_repository, entity_id=chain, kind=KIND_HTTP,
                method_or_url=url, body={'hash': '0x1'}, endpoint_kind='blockscout',
            )
        onchain_repository.commit()
        [row] = onchain_repository.get_evidence_for_run(run_id)
        assert row['method_or_url'] == url

    def test_an_unknown_evidence_kind_is_rejected(self, onchain_repository):
        run_id = onchain_repository.start_run('onchain.build')
        chain = onchain_repository.upsert_entity(level='chain', key='chain:4663')
        with run_context(run_id, 'onchain.build'):
            with pytest.raises(ValueError, match='unknown evidence kind'):
                store_response(
                    onchain_repository, entity_id=chain, kind='carrier-pigeon',
                    method_or_url='x', body={}, endpoint_kind='public',
                )


class TestEntityIsRequired:
    """P1: every evidence row references exactly one entity."""

    def test_the_column_is_not_null(self, onchain_repository):
        nullable = onchain_repository.fetch_one(
            'SELECT a.attnotnull AS not_null FROM pg_attribute a '
            "JOIN pg_class c ON c.oid = a.attrelid "
            'JOIN pg_namespace n ON n.oid = c.relnamespace '
            "WHERE n.nspname = 'onchain' AND c.relname = 'evidence' "
            "AND a.attname = 'entity_id'"
        )
        assert nullable['not_null'] is True

    def test_a_row_with_no_entity_is_refused_by_the_store(self, onchain_repository):
        run_id = onchain_repository.start_run('onchain.build')
        with pytest.raises(psycopg.errors.NotNullViolation):
            onchain_repository.insert_evidence(
                run_id=run_id, entity_id=None, kind='jsonrpc',
                method_or_url='eth_call', body={}, endpoint_kind='public',
            )
        onchain_repository.rollback()


class TestKeyedUrlByValue:
    def test_a_keyed_url_mislabelled_public_is_still_refused(
        self, onchain_repository, monkeypatch
    ):
        """The label is the caller\'s claim, not the check. A collector that
        labels an archive read `public` must not get the credential stored."""
        monkeypatch.setenv('ROBINHOOD_CHAIN_RPC_URL', 'https://rhc.g.alchemy.com/v2/SECRETKEY')
        run_id = onchain_repository.start_run('onchain.build')
        chain = onchain_repository.upsert_entity(level='chain', key='chain:4663')
        with run_context(run_id, 'onchain.build'):
            with pytest.raises(KeyedEndpointUrlError) as exc_info:
                store_response(
                    onchain_repository, entity_id=chain, kind=KIND_HTTP,
                    method_or_url='https://rhc.g.alchemy.com/v2/SECRETKEY/x',
                    body={}, endpoint_kind='public',
                )
        # The refusal itself must not repeat the credential.
        assert 'SECRETKEY' not in str(exc_info.value)
        assert onchain_repository.get_evidence_for_run(run_id) == []

    def test_an_unrelated_public_url_is_unaffected(
        self, onchain_repository, monkeypatch
    ):
        monkeypatch.setenv('ROBINHOOD_CHAIN_RPC_URL', 'https://rhc.g.alchemy.com/v2/SECRETKEY')
        run_id = onchain_repository.start_run('onchain.build')
        chain = onchain_repository.upsert_entity(level='chain', key='chain:4663')
        with run_context(run_id, 'onchain.build'):
            store_response(
                onchain_repository, entity_id=chain, kind=KIND_HTTP,
                method_or_url='https://api.dexscreener.com/latest/dex/pairs/robinhood/0x1',
                body={}, endpoint_kind='dexscreener',
            )
        onchain_repository.commit()
        assert len(onchain_repository.get_evidence_for_run(run_id)) == 1

    def test_no_configured_archive_endpoint_does_not_block_everything(
        self, onchain_repository, monkeypatch
    ):
        """With no key configured the value check has nothing to compare against
        and must not refuse ordinary reads."""
        monkeypatch.delenv('ROBINHOOD_CHAIN_RPC_URL', raising=False)
        run_id = onchain_repository.start_run('onchain.build')
        chain = onchain_repository.upsert_entity(level='chain', key='chain:4663')
        with run_context(run_id, 'onchain.build'):
            store_response(
                onchain_repository, entity_id=chain, kind=KIND_HTTP,
                method_or_url='https://robinhoodchain.blockscout.com/api/v2/addresses/0x1',
                body={}, endpoint_kind='blockscout',
            )
        onchain_repository.commit()
        assert len(onchain_repository.get_evidence_for_run(run_id)) == 1
