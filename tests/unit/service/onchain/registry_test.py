"""The registry file: validation, and the upsert that makes "a project is
configuration, never a code path" (V4, A1) true.

The shipped `registry/projects.json` is loaded and asserted against as it will
actually ship, not against a copy: the seed four ARE the acceptance criterion's
input, and a test over a hand-built registry would not notice a typo in the file
the build reads.
"""
import copy
import json

import pytest

from src.service.onchain.config import (
    DEFAULT_REGISTRY_PATH,
    ROBINHOOD_CHAIN_ID,
    VERIFIED_UNISWAP_ADDRESSES,
)
from src.service.onchain import registry as registry_module
from src.service.onchain.registry import (
    RegistryError,
    load_registry,
    parse_registry,
    upsert_registry,
)

SHIPPED = json.loads(DEFAULT_REGISTRY_PATH.read_text())


def _payload():
    return copy.deepcopy(SHIPPED)


class TestShippedRegistry:
    def test_the_shipped_file_loads(self):
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        assert set(registry.projects) == {
            'touch-grass', 'not-a-website', 'predict-fwa', 'zzz'
        }
        assert set(registry.chains) == {ROBINHOOD_CHAIN_ID}

    def test_the_seed_pool_references_are_the_operators_own_links(self):
        """Verbatim from the operator's Dexscreener links in the ticket. Two are
        32-byte v4 pool ids and two are 20-byte v3 pool addresses, which is why
        the identity requirement (P2) forbids assuming either is a token."""
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        assert registry.projects['touch-grass'].pool_ref == (
            '0x64c5dbbee60473344dc6f7b11391ff9c7bb7464c0d5ecfb0d311ff26df9f8c77'
        )
        assert registry.projects['not-a-website'].pool_ref == (
            '0x6d489e07d7fe2b4bc5749f75d56337888b68a34a'
        )
        assert registry.projects['predict-fwa'].pool_ref == (
            '0x5313a71595178721e1d58fd4cb459e4981aae441'
        )
        assert registry.projects['zzz'].pool_ref == (
            '0x6538e2c223ed70228114983afecbe5e69fe627e2fafdf367bdd6bdeff2ad391f'
        )

    def test_the_reference_width_selects_the_pool_kind(self):
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        assert registry.projects['touch-grass'].pool_ref_kind == 'pool_id'
        assert registry.projects['zzz'].pool_ref_kind == 'pool_id'
        assert registry.projects['not-a-website'].pool_ref_kind == 'pool_address'
        assert registry.projects['predict-fwa'].pool_ref_kind == 'pool_address'

    def test_the_chain_carries_the_five_verified_uniswap_addresses(self):
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        assert registry.chains[ROBINHOOD_CHAIN_ID].uniswap == {
            key: value.lower()
            for key, value in VERIFIED_UNISWAP_ADDRESSES[ROBINHOOD_CHAIN_ID].items()
        }

    def test_operator_supplied_sources_are_exactly_what_was_given(self):
        """Touch Grass (2026-09-06) and Not A Website (2026-09-11) carry the
        sources the operator supplied, verbatim from the ticket; the other two
        have none, and their published surfaces arrive in phase 1b through the
        structural hop, not by being typed into the registry."""
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        assert {s.url for s in registry.projects['touch-grass'].sources} == {
            'https://www.touchgrass.family',
            'https://www.touchgrass.family/app',
            'https://www.touchgrass.family/docs',
            'https://x.com/TouchGrassRWA',
        }
        assert {s.url for s in registry.projects['not-a-website'].sources} == {
            'http://notawebsite.fun/',
            'https://notawebsite.fun/docs',
            'https://pagemarkets.com/',
            'https://x.com/notawebsite_rh',
        }
        for key in ('predict-fwa', 'zzz'):
            assert registry.projects[key].sources == ()


class TestValidation:
    def test_an_unknown_archetype_is_rejected(self):
        payload = _payload()
        payload['projects'][0]['archetype'] = 'rebasing-treasury'
        with pytest.raises(RegistryError, match='unknown archetype'):
            parse_registry(payload)

    def test_a_project_on_an_undeclared_chain_is_rejected(self):
        payload = _payload()
        payload['projects'][0]['chain_id'] = 999
        with pytest.raises(RegistryError, match='is not declared'):
            parse_registry(payload)

    def test_a_pool_reference_of_the_wrong_width_is_rejected(self):
        """A token address is 20 bytes too, so width alone cannot catch that --
        but a truncated or padded reference silently resolves nothing, and the
        error has to arrive before any endpoint is touched."""
        payload = _payload()
        payload['projects'][0]['pool_ref'] = '0xdeadbeef'
        with pytest.raises(RegistryError, match='pool_ref must be'):
            parse_registry(payload)

    def test_an_edited_uniswap_address_is_rejected_against_the_verified_set(self):
        payload = _payload()
        payload['chains'][0]['uniswap']['v4_state_view'] = (
            '0x0000000000000000000000000000000000000001'
        )
        with pytest.raises(RegistryError, match='on-chain-verified address'):
            parse_registry(payload)

    def test_a_missing_uniswap_address_is_rejected(self):
        payload = _payload()
        del payload['chains'][0]['uniswap']['v3_factory']
        with pytest.raises(RegistryError, match='missing uniswap addresses'):
            parse_registry(payload)

    def test_a_duplicate_project_key_is_rejected(self):
        payload = _payload()
        payload['projects'].append(copy.deepcopy(payload['projects'][0]))
        with pytest.raises(RegistryError, match='duplicate project key'):
            parse_registry(payload)

    def test_an_unknown_source_class_is_rejected(self):
        payload = _payload()
        payload['projects'][0]['sources'][0]['class'] = 'carrier-pigeon'
        with pytest.raises(RegistryError, match='unknown source class'):
            parse_registry(payload)

    def test_malformed_json_names_the_file(self, tmp_path):
        path = tmp_path / 'projects.json'
        path.write_text('{not json')
        with pytest.raises(RegistryError, match='not valid JSON'):
            load_registry(path)

    def test_a_missing_file_is_an_error_not_an_empty_registry(self, tmp_path):
        with pytest.raises(RegistryError, match='not found'):
            load_registry(tmp_path / 'absent.json')


class TestUpsert:
    def test_the_registry_becomes_entities_under_chain_under_market(
        self, onchain_repository
    ):
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        project_ids = upsert_registry(onchain_repository, registry)
        onchain_repository.commit()

        assert set(project_ids) == set(registry.projects)
        market = onchain_repository.get_entity_by_key('market')
        chain = onchain_repository.get_entity_by_key(f'chain:{ROBINHOOD_CHAIN_ID}')
        assert chain['parent_id'] == market['id']
        for key in registry.projects:
            project = onchain_repository.get_entity_by_key(f'project:{key}')
            assert project['parent_id'] == chain['id']
            assert project['attrs_json']['archetype'] == 'launchpad-fixed-supply'

    def test_registry_sources_are_admitted_by_the_registry(self, onchain_repository):
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        project_ids = upsert_registry(onchain_repository, registry)
        onchain_repository.commit()

        sources = onchain_repository.get_sources_for_entity(
            project_ids['touch-grass']
        )
        assert len(sources) == 4
        assert {s['admission'] for s in sources} == {'admitted'}
        assert {s['admitted_by'] for s in sources} == {'registry'}
        assert all(s['admitted_at'] is not None for s in sources)

    def test_the_chain_explorer_rpc_and_dex_provider_are_admitted_as_chain_sources(
        self, onchain_repository
    ):
        """Design DG-3: the explorer, the RPC and the DEX provider are sources
        the collectors read, and source admission is phase 1b, so the registry
        admits them -- three chain-level rows, so the coverage grid (UX brief,
        slice B) reads every class from one table."""
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        upsert_registry(onchain_repository, registry)
        onchain_repository.commit()

        chain = onchain_repository.get_entity_by_key(f'chain:{ROBINHOOD_CHAIN_ID}')
        rows = onchain_repository.get_sources_for_entity(chain['id'])
        by_class = {s['class']: s for s in rows}
        assert len(rows) == 3
        assert set(by_class) == {'chain_rpc', 'chain_explorer', 'dex_provider'}
        assert by_class['chain_explorer']['url_or_handle'] == 'https://robinhoodchain.blockscout.com/api/v2/'
        assert by_class['dex_provider']['url_or_handle'] == 'https://dexscreener.com/robinhood'
        for row in rows:
            assert row['admission'] == 'admitted' and row['admitted_by'] == 'registry'
            assert row['evidence_json']['path'] == 'src/service/onchain/registry/projects.json'

        # Idempotent: the nightly re-upsert adds no fourth row.
        upsert_registry(onchain_repository, registry)
        onchain_repository.commit()
        assert len(onchain_repository.get_sources_for_entity(chain['id'])) == 3

    def test_the_rpc_row_is_the_host_and_never_carries_the_key(
        self, onchain_repository, monkeypatch
    ):
        """The archive URL carries its key in the path, and `url_or_handle` is
        a stored column that reaches the dossier JSON and the overview page.
        A fake key made of characters the host does not contain, in both the
        path and a query string: none of it may reach the row."""
        # Built at runtime from a low-entropy seed: a key-shaped literal in a
        # test file trips the secret scan on commit, and rightly so.
        fake_key = ''.join(c * 3 for c in 'ZQXJWVUP') + '0123456789'
        monkeypatch.setenv(
            'ROBINHOOD_CHAIN_RPC_URL',
            f'https://robinhood-mainnet.g.alchemy.com/v2/{fake_key}?apikey={fake_key}',
        )
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        upsert_registry(onchain_repository, registry)
        onchain_repository.commit()

        chain = onchain_repository.get_entity_by_key(f'chain:{ROBINHOOD_CHAIN_ID}')
        rpc = [s for s in onchain_repository.get_sources_for_entity(chain['id']) if s['class'] == 'chain_rpc']
        assert len(rpc) == 1
        handle = rpc[0]['url_or_handle']
        assert handle == 'robinhood-mainnet.g.alchemy.com'
        assert '?' not in handle and 'key' not in handle.lower() and fake_key not in handle
        assert set(handle).isdisjoint(set(fake_key))
        assert fake_key not in str(rpc[0]['evidence_json'])

    def test_without_an_archive_key_the_rpc_row_is_the_public_host(
        self, onchain_repository, monkeypatch
    ):
        monkeypatch.delenv('ROBINHOOD_CHAIN_RPC_URL', raising=False)
        monkeypatch.setenv('ROBINHOOD_CHAIN_PUBLIC_RPC_URL', 'https://rpc.robinhood.example/')
        assert registry_module.rpc_source_handle() == 'rpc.robinhood.example'

    @pytest.mark.parametrize('url', [
        'not a url',
        # An unclosed IPv6 literal: `urlsplit` raises rather than returning
        # an empty host, and the label must still be the answer.
        'https://[::1/v2/k',
    ])
    def test_an_endpoint_with_no_readable_host_is_labelled_configured(self, monkeypatch, url):
        monkeypatch.setenv('ROBINHOOD_CHAIN_RPC_URL', url)
        assert registry_module.rpc_source_handle() == 'configured (alchemy)'

    def test_a_changed_rpc_host_retires_the_previous_row(self, onchain_repository, monkeypatch):
        """The RPC row is keyed by host, so a moved endpoint adds a row rather
        than rewriting one; the old host must not stay admitted. The registry
        is the only writer that lowers an admission, and only for this case."""
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        monkeypatch.setenv('ROBINHOOD_CHAIN_RPC_URL', 'https://host-a.example/v2/FAKEKEY')
        upsert_registry(onchain_repository, registry)
        onchain_repository.commit()
        monkeypatch.setenv('ROBINHOOD_CHAIN_RPC_URL', 'https://host-b.example/v2/FAKEKEY')
        upsert_registry(onchain_repository, registry)
        onchain_repository.commit()

        chain = onchain_repository.get_entity_by_key(f'chain:{ROBINHOOD_CHAIN_ID}')
        rpc = {
            s['url_or_handle']: s
            for s in onchain_repository.get_sources_for_entity(chain['id']) if s['class'] == 'chain_rpc'
        }
        assert set(rpc) == {'host-a.example', 'host-b.example'}
        assert rpc['host-b.example']['admission'] == 'admitted'
        retired = rpc['host-a.example']
        assert retired['admission'] == 'retired' and retired['admitted_by'] == 'registry'
        assert retired['evidence_json']['replaced_by'] == 'host-b.example'
        assert retired['evidence_json']['recorded_at']
        first_evidence = dict(retired['evidence_json'])

        # A third load with the same host changes nothing: the retired row
        # keeps the evidence of when it was first replaced, and the other two
        # chain classes are untouched.
        upsert_registry(onchain_repository, registry)
        onchain_repository.commit()
        rows = onchain_repository.get_sources_for_entity(chain['id'])
        assert len(rows) == 4
        assert {s['admission'] for s in rows if s['class'] != 'chain_rpc'} == {'admitted'}
        assert next(s for s in rows if s['url_or_handle'] == 'host-a.example')['evidence_json'] == first_evidence

    def test_a_second_upsert_changes_nothing(self, onchain_repository):
        """The build re-upserts every night; that must be a no-op, including for
        `admitted_at`, which records when the source was let in."""
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        first = upsert_registry(onchain_repository, registry)
        onchain_repository.commit()
        before = onchain_repository.get_sources_for_entity(first['touch-grass'])

        second = upsert_registry(onchain_repository, registry)
        onchain_repository.commit()
        after = onchain_repository.get_sources_for_entity(second['touch-grass'])

        assert first == second
        assert [s['admitted_at'] for s in before] == [s['admitted_at'] for s in after]
        assert onchain_repository.fetch_one(
            'SELECT count(*) AS n FROM onchain.entity'
        )['n'] == 1 + 1 + len(registry.projects)

    def test_a_fifth_project_needs_no_code_change(self, onchain_repository):
        """Criterion A1's configuration half: a fifth object in `projects` runs
        the same path, with nothing in this repository naming it."""
        payload = _payload()
        payload['projects'].append({
            'key': 'fifth-project',
            'display_name': 'Fifth Project',
            'chain_id': ROBINHOOD_CHAIN_ID,
            'archetype': 'launchpad-fixed-supply',
            'pool_ref': '0x' + 'ab' * 20,
            'sources': [],
        })
        registry = parse_registry(payload)

        project_ids = upsert_registry(onchain_repository, registry)
        onchain_repository.commit()

        assert 'fifth-project' in project_ids
        entity = onchain_repository.get_entity_by_key('project:fifth-project')
        assert entity['attrs_json']['pool_ref_kind'] == 'pool_address'


class TestModuleResolution:
    def test_the_registry_import_resolves_to_the_module_not_the_directory(self):
        """`registry.py` sits beside a `registry/` directory and wins only
        because that directory is not a package. A mechanical assertion, because
        adding `registry/__init__.py` would silently redirect every import here
        to an empty package and a README warning does not run."""
        assert registry_module.__file__.endswith('registry.py')
        assert hasattr(registry_module, 'load_registry')
        assert not (DEFAULT_REGISTRY_PATH.parent / '__init__.py').exists()


class TestDriftGuards:
    def test_a_drifted_dexscreener_slug_is_rejected(self):
        """The slug is the provider's own name for the chain and is not
        derivable from the id, so it is knowledge in two places. A slug that
        matches nothing does not raise at the provider -- it returns no pairs."""
        payload = _payload()
        payload['chains'][0]['dexscreener_slug'] = 'robinhood-chain'
        with pytest.raises(RegistryError, match='shipped constant'):
            parse_registry(payload)

    def test_two_projects_cannot_claim_the_same_pool(self):
        """The pool entity key is derived from (chain, reference), so the second
        project's health section would silently read the first project's
        custody."""
        payload = _payload()
        duplicate = copy.deepcopy(payload['projects'][0])
        duplicate['key'] = 'touch-grass-copy'
        payload['projects'].append(duplicate)
        with pytest.raises(RegistryError, match='both name pool'):
            parse_registry(payload)

    def test_the_same_reference_on_a_different_chain_is_allowed(self):
        """Only (chain, reference) collides; the same address on two chains is
        two different pools."""
        payload = _payload()
        second_chain = copy.deepcopy(payload['chains'][0])
        second_chain['chain_id'] = 8453
        second_chain['key'] = 'base'
        second_chain['dexscreener_slug'] = 'base'
        payload['chains'].append(second_chain)
        elsewhere = copy.deepcopy(payload['projects'][0])
        elsewhere['key'] = 'touch-grass-on-base'
        elsewhere['chain_id'] = 8453
        payload['projects'].append(elsewhere)
        assert len(parse_registry(payload).projects) == 5


class TestAdmissionIsNotOverwritten:
    def test_a_suspended_source_survives_the_nightly_upsert(
        self, onchain_repository
    ):
        """The registry re-upserts every night. A source the operator suspends
        (P6, phase 2) must not silently revert to `admitted` on the next build."""
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        project_ids = upsert_registry(onchain_repository, registry)
        onchain_repository.commit()

        with onchain_repository.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE onchain.source SET admission = 'suspended', "
                "admitted_by = 'operator' WHERE url_or_handle = %s",
                ('https://x.com/TouchGrassRWA',),
            )
        onchain_repository.commit()

        upsert_registry(onchain_repository, registry)
        onchain_repository.commit()

        sources = {
            s['url_or_handle']: s
            for s in onchain_repository.get_sources_for_entity(
                project_ids['touch-grass']
            )
        }
        suspended = sources['https://x.com/TouchGrassRWA']
        assert suspended['admission'] == 'suspended'
        assert suspended['admitted_by'] == 'operator'
        # The others are untouched.
        assert sources['https://www.touchgrass.family']['admission'] == 'admitted'

    def test_a_candidate_source_is_raised_by_the_registry(self, onchain_repository):
        """The one direction that IS allowed: a candidate the operator has not
        ruled on yet becomes admitted when the registry names it."""
        source_id = onchain_repository.upsert_source(
            source_class='web',
            url_or_handle='https://www.touchgrass.family',
            admission='candidate',
        )
        onchain_repository.commit()

        registry = load_registry(DEFAULT_REGISTRY_PATH)
        upsert_registry(onchain_repository, registry)
        onchain_repository.commit()

        row = onchain_repository.fetch_one(
            'SELECT * FROM onchain.source WHERE id = %s', (source_id,)
        )
        assert row['admission'] == 'admitted'
        assert row['admitted_by'] == 'registry'


class TestPathResolution:
    def test_a_tilde_in_the_log_dir_is_expanded(self, monkeypatch):
        """`.env` is literal text, so `ONCHAIN_LOG_DIR=~/onchain-data/logs`
        arrives with the tilde intact; `Path()` would create a directory
        actually named `~` under wherever cron started the job."""
        from pathlib import Path as _Path

        from src.service.onchain.config import get_log_dir, get_registry_path

        monkeypatch.setenv('ONCHAIN_LOG_DIR', '~/onchain-data/logs/onchain')
        resolved = get_log_dir()
        assert '~' not in str(resolved)
        assert resolved == _Path.home() / 'onchain-data' / 'logs' / 'onchain'

        monkeypatch.setenv('ONCHAIN_REGISTRY_PATH', '~/registry.json')
        assert '~' not in str(get_registry_path())

    def test_an_empty_override_falls_back_to_the_default(self, monkeypatch):
        from src.service.onchain.config import DEFAULT_LOG_DIR, get_log_dir

        monkeypatch.setenv('ONCHAIN_LOG_DIR', '   ')
        assert get_log_dir() == DEFAULT_LOG_DIR
