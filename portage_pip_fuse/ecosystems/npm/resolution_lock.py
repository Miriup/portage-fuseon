"""
Dependency-pin locking for npm ebuild generation.

Ebuild generation resolves each dependency range to the highest published
version that satisfies it *at the moment of generation*. That makes the overlay
non-deterministic over time, and the consequences are not cosmetic:

- A package publishes a new version. On the next mount, a consumer that pinned
  ``~dev-nodejs/chalk-4.1.2`` now pins ``4.1.3``, so ``emerge -uDN`` wants to
  change a dependency the user never touched.
- Because the store is version-keyed and ``SLOT="${PV}"``, the previously
  installed version is not replaced -- it is a different slot. Repeated remounts
  therefore *accumulate* dependency versions rather than upgrading them.
- An installed package's recorded dependencies stop matching what the overlay
  now says it needs, which is the "pin drift" risk the design flagged.

This store records the version chosen for each dependency the first time it is
resolved, and returns it thereafter, so a mounted overlay keeps saying the same
thing until the lock is deliberately cleared.

Locks are keyed by ``(category, package, version)`` and persist in the shared
``patches.json`` alongside the other patch stores, namespaced by mount point.
Modelled on :mod:`portage_pip_fuse.slot_patch`; the difference is that a slot
override is one string per package version, whereas a lock is a mapping of
dependency name to version.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from portage_pip_fuse.constants import get_mount_point_key

logger = logging.getLogger(__name__)

__all__ = ['PATCH_FILE_VERSION', 'STORAGE_KEY', 'ResolutionLockStore']

#: Shared patch-file format version, matching the other stores.
PATCH_FILE_VERSION = 3

#: Section name within a mount point's entry in patches.json.
STORAGE_KEY = 'npm_resolution_locks'


class ResolutionLockStore:
    """
    Record and replay the dependency versions chosen for a package version.

    Examples:
        >>> store = ResolutionLockStore()
        >>> store.get('dev-nodejs', 'chalk', '4.1.2') is None
        True

        Recording a pin makes it stick:

        >>> store.record('dev-nodejs', 'chalk', '4.1.2', 'ansi-styles', '4.3.0')
        '4.3.0'
        >>> store.get_pin('dev-nodejs', 'chalk', '4.1.2', 'ansi-styles')
        '4.3.0'

        A second resolution returns the locked version, not the newly offered
        one. This is the whole point: without it, a remount would silently
        change the ebuild's RDEPEND.

        >>> store.record('dev-nodejs', 'chalk', '4.1.2', 'ansi-styles', '4.9.9')
        '4.3.0'

        Locks are per package version, so a different version resolves freely:

        >>> store.record('dev-nodejs', 'chalk', '5.0.0', 'ansi-styles', '6.0.0')
        '6.0.0'
        >>> store.get_pin('dev-nodejs', 'chalk', '4.1.2', 'ansi-styles')
        '4.3.0'
    """

    def __init__(
        self,
        storage_path: Optional[str] = None,
        mount_point: Optional[str] = None,
    ):
        """
        Args:
            storage_path: JSON file for persistence; None keeps locks in memory
                only, which is the right choice for a one-shot CLI invocation
            mount_point: Mount point the locks belong to, so two overlays mounted
                from one config do not share pins
        """
        self.storage_path = Path(storage_path) if storage_path else None
        self.mount_point = get_mount_point_key(mount_point) if mount_point else None
        #: "category/package/version" -> {dependency name: version}
        self.locks: Dict[str, Dict[str, str]] = {}
        self._dirty = False

        if self.storage_path and self.storage_path.exists():
            self._load()

    # -- persistence ----------------------------------------------------------

    @staticmethod
    def _key(category: str, package: str, version: str) -> str:
        """Build the storage key for a package version."""
        return '%s/%s/%s' % (category, package, version)

    def _load(self) -> None:
        """Load locks from the shared patch file."""
        if not self.storage_path or not self.storage_path.exists():
            return

        try:
            with self.storage_path.open('r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error('Failed to load resolution locks from %s: %s',
                         self.storage_path, exc)
            self.locks = {}
            return

        self.locks = {}
        mount_key = self.mount_point or '_default'
        entry = (data.get('mount_points') or {}).get(mount_key) or {}
        stored = entry.get(STORAGE_KEY) or {}

        # Reject anything not shaped like a lock rather than letting a malformed
        # entry surface as a bogus pin later.
        for key, pins in stored.items():
            if isinstance(pins, dict) and all(
                    isinstance(name, str) and isinstance(version, str)
                    for name, version in pins.items()):
                self.locks[key] = dict(pins)
            else:
                logger.warning('Ignoring malformed resolution lock for %s', key)

        logger.info('Loaded resolution locks for %d package version(s) from %s',
                    len(self.locks), self.storage_path)

    def save(self) -> bool:
        """
        Write locks to the shared patch file, preserving other sections.

        Returns:
            True on success, or when running memory-only
        """
        if not self.storage_path:
            return True

        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)

            existing: Dict = {}
            if self.storage_path.exists():
                try:
                    with self.storage_path.open('r', encoding='utf-8') as handle:
                        existing = json.load(handle)
                except (json.JSONDecodeError, OSError):
                    # A corrupt file must not cost the caller its locks, but it
                    # also must not silently discard other stores' sections, so
                    # start fresh and say so.
                    logger.warning('Rewriting unreadable %s', self.storage_path)
                    existing = {}

            existing['version'] = max(existing.get('version', 1),
                                      PATCH_FILE_VERSION)
            existing.setdefault('mount_points', {})

            mount_key = self.mount_point or '_default'
            existing['mount_points'].setdefault(mount_key, {})
            existing['mount_points'][mount_key][STORAGE_KEY] = self.locks

            temp_path = self.storage_path.with_suffix('.tmp')
            with temp_path.open('w', encoding='utf-8') as handle:
                json.dump(existing, handle, indent=2, sort_keys=True)
            temp_path.replace(self.storage_path)

            self._dirty = False
            logger.debug('Saved resolution locks for %d package version(s)',
                         len(self.locks))
            return True

        except OSError as exc:
            logger.error('Failed to save resolution locks to %s: %s',
                         self.storage_path, exc)
            return False

    @staticmethod
    def list_mount_points(storage_path: str) -> List[str]:
        """
        List the mount-point namespaces a lock file contains.

        Locks are namespaced by mount point, so a tool inspecting them has to
        know which namespaces exist rather than assuming the default. Without
        this, locks written by a mount at a custom path are invisible.

        Args:
            storage_path: Path to the shared patch file

        Returns:
            Sorted namespace keys that hold locks
        """
        path = Path(storage_path)
        if not path.exists():
            return []

        try:
            with path.open('r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return []

        return sorted(
            key for key, entry in (data.get('mount_points') or {}).items()
            if isinstance(entry, dict) and entry.get(STORAGE_KEY)
        )

    # -- queries --------------------------------------------------------------

    def get(
        self,
        category: str,
        package: str,
        version: str,
    ) -> Optional[Dict[str, str]]:
        """
        Get every locked pin for a package version.

        Args:
            category: Gentoo category
            package: Gentoo package name
            version: PMS version

        Returns:
            A copy of the pins, or None when nothing is locked
        """
        pins = self.locks.get(self._key(category, package, version))
        return dict(pins) if pins else None

    def get_pin(
        self,
        category: str,
        package: str,
        version: str,
        dependency: str,
    ) -> Optional[str]:
        """
        Get the locked version for one dependency.

        Args:
            category: Gentoo category
            package: Gentoo package name
            version: PMS version
            dependency: npm dependency name

        Returns:
            The locked upstream version, or None if not locked
        """
        pins = self.locks.get(self._key(category, package, version)) or {}
        return pins.get(dependency)

    def has_lock(self, category: str, package: str, version: str) -> bool:
        """Report whether any pin is locked for a package version."""
        return bool(self.locks.get(self._key(category, package, version)))

    # -- mutation -------------------------------------------------------------

    def record(
        self,
        category: str,
        package: str,
        version: str,
        dependency: str,
        resolved: str,
    ) -> str:
        """
        Lock a dependency's version, or return the version already locked.

        This is the hook ebuild generation calls. It is deliberately
        first-write-wins rather than last-write-wins: the point is that a
        resolution performed today keeps its answer tomorrow, when a newer
        version has been published.

        Args:
            category: Gentoo category
            package: Gentoo package name
            version: PMS version of the package being generated
            dependency: npm dependency name
            resolved: The version just resolved, used only if nothing is locked

        Returns:
            The version to use, locked or newly recorded

        Examples:
            >>> store = ResolutionLockStore()
            >>> store.record('dev-nodejs', 'p', '1.0.0', 'dep', '2.0.0')
            '2.0.0'
            >>> store.record('dev-nodejs', 'p', '1.0.0', 'dep', '3.0.0')
            '2.0.0'
        """
        key = self._key(category, package, version)
        pins = self.locks.setdefault(key, {})

        existing = pins.get(dependency)
        if existing is not None:
            if existing != resolved:
                logger.debug('%s-%s: keeping locked %s@%s rather than %s',
                             package, version, dependency, existing, resolved)
            return existing

        pins[dependency] = resolved
        self._dirty = True
        return resolved

    def set(
        self,
        category: str,
        package: str,
        version: str,
        pins: Dict[str, str],
    ) -> None:
        """
        Replace every pin for a package version.

        Args:
            category: Gentoo category
            package: Gentoo package name
            version: PMS version
            pins: Mapping of npm dependency name to upstream version

        Raises:
            ValueError: if a name or version is not a string
        """
        for name, pinned in pins.items():
            if not isinstance(name, str) or not isinstance(pinned, str):
                raise ValueError('pins must map string names to string versions')

        key = self._key(category, package, version)
        if pins:
            self.locks[key] = dict(pins)
        else:
            self.locks.pop(key, None)
        self._dirty = True

    def remove(self, category: str, package: str, version: str) -> bool:
        """
        Clear the locks for a package version, letting it resolve afresh.

        Returns:
            True if anything was removed
        """
        key = self._key(category, package, version)
        if key in self.locks:
            del self.locks[key]
            self._dirty = True
            return True
        return False

    def remove_package(self, category: str, package: str) -> int:
        """
        Clear locks for every version of a package.

        Returns:
            The number of package versions unlocked
        """
        prefix = '%s/%s/' % (category, package)
        keys = [key for key in self.locks if key.startswith(prefix)]
        for key in keys:
            del self.locks[key]
        if keys:
            self._dirty = True
        return len(keys)

    def clear(self) -> int:
        """
        Clear every lock.

        Returns:
            The number of package versions unlocked
        """
        count = len(self.locks)
        self.locks.clear()
        if count:
            self._dirty = True
        return count

    # -- listing --------------------------------------------------------------

    def list_categories(self) -> Set[str]:
        """List categories that have locks."""
        return {key.split('/')[0] for key in self.locks if '/' in key}

    def list_packages(self, category: str) -> Set[str]:
        """List packages with locks in a category."""
        packages = set()
        for key in self.locks:
            parts = key.split('/')
            if len(parts) == 3 and parts[0] == category:
                packages.add(parts[1])
        return packages

    def list_versions(self, category: str, package: str) -> Set[str]:
        """List locked versions of a package."""
        versions = set()
        for key in self.locks:
            parts = key.split('/')
            if len(parts) == 3 and parts[0] == category and parts[1] == package:
                versions.add(parts[2])
        return versions

    def list_all_locks(self) -> List[Tuple[str, str, str, Dict[str, str]]]:
        """
        List every lock as ``(category, package, version, pins)``.

        Returns:
            Sorted list, for diagnostics and the debug command
        """
        result = []
        for key, pins in sorted(self.locks.items()):
            parts = key.split('/')
            if len(parts) == 3:
                result.append((parts[0], parts[1], parts[2], dict(pins)))
        return result

    # -- text format ----------------------------------------------------------

    def generate_patch_content(
        self,
        category: str,
        package: str,
        version: str,
    ) -> str:
        """
        Render a package version's locks as editable text.

        The format the ``.sys/resolution-lock/`` control surface reads and
        writes: one ``name version`` pair per line.

        Args:
            category: Gentoo category
            package: Gentoo package name
            version: PMS version

        Returns:
            Text representation, empty-but-commented when nothing is locked

        Examples:
            >>> store = ResolutionLockStore()
            >>> store.set('dev-nodejs', 'chalk', '4.1.2',
            ...           {'ansi-styles': '4.3.0', '@types/node': '20.1.0'})
            >>> print(store.generate_patch_content('dev-nodejs', 'chalk', '4.1.2'))
            # Resolution locks for dev-nodejs/chalk-4.1.2
            # One "<npm name> <version>" per line. Delete a line to let that
            # dependency resolve to the newest matching version again.
            @types/node 20.1.0
            ansi-styles 4.3.0
            <BLANKLINE>
        """
        lines = [
            '# Resolution locks for %s/%s-%s' % (category, package, version),
            '# One "<npm name> <version>" per line. Delete a line to let that',
            '# dependency resolve to the newest matching version again.',
        ]

        pins = self.locks.get(self._key(category, package, version)) or {}
        for name in sorted(pins):
            lines.append('%s %s' % (name, pins[name]))

        return '\n'.join(lines) + '\n'

    @staticmethod
    def parse_patch_content(content: str) -> Optional[Dict[str, str]]:
        """
        Parse locks from the text format.

        Args:
            content: Text as produced by :meth:`generate_patch_content`

        Returns:
            Parsed pins, or None if any non-comment line is malformed. Failing
            the whole parse is deliberate: applying half of an edited lock file
            would leave the rest resolving freely, which is exactly the drift
            this store exists to prevent.

        Examples:
            >>> parse = ResolutionLockStore.parse_patch_content
            >>> parse('ansi-styles 4.3.0\\n@types/node 20.1.0\\n') == {
            ...     'ansi-styles': '4.3.0', '@types/node': '20.1.0'}
            True

            Comments and blank lines are ignored:

            >>> parse('# a comment\\n\\nchalk 4.1.2\\n')
            {'chalk': '4.1.2'}

            A leading '==' is tolerated, for consistency with the other
            .sys control formats:

            >>> parse('== chalk 4.1.2\\n')
            {'chalk': '4.1.2'}

            An empty file clears every lock:

            >>> parse('# only comments\\n')
            {}

            A malformed line rejects the whole file:

            >>> parse('chalk\\n') is None
            True
            >>> parse('chalk 1.0.0 extra\\n') is None
            True
        """
        pins: Dict[str, str] = {}

        for raw_line in (content or '').splitlines():
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue

            if line.startswith('=='):
                line = line[2:].strip()

            parts = line.split()
            if len(parts) != 2:
                logger.warning('Malformed resolution lock line: %r', raw_line)
                return None

            pins[parts[0]] = parts[1]

        return pins

    @property
    def is_dirty(self) -> bool:
        """Report whether there are unsaved changes."""
        return self._dirty
