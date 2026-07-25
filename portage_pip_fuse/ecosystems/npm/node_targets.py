"""
Detection of the Node runtime versions available on the system.

The RubyGems and PyPI plugins ask Portage which *implementations* the user has
enabled, via ``RUBY_TARGETS`` and ``PYTHON_TARGETS``. Node has no equivalent:
there is no USE_EXPAND variable, and ``net-libs/nodejs`` is a single slotted
package rather than a family of interpreters. So "compatibility" here means a
different question -- which nodejs versions are actually installed -- and the
answer comes from the installed-package database rather than from make.conf.

Detection order, most authoritative first:

1. the ``NODE_VERSIONS`` environment variable, so a caller can override
   everything (also what the test suite uses);
2. Portage's installed-package database, which is the real answer on a Gentoo
   system;
3. the ``node`` binary on PATH, which is right when running outside Portage,
   for instance in a development checkout;
4. :data:`FALLBACK_NODE_VERSIONS`, so a missing runtime degrades to a permissive
   default instead of hiding every package.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import logging
import os
import re
import subprocess
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    'FALLBACK_NODE_VERSIONS',
    'NodeTargetDetector',
    'get_node_versions',
    'clear_cache',
]

#: Used when nothing can be detected. Deliberately permissive: filtering
#: everything out because the runtime could not be found would be worse than
#: showing a package that turns out to need a newer Node.
FALLBACK_NODE_VERSIONS = ('22.0.0',)

#: How long a detection result stays cached, in seconds. Installed versions do
#: not change during a FUSE mount often enough to justify re-querying Portage on
#: every package listing.
CACHE_TTL = 3600

_VERSION_RE = re.compile(r'v?(\d+)\.(\d+)\.(\d+)')


class NodeTargetDetector:
    """
    Detect available Node versions, with a process-lifetime cache.

    Examples:
        >>> versions = NodeTargetDetector.get_node_versions()
        >>> isinstance(versions, list)
        True
        >>> all(isinstance(v, str) for v in versions)
        True
        >>> len(versions) >= 1
        True
    """

    _cache: dict = {}

    @classmethod
    def get_node_versions(cls) -> List[str]:
        """
        Return the Node versions available on this system, newest first.

        Returns:
            Full ``major.minor.patch`` version strings; never empty

        Examples:
            >>> import os
            >>> os.environ['NODE_VERSIONS'] = '18.0.0 20.1.2'
            >>> NodeTargetDetector.clear_cache()
            >>> NodeTargetDetector.get_node_versions()
            ['20.1.2', '18.0.0']
            >>> del os.environ['NODE_VERSIONS']
            >>> NodeTargetDetector.clear_cache()
        """
        cached = cls._get_cached('node_versions')
        if cached is not None:
            return cached

        for source in (cls._from_environment,
                       cls._from_portage,
                       cls._from_binary):
            versions = source()
            if versions:
                normalised = cls._sort_versions(versions)
                cls._set_cached('node_versions', normalised)
                logger.debug('Detected Node versions %s via %s',
                             normalised, source.__name__)
                return normalised

        logger.debug('No Node runtime detected; falling back to %s',
                     list(FALLBACK_NODE_VERSIONS))
        fallback = list(FALLBACK_NODE_VERSIONS)
        cls._set_cached('node_versions', fallback)
        return fallback

    @classmethod
    def clear_cache(cls) -> None:
        """Discard cached detection results."""
        cls._cache.clear()

    # -- detection sources ----------------------------------------------------

    @classmethod
    def _from_environment(cls) -> Optional[List[str]]:
        """Read an explicit override from ``NODE_VERSIONS``."""
        raw = os.environ.get('NODE_VERSIONS', '')
        if not raw:
            return None
        return [v for v in (cls._normalise(part) for part in raw.split()) if v]

    @classmethod
    def _from_portage(cls) -> Optional[List[str]]:
        """
        Ask Portage which ``net-libs/nodejs`` versions are installed.

        This is the authoritative source on a Gentoo system, and the reason the
        filter reflects what is actually installed rather than what a profile
        would allow.
        """
        try:
            import portage
        except ImportError:
            return None

        try:
            vardb = portage.db[portage.root]['vartree'].dbapi
            matches = vardb.match('net-libs/nodejs')
        except Exception as exc:  # portage raises a wide variety here
            logger.debug('Portage query for net-libs/nodejs failed: %s', exc)
            return None

        versions = []
        for cpv in matches:
            try:
                version = portage.versions.cpv_getversion(cpv)
            except Exception:
                continue
            normalised = cls._normalise(version or '')
            if normalised:
                versions.append(normalised)

        return versions or None

    @classmethod
    def _from_binary(cls) -> Optional[List[str]]:
        """Fall back to whatever ``node`` is on PATH."""
        try:
            result = subprocess.run(
                ['node', '--version'],
                capture_output=True, text=True, timeout=10,
            )
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            return None

        if result.returncode != 0:
            return None

        normalised = cls._normalise(result.stdout.strip())
        return [normalised] if normalised else None

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _normalise(text: str) -> Optional[str]:
        """
        Reduce a version string to bare ``major.minor.patch``.

        Handles the ``v`` prefix ``node --version`` prints and any Gentoo
        revision suffix.

        Examples:
            >>> NodeTargetDetector._normalise('v22.22.2')
            '22.22.2'
            >>> NodeTargetDetector._normalise('20.11.1-r1')
            '20.11.1'
            >>> NodeTargetDetector._normalise('22') is None
            True
        """
        match = _VERSION_RE.search(text or '')
        if match is None:
            return None
        return '%s.%s.%s' % match.groups()

    @staticmethod
    def _sort_versions(versions: List[str]) -> List[str]:
        """Sort newest first, dropping duplicates."""
        def key(version):
            return tuple(int(part) for part in version.split('.'))
        return sorted(set(versions), key=key, reverse=True)

    @classmethod
    def _get_cached(cls, key: str) -> Optional[List[str]]:
        entry = cls._cache.get(key)
        if entry is None:
            return None
        value, stamp = entry
        if time.time() - stamp >= CACHE_TTL:
            del cls._cache[key]
            return None
        return value

    @classmethod
    def _set_cached(cls, key: str, value: List[str]) -> None:
        cls._cache[key] = (value, time.time())


def get_node_versions() -> List[str]:
    """
    Module-level shorthand for :meth:`NodeTargetDetector.get_node_versions`.

    Examples:
        >>> isinstance(get_node_versions(), list)
        True
    """
    return NodeTargetDetector.get_node_versions()


def clear_cache() -> None:
    """Module-level shorthand for :meth:`NodeTargetDetector.clear_cache`."""
    NodeTargetDetector.clear_cache()
