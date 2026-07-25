"""
Two-level JSON cache: in-memory dict over sharded on-disk files.

Every metadata provider needs the same thing — cache a registry's JSON reply in
memory for the life of the process, and on disk across runs, with a TTL on both
tiers. The pattern was independently reimplemented three times
(``pip_metadata.PyPIMetadataExtractor``,
``ecosystems/rubygems/plugin.RubyGemsMetadataProvider``, plus ad-hoc dicts in
both filesystems), so a fourth ecosystem would have written it again.

Design notes worth keeping in mind when using this:

- Disk files are sharded into two-character subdirectories. A flat directory
  with hundreds of thousands of entries is slow to stat on most filesystems.
- Writes go to a temporary file and are then renamed, so a crash or a
  concurrently-reading FUSE thread never observes a half-written document.
- A corrupt or expired file is removed on read rather than left to fail
  repeatedly.
- TTL is enforced against file mtime on disk and against an insertion timestamp
  in memory, so a long-lived FUSE process expires entries the same way a
  short-lived CLI invocation does.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

__all__ = ['JSONCache']


class JSONCache:
    """
    A TTL-bounded, two-level JSON cache.

    Examples:
        >>> import tempfile
        >>> tmp = tempfile.mkdtemp()
        >>> cache = JSONCache(tmp, ttl=3600)
        >>> cache.get('requests') is None
        True
        >>> cache.set('requests', {'name': 'requests'})
        >>> cache.get('requests')
        {'name': 'requests'}

        A second cache over the same directory sees the entry, because it was
        persisted rather than only memoized:

        >>> other = JSONCache(tmp, ttl=3600)
        >>> other.get('requests')
        {'name': 'requests'}

        Keys are namespaced by an optional version, so a package document and a
        specific release document never collide:

        >>> cache.set('requests', {'v': '2.0'}, version='2.0')
        >>> cache.get('requests', version='2.0')
        {'v': '2.0'}
        >>> cache.get('requests')
        {'name': 'requests'}

        A zero TTL expires immediately:

        >>> expired = JSONCache(tmp, ttl=0)
        >>> expired.set('short', {'a': 1})
        >>> expired.get('short') is None
        True
    """

    def __init__(
        self,
        cache_dir: Union[str, Path],
        ttl: int = 3600,
        shard_width: int = 2,
    ):
        """
        Initialize the cache.

        Args:
            cache_dir: Directory holding the on-disk tier. Created if absent.
            ttl: Seconds an entry stays fresh, in both tiers
            shard_width: Number of leading key characters used as the
                subdirectory name
        """
        self.cache_dir = Path(cache_dir)
        self.ttl = ttl
        self.shard_width = shard_width
        self._memory: Dict[str, Tuple[Any, float]] = {}

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(f"Cannot create cache directory {self.cache_dir}: {exc}")

    def make_key(self, name: str, version: Optional[str] = None) -> str:
        """
        Build a cache key from a package name and optional version.

        Args:
            name: Package name; case is folded, since registries are
                case-insensitive about lookups
            version: Optional version, to key a single release document

        Returns:
            Cache key

        Examples:
            >>> import tempfile
            >>> cache = JSONCache(tempfile.mkdtemp())
            >>> cache.make_key('Requests')
            'requests'
            >>> cache.make_key('Requests', '2.0')
            'requests_2.0'
        """
        if version:
            return f"{name.lower()}_{version}"
        return name.lower()

    def path_for(self, key: str) -> Path:
        """
        Return the on-disk path for a key, creating its shard directory.

        Args:
            key: Cache key

        Returns:
            Path to the JSON file backing this key
        """
        shard = key[:self.shard_width] if len(key) >= self.shard_width else '0' * self.shard_width
        # Keys come from package names, which may contain a path separator in
        # scoped-name ecosystems; flatten so the shard stays one level deep.
        shard = shard.replace('/', '_').replace('.', '_')
        shard_dir = self.cache_dir / shard
        try:
            shard_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        safe_key = key.replace('/', '_')
        return shard_dir / f"{safe_key}.json"

    def get(self, name: str, version: Optional[str] = None) -> Optional[Any]:
        """
        Look a document up, memory tier first.

        A disk hit is promoted into memory. Expired or unreadable entries are
        removed as a side effect.

        Args:
            name: Package name
            version: Optional version

        Returns:
            The cached document, or None on a miss
        """
        key = self.make_key(name, version)
        now = time.time()

        cached = self._memory.get(key)
        if cached is not None:
            data, stamp = cached
            if now - stamp < self.ttl:
                return data
            del self._memory[key]

        path = self.path_for(key)
        if not path.exists():
            return None

        try:
            if now - path.stat().st_mtime >= self.ttl:
                path.unlink(missing_ok=True)
                return None
            with path.open('r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            logger.debug(f"Discarding unusable cache entry {key}: {exc}")
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

        self._memory[key] = (data, now)
        return data

    def set(self, name: str, data: Any, version: Optional[str] = None) -> None:
        """
        Store a document in both tiers.

        The disk write is atomic: a temporary file is renamed into place, so a
        concurrent reader sees either the old document or the new one.

        Args:
            name: Package name
            data: JSON-serialisable document
            version: Optional version
        """
        key = self.make_key(name, version)
        self._memory[key] = (data, time.time())

        path = self.path_for(key)
        temp_path = path.with_suffix('.tmp')
        try:
            with temp_path.open('w', encoding='utf-8') as handle:
                json.dump(data, handle)
            temp_path.replace(path)
        except (OSError, TypeError) as exc:
            logger.warning(f"Failed to cache {key}: {exc}")
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def invalidate(self, name: str, version: Optional[str] = None) -> None:
        """
        Drop an entry from both tiers.

        Args:
            name: Package name
            version: Optional version
        """
        key = self.make_key(name, version)
        self._memory.pop(key, None)
        try:
            self.path_for(key).unlink(missing_ok=True)
        except OSError:
            pass

    def clear_memory(self) -> None:
        """Drop the in-memory tier, leaving the on-disk tier intact."""
        self._memory.clear()

    def list_cached(self) -> List[str]:
        """
        List the package names present in the on-disk tier.

        Version-specific entries are excluded, so this reports packages rather
        than individual releases.

        Returns:
            Sorted list of package names
        """
        names = set()
        if not self.cache_dir.exists():
            return []

        for shard in self.cache_dir.iterdir():
            if not shard.is_dir():
                continue
            for entry in shard.glob('*.json'):
                stem = entry.stem
                if '_' in stem:
                    # A version-keyed entry, not a package document.
                    continue
                names.add(stem)

        return sorted(names)
