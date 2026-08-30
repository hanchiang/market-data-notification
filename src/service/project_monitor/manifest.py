"""R9: snapshot the dapp's build-time manifest each run, and diff it.

This is in slice 1 rather than a later one for a reason that has nothing to do
with its size: a bundle is overwritten on every deploy with no archive, so a
snapshot not taken is unrecoverable -- unlike chain history, which the archive
endpoint now serves.

**Extraction is by string content, never by minified identifier.** The core
registry lives under a two-letter name (`Jc` at the 2026-08-29 capture) that
changes on every rebuild, and identifier-keyed extraction rots silently:
returning nothing reads exactly like a quiet week. Recipes and provenance:
MARKET-DATA/docs/traces/2026-08-29-netnet-dapp-crawl-evidence.md, Fact 3.
"""
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import aiohttp

logger = logging.getLogger('Project monitor manifest')

BROWSER_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)

SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="([^"]+\.js)"')
BUILD_HASH_RE = re.compile(r'build\s*"?\s*,?\s*"?([0-9a-f]{7,40})"?')
# The 62-entry labelled registry: `name:{address:"0x..."}`.
LABELLED_RE = re.compile(r'([A-Za-z0-9_$]+)\s*:\s*\{\s*address\s*:\s*"(0x[0-9a-fA-F]{40})"')
# The core set, wrapped in a minified helper: `name:xx("0x...")`.
CORE_ENTRY_RE = re.compile(r'([A-Za-z0-9_$]+)\s*:\s*[A-Za-z0-9_$]+\("(0x[0-9a-fA-F]{40})"\)')

CORE_ANCHOR = 'bondDepository:'
PLACEHOLDER_ADDRESS = '0x' + '0' * 40


class ManifestExtractionError(RuntimeError):
    """A recipe yielded nothing where it must yield at least one name."""


@dataclass(frozen=True)
class ManifestSnapshot:
    bundle_filename: str
    build_hash: Optional[str]
    bundle_sha256: str
    registry: Dict[str, str]
    core_count: int
    labelled_count: int


@dataclass
class ManifestDiffs:
    added: List[Dict[str, str]] = field(default_factory=list)
    removed: List[Dict[str, str]] = field(default_factory=list)
    placeholder_filled: List[Dict[str, str]] = field(default_factory=list)
    changed: List[Dict[str, str]] = field(default_factory=list)

    def as_rows(self) -> List[Dict[str, Optional[str]]]:
        rows: List[Dict[str, Optional[str]]] = []
        for kind, entries in (
            ('added', self.added),
            ('removed', self.removed),
            ('placeholder_filled', self.placeholder_filled),
            ('changed', self.changed),
        ):
            for entry in entries:
                rows.append(
                    {
                        'kind': kind,
                        'name': entry['name'],
                        'old_address': entry.get('old_address'),
                        'new_address': entry.get('new_address'),
                    }
                )
        return rows

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.placeholder_filled or self.changed)


def extract_core_registry(bundle: str) -> Dict[str, str]:
    """The core contract set, found by the distinctive `bondDepository:` literal.

    The window around the anchor is generous because the object's field order is
    not guaranteed across rebuilds -- `bondDepository` sits in the middle at the
    2026-08-29 and 2026-08-30 builds, but a reordering must not truncate the set.
    """
    index = bundle.find(CORE_ANCHOR)
    if index < 0:
        raise ManifestExtractionError(
            f'core registry anchor {CORE_ANCHOR!r} not found in the bundle'
        )
    window = bundle[max(0, index - 2000) : index + 3000]
    return dict(CORE_ENTRY_RE.findall(window))


def extract_labelled_registry(bundle: str) -> Dict[str, str]:
    return dict(LABELLED_RE.findall(bundle))


def extract_registry(bundle: str) -> Tuple[Dict[str, str], int, int]:
    """Both recipes, **each checked non-empty on its own**.

    A single "zero addresses" check would let one recipe rot behind the other:
    the labelled recipe yields 60-odd names, so it would mask a core recipe that
    had stopped matching entirely. There is deliberately no higher floor -- a
    fixed "at least 15" would turn the project retiring one core contract into a
    permanent alarm, which is what the `removed` diff exists to report instead.
    """
    core = extract_core_registry(bundle)
    if not core:
        raise ManifestExtractionError('core registry recipe yielded no addresses')
    labelled = extract_labelled_registry(bundle)
    if not labelled:
        raise ManifestExtractionError('labelled registry recipe yielded no addresses')

    merged = dict(labelled)
    merged.update(core)  # the core set wins where a name appears in both
    return merged, len(core), len(labelled)


async def fetch_manifest(
    page_url: str, *, session: Optional[aiohttp.ClientSession] = None
) -> ManifestSnapshot:
    """GET the page, find its bundle, GET that, extract and hash.

    Both hashes are stored: the footer build hash is what the evidence trace
    keys expiry on, and the bundle's sha256 is what proves two fetches of the
    same filename returned the same bytes.
    """
    owns_session = session is None
    session = session or aiohttp.ClientSession(
        headers={'User-Agent': BROWSER_USER_AGENT},
        timeout=aiohttp.ClientTimeout(total=60),
    )
    try:
        async with session.get(page_url) as response:
            response.raise_for_status()
            html = await response.text()

        sources = SCRIPT_SRC_RE.findall(html)
        if not sources:
            raise ManifestExtractionError('no script src found on the manifest page')
        bundle_url = urljoin(page_url, sources[0])

        async with session.get(bundle_url) as response:
            response.raise_for_status()
            body = await response.read()
    finally:
        if owns_session:
            await session.close()

    bundle = body.decode('utf-8', 'replace')
    registry, core_count, labelled_count = extract_registry(bundle)

    # The build hash is rendered by the footer component, so it lives in the
    # bundle rather than in the served HTML shell.
    build_match = BUILD_HASH_RE.search(bundle) or BUILD_HASH_RE.search(html)
    return ManifestSnapshot(
        bundle_filename=bundle_url.rsplit('/', 1)[-1],
        build_hash=build_match.group(1) if build_match else None,
        bundle_sha256=hashlib.sha256(body).hexdigest(),
        registry=registry,
        core_count=core_count,
        labelled_count=labelled_count,
    )


def diff_registries(
    previous: Optional[Dict[str, str]], current: Dict[str, str]
) -> ManifestDiffs:
    """Four kinds. `removed` exists because three could not report a vanished
    registry, and a registry that quietly stops listing a contract is exactly
    the change worth seeing."""
    diffs = ManifestDiffs()
    if previous is None:
        return diffs

    for name, address in current.items():
        if name not in previous:
            diffs.added.append({'name': name, 'new_address': address})
            continue
        old = previous[name]
        if old.lower() == address.lower():
            continue
        if old.lower() == PLACEHOLDER_ADDRESS:
            diffs.placeholder_filled.append(
                {'name': name, 'old_address': old, 'new_address': address}
            )
        else:
            diffs.changed.append(
                {'name': name, 'old_address': old, 'new_address': address}
            )

    for name, address in previous.items():
        if name not in current:
            diffs.removed.append({'name': name, 'old_address': address})

    return diffs


def is_extractor_verified(
    previous_snapshot: Optional[Dict[str, object]], current: ManifestSnapshot
) -> bool:
    """AC8's evidence: the bundle changed and the extracted registry did not.

    That combination is the only thing that distinguishes "the extractor still
    works" from "nothing has been deployed since the last snapshot" -- without a
    rebuild in between, an unchanged registry proves nothing about the recipes.
    """
    if previous_snapshot is None:
        return False
    bundle_changed = (
        previous_snapshot.get('bundle_sha256') != current.bundle_sha256
        or previous_snapshot.get('build_hash') != current.build_hash
    )
    registry_unchanged = previous_snapshot.get('registry_json') == current.registry
    return bool(bundle_changed and registry_unchanged)
