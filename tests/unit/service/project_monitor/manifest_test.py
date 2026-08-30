"""AC8: manifest extraction, the four diffs, and the per-recipe checks.

The bundle fixture is a trimmed excerpt of the REAL bundle
(`index-Dv2f3tfH.js`, build `040b697`, fetched 2026-08-30) covering both
regions the recipes read. Real content, because a synthetic bundle would test
the regexes against text written to satisfy them.
"""
from pathlib import Path

import pytest

from src.service.project_monitor.manifest import (
    ManifestExtractionError,
    ManifestSnapshot,
    diff_registries,
    extract_core_registry,
    extract_labelled_registry,
    extract_registry,
    is_extractor_verified,
)

BUNDLE = (
    Path(__file__).parent / 'fixtures' / 'bundle_excerpt_040b697.js'
).read_text()


def test_both_recipes_extract_from_the_real_bundle():
    """By string content, never by minified identifier: the core set lived under
    `Jc` on 2026-08-29 and the identifier changes on every rebuild."""
    registry, core_count, labelled_count = extract_registry(BUNDLE)
    assert core_count >= 14
    assert labelled_count >= 30
    # Spot-check against addresses recorded independently in the crawl trace.
    assert registry['bondDepository'] == '0xff32a969A0c567129eECD926D04657728E1980C1'
    assert registry['managerSleeve'] == '0x498752D5fa0600CBd613074C151Abe15B3FeC7CB'
    assert registry['inverseBond'] == '0x92166e94Eea5B7799b761653881692f881dFC4C9'


def test_extraction_does_not_depend_on_the_minified_identifier():
    """Rename every two-letter identifier and the recipes must still work.

    This is the actual failure mode: `Jc` becoming `Kd` on the next deploy, with
    an identifier-keyed extractor returning nothing -- which reads exactly like
    a quiet week.
    """
    renamed = BUNDLE.replace('Jc=', 'Zq9=').replace('nn(', 'xy7(')
    registry, core_count, _ = extract_registry(renamed)
    assert core_count >= 14
    assert registry['bondDepository'] == '0xff32a969A0c567129eECD926D04657728E1980C1'


def test_a_zero_yield_core_recipe_raises_even_when_the_other_yields():
    """AC8 names this exactly. A single "zero addresses" check would let the
    core recipe rot behind the labelled one, which yields 30-odd names on its
    own and would mask the failure indefinitely."""
    # Remove the core anchor, leaving the labelled registry intact.
    without_core = BUNDLE.replace('bondDepository:', 'bondDep0sitory:')
    assert extract_labelled_registry(without_core)  # the other recipe still works
    with pytest.raises(ManifestExtractionError):
        extract_registry(without_core)


def test_a_zero_yield_labelled_recipe_also_raises():
    core_only = extract_core_registry(BUNDLE)
    assert core_only
    stripped = BUNDLE.replace('address:"0x', 'addr3ss:"0x')
    with pytest.raises(ManifestExtractionError):
        extract_registry(stripped)


def test_a_snapshot_missing_one_core_name_does_not_raise():
    """AC8's other half. There is deliberately no "at least 15" floor: it would
    turn the project retiring one core contract into a permanent alarm, and the
    `removed` diff is what reports that instead."""
    without_one = BUNDLE.replace(
        'pairOracle:nn("0x929631b33F4070D6f54477fba3FD27566567dAca"),', ''
    )
    registry, core_count, _ = extract_registry(without_one)
    assert 'pairOracle' not in registry
    assert core_count >= 13  # still non-empty, so no raise


def test_a_removed_name_is_reported_as_removed_not_as_no_change():
    previous = {'staking': '0xaaa', 'pairOracle': '0xbbb'}
    current = {'staking': '0xaaa'}
    diffs = diff_registries(previous, current)
    assert diffs.removed == [{'name': 'pairOracle', 'old_address': '0xbbb'}]
    assert diffs.added == [] and diffs.changed == []


def test_the_four_diff_kinds():
    previous = {
        'staking': '0xaaa',
        'presserMarket': '0x' + '0' * 40,
        'treasury': '0xccc',
        'gone': '0xddd',
    }
    current = {
        'staking': '0xaaa',
        'presserMarket': '0xeee',
        'treasury': '0xfff',
        'brandNew': '0x111',
    }
    diffs = diff_registries(previous, current)
    assert [d['name'] for d in diffs.added] == ['brandNew']
    assert [d['name'] for d in diffs.removed] == ['gone']
    assert [d['name'] for d in diffs.placeholder_filled] == ['presserMarket']
    assert [d['name'] for d in diffs.changed] == ['treasury']
    assert len(diffs.as_rows()) == 4


def test_no_diffs_on_an_identical_registry():
    """The diff must be silent on correct input, or every run reports change."""
    registry = {'staking': '0xaaa', 'treasury': '0xbbb'}
    assert not diff_registries(registry, registry)


def test_the_first_snapshot_produces_no_diffs():
    assert not diff_registries(None, {'staking': '0xaaa'})


def _snapshot(registry, filename='index-Dv2f3tfH.js', build='040b697', sha='new'):
    return ManifestSnapshot(
        bundle_filename=filename, build_hash=build, bundle_sha256=sha,
        registry=registry, core_count=14, labelled_count=40,
    )


def test_a_changed_bundle_with_an_unchanged_registry_is_extractor_verified():
    """AC8's evidence, from a real rebuild.

    The app was rebuilt between 2026-08-29 (`index-CGHPYtbP.js`, build
    `1f9a2d5`) and 2026-08-30 (`index-Dv2f3tfH.js`, build `040b697`) while the
    core registry stayed byte-identical. That combination -- new bundle, same
    extracted addresses -- is the only thing that distinguishes "the recipes
    still work" from "nothing has been deployed since the last snapshot".
    """
    registry = extract_core_registry(BUNDLE)
    previous = {
        'bundle_filename': 'index-CGHPYtbP.js',
        'build_hash': '1f9a2d5',
        'bundle_sha256': 'old',
        'registry_json': registry,
    }
    assert is_extractor_verified(previous, _snapshot(registry)) is True


def test_an_unchanged_bundle_is_not_extractor_verified():
    """Without a rebuild in between, an unchanged registry proves nothing about
    the recipes -- so claiming verification there would be a false green."""
    registry = extract_core_registry(BUNDLE)
    previous = {
        'bundle_filename': 'index-Dv2f3tfH.js',
        'build_hash': '040b697',
        'bundle_sha256': 'same',
        'registry_json': registry,
    }
    assert is_extractor_verified(previous, _snapshot(registry, sha='same')) is False


def test_a_changed_registry_is_not_extractor_verified():
    registry = extract_core_registry(BUNDLE)
    previous = {
        'bundle_filename': 'index-CGHPYtbP.js',
        'build_hash': '1f9a2d5',
        'bundle_sha256': 'old',
        'registry_json': {**registry, 'treasury': '0xdifferent'},
    }
    assert is_extractor_verified(previous, _snapshot(registry)) is False


def test_the_first_snapshot_is_not_extractor_verified():
    assert is_extractor_verified(None, _snapshot({'a': '0x1'})) is False


def test_snapshots_and_diffs_persist(repository):
    registry = extract_core_registry(BUNDLE)
    snapshot_id = repository.insert_manifest_snapshot(
        project='NETNET', bundle_filename='index-Dv2f3tfH.js', build_hash='040b697',
        bundle_sha256='abc', registry=registry, extractor_verified=True,
    )
    repository.insert_manifest_diffs(
        snapshot_id, diff_registries({**registry, 'gone': '0x1'}, registry).as_rows()
    )
    repository.commit()

    latest = repository.get_latest_manifest_snapshot('NETNET')
    # `extractor_verified` is a real boolean, not the 1 the SQLite draft wrote.
    assert latest['extractor_verified'] is True
    assert latest['registry_json'] == registry
    rows = repository.fetch_all('SELECT kind, name FROM manifest_diff')
    assert rows == [{'kind': 'removed', 'name': 'gone'}]
