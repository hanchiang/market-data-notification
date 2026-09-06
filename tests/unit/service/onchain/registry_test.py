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

    def test_only_touch_grass_has_operator_supplied_sources(self):
        """The other three had none; their published surfaces arrive in phase 1b
        through the structural hop, not by being typed into the registry."""
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        assert {s.url for s in registry.projects['touch-grass'].sources} == {
            'https://www.touchgrass.family',
            'https://www.touchgrass.family/app',
            'https://www.touchgrass.family/docs',
            'https://x.com/TouchGrassRWA',
        }
        for key in ('not-a-website', 'predict-fwa', 'zzz'):
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

    def test_the_chain_explorer_is_admitted_as_a_source(self, onchain_repository):
        """Design DG-3: the explorer and the RPC are sources the collectors read,
        and source admission is phase 1b, so the registry admits them."""
        registry = load_registry(DEFAULT_REGISTRY_PATH)
        upsert_registry(onchain_repository, registry)
        onchain_repository.commit()

        chain = onchain_repository.get_entity_by_key(f'chain:{ROBINHOOD_CHAIN_ID}')
        classes = {
            s['class'] for s in onchain_repository.get_sources_for_entity(chain['id'])
        }
        assert 'chain_explorer' in classes

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
