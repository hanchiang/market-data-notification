"""Field-level diff of two section builds (requirement V3, P4, criterion A2).

"Change, not just state": a section that only shows a current value is not
finished, so every build is diffed against the previous one and the diff is what
the operator reads.

Three rules make the diff mean something:

* **A `failed` field keeps its previous value as the baseline and is left out of
  the diff** (A3). A collector that could not read a field must not make the
  field look like it changed to nothing -- that is a fetch failure becoming a
  finding, which the requirement forbids outright. The baseline has to survive
  the *storage* round trip too, which is why a failure marker carries it: a
  section stored as `partial` holds the marker, so the night after an outage
  would otherwise diff against a document with no value for that field and
  report a structural change as an appearance. `with_baselines` is what a
  builder calls before writing a section, and it chains across consecutive
  outages because each marker inherits the previous one's baseline.
* **Address lists are compared as sets.** Providers and log queries return
  addresses in whatever order they happen to; ordering is not a change, and a
  diff that said so every night would train the operator to ignore it.
* **Unchanged inputs yield an empty change list**, with no exceptions for
  formatting: `fields_json` is canonical (sorted keys, big integers as strings)
  precisely so that two equal readings compare equal.
"""
import math
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

# A field whose value is a mapping carrying this state is one the collector could
# not read. The error class travels with it.
FAILED_STATE = 'failed'

# Distinct from None, which is a legitimate field value: `resolve` has to be
# able to say "this field has no value at all" without claiming it is null.
_ABSENT = object()

# `0X` as well as `0x`: the prefix's case is not load-bearing anywhere, and
# a list that missed the check would silently become order-sensitive.
_ADDRESS = re.compile(r'^0[xX][0-9a-fA-F]{40}$')

# Only a decimal integer or fixed-point string is a number here. `float()`
# also accepts 'inf', 'Infinity' and 'nan', and a delta of NaN is rejected by
# Postgres `jsonb` -- which would abort the build transaction mid-write, on a
# token that merely happens to be named "Infinity" (round-1 finding P3-1).
_DECIMAL = re.compile(r'^-?\d+(\.\d+)?$')


def is_failed(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get('state') == FAILED_STATE


def failed_field(error_class: str, baseline: Any = _ABSENT) -> Dict[str, Any]:
    """The marker a collector stores for a field it could not read.

    `baseline` is the last good value, carried so the field's history survives
    the outage. Omit it only when there is no previous value at all.
    """
    marker: Dict[str, Any] = {'state': FAILED_STATE, 'error_class': error_class}
    if baseline is not _ABSENT:
        marker['baseline'] = baseline
    return marker


def resolve(value: Any) -> Any:
    """A failure marker's kept baseline, or the value itself.

    Returns `_ABSENT` for a marker with no baseline, which is the "failed and
    never read successfully" case -- distinct from a field whose value is None.
    """
    if not is_failed(value):
        return value
    if 'baseline' not in value:
        return _ABSENT
    # Recursive because a marker written before `with_baselines` existed, or by a
    # caller that stored a marker as a baseline, must still resolve to a value.
    return resolve(value['baseline'])


def with_baselines(
    current: Mapping[str, Any], previous: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    """The document to STORE: each failed field's marker carries the last good
    value from the previous section.

    Called by the builder before `insert_section`. Without it a `partial`
    section's stored fields hold only failure markers, and the next build has
    nothing to diff that field against -- an outage would turn the next real
    change into an `added`, which the frozen table then flags as
    `structural_appeared` rather than as the change it is.
    """
    previous = previous or {}
    stamped: Dict[str, Any] = {}
    for name, value in current.items():
        if not is_failed(value) or 'baseline' in value:
            stamped[name] = value
            continue
        kept = resolve(previous.get(name, _ABSENT))
        stamped[name] = (
            dict(value) if kept is _ABSENT
            else failed_field(value.get('error_class', 'unknown'), kept)
        )
    return stamped


def effective_fields(
    current: Mapping[str, Any], previous: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    """The document a build is diffed on: current values, with each `failed`
    field replaced by the last good value -- its own carried baseline first, then
    the previous section's.

    A failed field with no baseline anywhere stays failed: there is nothing to
    keep, and dropping it would make the field silently vanish from the section.
    """
    previous = previous or {}
    resolved: Dict[str, Any] = {}
    for name, value in current.items():
        if not is_failed(value):
            resolved[name] = value
            continue
        kept = resolve(value)
        if kept is _ABSENT:
            kept = resolve(previous.get(name, _ABSENT))
        resolved[name] = value if kept is _ABSENT else kept
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
    # The previous document is resolved FIRST: a `partial` section stores failure
    # markers, and comparing against the marker rather than against the value it
    # carries is what turned a True->False change into an appearance (round-1
    # finding P2-2).
    baseline = {
        name: resolve(value) for name, value in (previous or {}).items()
    }
    baseline = {
        name: value for name, value in baseline.items() if value is not _ABSENT
    }
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
    delta = new_number - old_number
    # Belt and braces: a subtraction of two finite floats can still overflow to
    # infinity, and only a finite number survives `json.dumps` into `jsonb`.
    return delta if math.isfinite(delta) else None


def _as_number(value: Any) -> Optional[float | int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    if isinstance(value, str) and _DECIMAL.match(value):
        # int first: a chain amount is an exact integer far beyond float64, and
        # parsing it as a float would silently round the delta.
        try:
            return int(value)
        except ValueError:
            return float(value)
    return None


def summarise(changes: Mapping[str, Any]) -> Tuple[int, int, int]:
    """(added, removed, changed) counts, for a build row or a report line."""
    return (
        len(changes.get('added') or {}),
        len(changes.get('removed') or {}),
        len(changes.get('changed') or []),
    )
