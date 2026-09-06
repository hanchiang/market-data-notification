"""The frozen threshold table and its version row (requirement P12, criterion A9).

Version 1 is **structural only**: any change to a listed field flags the
section. Numeric thresholds are frozen at phase 1b, when the alerts section
first scores anything against a number -- there is no data to size one against
now, and a number chosen after seeing data would be a new version anyway, which
is the rule this module exists to enforce.

The freeze is real because the version is checked against version control: A9
asks that the commit introducing a version predate the earliest data scored
under it, found with `git log -S`. That is why the build records
`first_seen_commit` (the checkout's HEAD when the version was first scored) and
why the table lives in a tracked Python file rather than a database row.
"""
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger('Onchain thresholds')

THRESHOLD_VERSION = "2026-09-06.1"

# Any change to one of these fields flags the section. Structural facts: a proxy
# that appears, an owner that changes, a mint path that opens, a pool whose key
# no longer matches. Each is a fact about what CAN happen to the token, so a
# change is worth the operator's attention regardless of magnitude -- which is
# exactly why they need no number.
STRUCTURAL: Dict[str, List[str]] = {
    "contract_safety": [
        "proxy",
        "implementation",
        "admin",
        "privileged_selectors",
        "owner",
        "frozen",
        "hook_permissions",
        "verified_source",
    ],
    "identity": [
        "pool_address",
        "pool_id",
        "key_matches",
        "factory_matches",
        "hooks",
    ],
    "token_economics": ["mint_path"],
}


def threshold_table() -> Dict[str, Any]:
    """The serialisable table stored on the version row, so a diff scored months
    ago can be re-read against the table it was actually scored under."""
    return {
        'version': THRESHOLD_VERSION,
        'kind': 'structural-only',
        'structural': {name: list(fields) for name, fields in STRUCTURAL.items()},
    }


def flagged_changes(section_name: str, changes: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The subset of a section's diff that crosses the frozen table.

    Takes the diff's own shape (`added`/`removed`/`changed` from `diff.py`) so
    the flags are derived from the stored diff and not from a second pass over
    the fields: a flag that could disagree with the diff beside it would be
    worse than no flag.
    """
    watched = set(STRUCTURAL.get(section_name, ()))
    if not watched:
        return []

    flagged: List[Dict[str, Any]] = []
    for change in changes.get('changed', []):
        if change.get('field') in watched:
            flagged.append({'field': change['field'], 'reason': 'structural_change'})
    for field_name in _keys(changes.get('added')):
        if field_name in watched:
            flagged.append({'field': field_name, 'reason': 'structural_appeared'})
    for field_name in _keys(changes.get('removed')):
        if field_name in watched:
            flagged.append({'field': field_name, 'reason': 'structural_disappeared'})
    return sorted(flagged, key=lambda entry: (entry['field'], entry['reason']))


def _keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        return list(value)
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def head_commit(repo_root: Optional[Path] = None) -> Optional[str]:
    """The checkout's HEAD, or None when git is unavailable.

    None rather than a raise: a missing commit weakens the A9 evidence for that
    version but must not fail a build that is otherwise fine. The check reports
    an unrecorded commit as unverifiable rather than as a pass.
    """
    root = repo_root or Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning('git is unavailable; threshold version commit not recorded')
        return None
    if result.returncode != 0:
        logger.warning('git rev-parse failed; threshold version commit not recorded')
        return None
    return result.stdout.strip() or None


def record_version(repository: Any, repo_root: Optional[Path] = None) -> None:
    """Write the version row on first sight. First write wins.

    Called by the build before it scores anything, so `first_seen_at` is never
    later than the first `section_diff` written under the version -- the ordering
    A9 checks.
    """
    if repository.get_threshold_version(THRESHOLD_VERSION) is not None:
        return
    repository.record_threshold_version(
        version=THRESHOLD_VERSION,
        first_seen_commit=head_commit(repo_root),
        table=threshold_table(),
    )
