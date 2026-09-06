"""Field-level diff of two section builds (requirement V3, P4, criterion A2).

"Change, not just state": a section that only shows a current value is not
finished, so every build is diffed against the previous one and the diff is what
the operator reads.

Three rules make the diff mean something:

* **A `failed` field keeps its previous value as the baseline and is left out of
  the diff** (A3). A collector that could not read a field must not make the
  field look like it changed to nothing -- that is a fetch failure becoming a
  finding, which the requirement forbids outright.
* **Address lists are compared as sets.** Providers and log queries return
  addresses in whatever order they happen to; ordering is not a change, and a
  diff that said so every night would train the operator to ignore it.
* **Unchanged inputs yield an empty change list**, with no exceptions for
  formatting: `fields_json` is canonical (sorted keys, big integers as strings)
  precisely so that two equal readings compare equal.
"""
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

# A field whose value is a mapping carrying this state is one the collector could
# not read. The error class travels with it.
FAILED_STATE = 'failed'

# `0X` as well as `0x`: the prefix's case is not load-bearing anywhere, and
# a list that missed the check would silently become order-sensitive.
_ADDRESS = re.compile(r'^0[xX][0-9a-fA-F]{40}$')


def is_failed(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get('state') == FAILED_STATE


def effective_fields(
    current: Mapping[str, Any], previous: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    """The document a build is diffed on: current values, with each `failed`
    field replaced by the previous build's value for that field.

    A failed field with no previous value stays failed -- there is no baseline to
    keep, and dropping it would make the field silently vanish from the section.
    """
    previous = previous or {}
    resolved: Dict[str, Any] = {}
    for name, value in current.items():
        if is_failed(value) and name in previous and not is_failed(previous[name]):
            resolved[name] = previous[name]
        else:
            resolved[name] = value
    return resolved


def diff_fields(
    previous: Optional[Mapping[str, Any]], current: Mapping[str, Any]
) -> Dict[str, Any]:
    """Compare two `fields_json` documents.

    Returns `{'added': {...}, 'removed': {...}, 'changed': [...]}`. A `changed`
    entry is `{'field', 'old', 'new'}` plus `'delta'` for a numeric pair and
    `'added_members'`/`'removed_members'` for an address list.

    With no previous section, every field is `added` -- a first build has no
    diff to report, and reporting the whole section as new is the honest form of
    that.
    """
    baseline = dict(previous or {})
    resolved = effective_fields(current, baseline)

    # A field that is failed in BOTH builds has no comparable value on either
    # side, so it is neither a change nor an absence -- it is excluded, and the
    # section's own `partial` status is what records that it failed.
    comparable = {
        name: value for name, value in resolved.items() if not is_failed(value)
    }
    baseline_comparable = {
        name: value for name, value in baseline.items() if not is_failed(value)
    }

    added = {
        name: value
        for name, value in comparable.items()
        if name not in baseline_comparable
    }
    removed = {
        name: value
        for name, value in baseline_comparable.items()
        if name not in comparable
    }

    changed: List[Dict[str, Any]] = []
    for name in sorted(set(comparable) & set(baseline_comparable)):
        old = baseline_comparable[name]
        new = comparable[name]
        entry = _compare(name, old, new)
        if entry is not None:
            changed.append(entry)

    return {
        'added': dict(sorted(added.items())),
        'removed': dict(sorted(removed.items())),
        'changed': changed,
    }


def is_empty(changes: Mapping[str, Any]) -> bool:
    return not (changes.get('added') or changes.get('removed') or changes.get('changed'))


def _compare(name: str, old: Any, new: Any) -> Optional[Dict[str, Any]]:
    if _is_address_list(old) and _is_address_list(new):
        return _compare_address_lists(name, old, new)
    if old == new:
        return None
    entry: Dict[str, Any] = {'field': name, 'old': old, 'new': new}
    delta = _numeric_delta(old, new)
    if delta is not None:
        entry['delta'] = delta
    return entry


def _compare_address_lists(
    name: str, old: List[Any], new: List[Any]
) -> Optional[Dict[str, Any]]:
    old_set = {str(a).lower() for a in old}
    new_set = {str(a).lower() for a in new}
    if old_set == new_set:
        return None
    return {
        'field': name,
        'old': old,
        'new': new,
        'added_members': sorted(new_set - old_set),
        'removed_members': sorted(old_set - new_set),
    }


def _is_address_list(value: Any) -> bool:
    """Only a list of 20-byte hex addresses is order-insensitive.

    Deliberately narrow: an arbitrary list of strings can be an ordered ranking
    (the top-ten holders), where a reordering IS the change worth reporting.
    """
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(item, str) and _ADDRESS.match(item) for item in value)
    )


def _numeric_delta(old: Any, new: Any) -> Optional[float | int]:
    """`new - old` when both sides are numbers, including the decimal STRINGS
    that carry chain amounts too large for a JSON number."""
    old_number = _as_number(old)
    new_number = _as_number(new)
    if old_number is None or new_number is None:
        return None
    return new_number - old_number


def _as_number(value: Any) -> Optional[float | int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        for parse in (int, float):
            try:
                return parse(value)
            except ValueError:
                continue
    return None


def summarise(changes: Mapping[str, Any]) -> Tuple[int, int, int]:
    """(added, removed, changed) counts, for a build row or a report line."""
    return (
        len(changes.get('added') or {}),
        len(changes.get('removed') or {}),
        len(changes.get('changed') or []),
    )
