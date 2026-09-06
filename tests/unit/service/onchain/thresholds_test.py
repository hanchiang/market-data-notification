"""The frozen threshold table (P12) and criterion A9.

A9 is checked the way the requirement states it: against version control. The
test finds the commit that introduced the version string with `git log -S` and
compares its author date with the earliest `section_diff` scored under that
version in the store.
"""
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.service.onchain import thresholds
from src.service.onchain.diff import diff_fields
from src.service.onchain.thresholds import (
    STRUCTURAL,
    THRESHOLD_VERSION,
    flagged_changes,
    head_commit,
    record_version,
    threshold_table,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


class TestFrozenTable:
    def test_version_one_is_structural_only(self):
        """Numeric thresholds are frozen at phase 1b; version 1 declares that,
        so a number added here without a version bump is a visible break."""
        table = threshold_table()
        assert table['version'] == '2026-09-06.1'
        assert table['kind'] == 'structural-only'
        assert set(table['structural']) == {
            'contract_safety', 'identity', 'token_economics'
        }

    def test_the_table_matches_the_design_exactly(self):
        assert STRUCTURAL == {
            'contract_safety': [
                'proxy', 'implementation', 'admin', 'privileged_selectors',
                'owner', 'frozen', 'hook_permissions', 'verified_source',
            ],
            'identity': [
                'pool_address', 'pool_id', 'key_matches', 'factory_matches', 'hooks',
            ],
            'token_economics': ['mint_path'],
        }


class TestFlagging:
    def test_a_watched_field_changing_is_flagged(self):
        changes = diff_fields({'owner': '0xa'}, {'owner': '0xb'})
        assert flagged_changes('contract_safety', changes) == [
            {'field': 'owner', 'reason': 'structural_change'}
        ]

    def test_an_unwatched_field_changing_is_not_flagged(self):
        changes = diff_fields({'holders': 10}, {'holders': 11})
        assert flagged_changes('onchain_health', changes) == []
        assert flagged_changes('contract_safety', changes) == []

    def test_a_watched_field_appearing_or_disappearing_is_flagged(self):
        appeared = diff_fields({}, {'mint_path': 'open'})
        assert flagged_changes('token_economics', appeared) == [
            {'field': 'mint_path', 'reason': 'structural_appeared'}
        ]
        gone = diff_fields({'mint_path': 'fixed'}, {})
        assert flagged_changes('token_economics', gone) == [
            {'field': 'mint_path', 'reason': 'structural_disappeared'}
        ]

    def test_an_empty_diff_flags_nothing(self):
        assert flagged_changes('identity', diff_fields({'hooks': '0x0'}, {'hooks': '0x0'})) == []

    def test_a_section_with_no_watched_fields_flags_nothing(self):
        changes = diff_fields({'anything': 1}, {'anything': 2})
        assert flagged_changes('recent_claims', changes) == []


class TestVersionRow:
    def test_the_version_row_is_written_once_and_never_moved(
        self, onchain_repository
    ):
        record_version(onchain_repository, REPO_ROOT)
        onchain_repository.commit()
        first = onchain_repository.get_threshold_version(THRESHOLD_VERSION)

        record_version(onchain_repository, REPO_ROOT)
        onchain_repository.commit()
        second = onchain_repository.get_threshold_version(THRESHOLD_VERSION)

        assert first['first_seen_at'] == second['first_seen_at']
        assert second['table_json'] == threshold_table()

    def test_the_row_records_the_checkouts_head(self, onchain_repository):
        record_version(onchain_repository, REPO_ROOT)
        onchain_repository.commit()
        row = onchain_repository.get_threshold_version(THRESHOLD_VERSION)
        assert row['first_seen_commit'] == head_commit(REPO_ROOT)

    def test_a_missing_git_leaves_the_commit_unrecorded_rather_than_failing(
        self, tmp_path
    ):
        """A build that is otherwise fine must not fail because git is absent;
        the A9 check then reports the version as unverifiable, not as a pass."""
        assert head_commit(tmp_path) is None


def _introducing_commit_date(version: str) -> datetime:
    """The author date of the commit that first introduced `version`.

    `git log -S` finds commits where the number of occurrences of the string
    changed; `--reverse` puts the oldest first, so the FIRST line is the
    introducing commit.
    """
    result = subprocess.run(
        ['git', 'log', '-S', version, '--reverse', '--format=%H %aI',
         '--', 'src/service/onchain/thresholds.py'],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=True,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        pytest.skip(
            f'{version} is not committed yet, so version control cannot date it. '
            'This test becomes meaningful from the commit that adds the module.'
        )
    return datetime.fromisoformat(lines[0].split(' ', 1)[1])


def _earliest_scored_at(repository, version: str):
    return repository.fetch_one(
        'SELECT min(created_at) AS first_scored FROM onchain.section_diff '
        'WHERE threshold_version = %s',
        (version,),
    )['first_scored']


def _score_one_diff(repository, *, section_name: str, version: str) -> int:
    project = repository.upsert_entity(level='project', key='project:zzz')
    run_id = repository.start_run('onchain.build')
    build_id = repository.start_build(run_id=run_id, project_id=project)
    section_id = repository.insert_section(
        build_id=build_id, name=section_name, status='ok', fields={'owner': '0xb'},
    )
    changes = diff_fields({'owner': '0xa'}, {'owner': '0xb'})
    repository.insert_section_diff(
        section_id=section_id,
        previous_section_id=None,
        changes=changes,
        flagged=flagged_changes(section_name, changes),
        threshold_version=version,
    )
    repository.commit()
    return section_id


class TestThresholdsPredateTheirData:
    """A9, against the real store and real version control."""

    def test_the_introducing_commit_predates_the_earliest_scored_diff(
        self, onchain_repository
    ):
        introduced_at = _introducing_commit_date(THRESHOLD_VERSION)
        record_version(onchain_repository, REPO_ROOT)
        _score_one_diff(
            onchain_repository, section_name='contract_safety',
            version=THRESHOLD_VERSION,
        )

        earliest = _earliest_scored_at(onchain_repository, THRESHOLD_VERSION)
        assert earliest is not None
        assert introduced_at < earliest, (
            f'threshold version {THRESHOLD_VERSION} was introduced at '
            f'{introduced_at} but data was scored under it at {earliest}'
        )

    def test_the_same_check_goes_red_when_data_predates_the_version(
        self, onchain_repository
    ):
        """The guard above is evidence only if it can fail. Back-date one stored
        diff to before the introducing commit and run the SAME comparison: it
        must reject. Without this, a check that always passed would be
        indistinguishable from a check that works."""
        introduced_at = _introducing_commit_date(THRESHOLD_VERSION)
        record_version(onchain_repository, REPO_ROOT)
        section_id = _score_one_diff(
            onchain_repository, section_name='identity', version=THRESHOLD_VERSION,
        )
        with onchain_repository.connection.cursor() as cursor:
            cursor.execute(
                'UPDATE onchain.section_diff SET created_at = %s WHERE section_id = %s',
                (introduced_at - timedelta(days=1), section_id),
            )
        onchain_repository.commit()

        earliest = _earliest_scored_at(onchain_repository, THRESHOLD_VERSION)
        assert not (introduced_at < earliest), (
            'the A9 comparison accepted a diff scored before its threshold '
            'version existed'
        )


def test_the_module_carries_no_numeric_threshold_yet(monkeypatch):
    """Version 1 is structural only. A numeric constant appearing here without a
    version bump is the failure P12 exists to prevent, so it is asserted rather
    than trusted to review."""
    table = threshold_table()
    assert 'numeric' not in table
    for fields in table['structural'].values():
        assert all(isinstance(name, str) for name in fields)
    assert thresholds.THRESHOLD_VERSION.startswith('2026-09-06')
