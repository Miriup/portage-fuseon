"""
Git source patching system for manual configuration of git repository sources.

This module provides a virtual filesystem API for overriding git source configuration
for packages that need custom repository URLs or tag patterns.

The patches are stored in the .sys/git-source/ directory:
- .sys/git-source/{package}/_all  - Enable for all versions
- .sys/git-source/{package}/{version}  - Enable for specific version

Patch File Format:
    # Comments start with #
    == git                                    # Auto-detect URL from PyPI metadata
    == git https://github.com/custom/repo.git # Override URL
    == git https://... v{version}             # Override URL + tag pattern
    == wheel                                  # Force wheel (disable git)

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from .constants import get_mount_point_key
from .git_provider import validate_git_url

logger = logging.getLogger(__name__)

# Current patch file format version
PATCH_FILE_VERSION = 1

# Valid source mode values
VALID_SOURCE_MODES = {
    'git',      # Use git source (auto-detect or override URL)
    'wheel',    # Force wheel fallback (disable git)
    'auto',     # Auto-detect best source (default behavior)
}


def is_valid_source_mode(mode: str) -> bool:
    """
    Check if a source mode value is valid.

    Args:
        mode: The source mode to validate

    Returns:
        True if valid, False otherwise

    Examples:
        >>> is_valid_source_mode('git')
        True
        >>> is_valid_source_mode('wheel')
        True
        >>> is_valid_source_mode('auto')
        True
        >>> is_valid_source_mode('invalid')
        False
    """
    return mode in VALID_SOURCE_MODES


@dataclass
class GitSourcePatch:
    """
    Represents a git source override.

    Attributes:
        mode: Source mode ('git', 'wheel', 'auto')
        git_url: Optional override git URL
        tag_pattern: Optional override tag pattern (e.g., 'v{version}', '{version}')
        timestamp: Unix timestamp when patch was created

    Examples:
        >>> patch = GitSourcePatch('git', 'https://github.com/user/repo.git', 'v{version}', 1700000000.0)
        >>> patch.mode
        'git'
        >>> patch.git_url
        'https://github.com/user/repo.git'
    """
    mode: str
    git_url: Optional[str]
    tag_pattern: Optional[str]
    timestamp: float

    def __post_init__(self):
        """Validate the patch."""
        if not is_valid_source_mode(self.mode):
            raise ValueError(f"Invalid source mode: {self.mode}")
        if self.git_url and not validate_git_url(self.git_url):
            raise ValueError(f"Invalid git URL: {self.git_url}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GitSourcePatch':
        """Create from dictionary."""
        return cls(**data)


@dataclass
class PackageGitSourcePatch:
    """
    Git source override for a specific package version.

    Attributes:
        category: Package category (e.g., 'dev-python')
        package: Package name (e.g., 'faster-whisper')
        version: Version string or '_all' for all versions
        patch: The git source patch (or None if not set)

    Examples:
        >>> pp = PackageGitSourcePatch('dev-python', 'faster-whisper', '1.2.1', None)
        >>> pp.category
        'dev-python'
        >>> pp.is_all_versions
        False
        >>> pp_all = PackageGitSourcePatch('dev-python', 'faster-whisper', '_all', None)
        >>> pp_all.is_all_versions
        True
    """
    category: str
    package: str
    version: str  # Version string or '_all' for all versions
    patch: Optional[GitSourcePatch] = None

    @property
    def is_all_versions(self) -> bool:
        """Check if this applies to all versions."""
        return self.version == '_all'

    @property
    def key(self) -> str:
        """Generate unique key for this package/version combination."""
        return f"{self.category}/{self.package}/{self.version}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'category': self.category,
            'package': self.package,
            'version': self.version,
            'patch': self.patch.to_dict() if self.patch else None
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PackageGitSourcePatch':
        """Create from dictionary."""
        patch = GitSourcePatch.from_dict(data['patch']) if data.get('patch') else None
        return cls(
            category=data['category'],
            package=data['package'],
            version=data['version'],
            patch=patch
        )


class GitSourcePatchStore:
    """
    Storage and application of git source patches.

    This class manages patches that override git source configuration for packages,
    persisting them to JSON and applying them during ebuild generation.

    Attributes:
        storage_path: Path to the JSON file storing patches
        patches: Dictionary mapping package keys to PackageGitSourcePatch

    Examples:
        >>> import tempfile
        >>> with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        ...     store = GitSourcePatchStore(f.name)
        >>> store.set_git_source('dev-python', 'faster-whisper', '_all', 'https://github.com/user/repo.git')
        >>> store.get_git_source('dev-python', 'faster-whisper', '1.2.1')
        ('git', 'https://github.com/user/repo.git', None)
        >>> import os; os.unlink(f.name)
    """

    def __init__(self, storage_path: Optional[str] = None, mount_point: Optional[str] = None):
        """
        Initialize the patch store.

        Args:
            storage_path: Path to JSON file for persistence (None for memory-only)
            mount_point: Mount point path for namespaced configuration
        """
        self.storage_path = Path(storage_path) if storage_path else None
        self.mount_point = get_mount_point_key(mount_point) if mount_point else None
        self.patches: Dict[str, PackageGitSourcePatch] = {}
        self._dirty = False

        if self.storage_path and self.storage_path.exists():
            self._load()

    def _load(self) -> None:
        """Load patches from JSON file."""
        if not self.storage_path or not self.storage_path.exists():
            return

        try:
            with self.storage_path.open('r', encoding='utf-8') as f:
                data = json.load(f)

            self.patches = {}

            if 'mount_points' in data:
                # v1+ format: mount_points -> {mount_point -> {git_source_patches: [...]}}
                mp_key = self.mount_point or '_default'
                if mp_key in data['mount_points']:
                    mp_data = data['mount_points'][mp_key]
                    for item in mp_data.get('git_source_patches', []):
                        pp = PackageGitSourcePatch.from_dict(item)
                        self.patches[pp.key] = pp
            else:
                # Legacy format: git_source_patches at top level
                for item in data.get('git_source_patches', []):
                    pp = PackageGitSourcePatch.from_dict(item)
                    self.patches[pp.key] = pp

            logger.info(f"Loaded {len(self.patches)} git source patches from {self.storage_path}"
                       + (f" (mount: {self.mount_point})" if self.mount_point else ""))

        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.error(f"Failed to load git source patches from {self.storage_path}: {e}")
            self.patches = {}

    def save(self) -> bool:
        """
        Save patches to JSON file atomically.

        Returns:
            True if save was successful, False otherwise
        """
        if not self.storage_path:
            return True  # Memory-only mode

        try:
            # Ensure directory exists
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)

            # Load existing data to preserve other sections
            existing_data = {}
            if self.storage_path.exists():
                try:
                    with self.storage_path.open('r', encoding='utf-8') as f:
                        existing_data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass

            existing_data['version'] = existing_data.get('version', PATCH_FILE_VERSION)
            if 'mount_points' not in existing_data:
                existing_data['mount_points'] = {}

            # Update patches for this mount point
            mp_key = self.mount_point or '_default'
            if mp_key not in existing_data['mount_points']:
                existing_data['mount_points'][mp_key] = {}
            existing_data['mount_points'][mp_key]['git_source_patches'] = [
                pp.to_dict() for pp in self.patches.values() if pp.patch is not None
            ]

            # Write to temporary file first
            temp_path = self.storage_path.with_suffix('.tmp')
            with temp_path.open('w', encoding='utf-8') as f:
                json.dump(existing_data, f, indent=2)

            # Atomic rename
            temp_path.rename(self.storage_path)
            self._dirty = False

            logger.debug(f"Saved {len(self.patches)} git source patches to {self.storage_path}")
            return True

        except OSError as e:
            logger.error(f"Failed to save git source patches to {self.storage_path}: {e}")
            return False

    def _get_or_create_patch(self, category: str, package: str, version: str) -> PackageGitSourcePatch:
        """Get or create PackageGitSourcePatch for a package/version."""
        key = f"{category}/{package}/{version}"
        if key not in self.patches:
            self.patches[key] = PackageGitSourcePatch(category, package, version, None)
        return self.patches[key]

    def set_git_source(self, category: str, package: str, version: str,
                       git_url: Optional[str] = None, tag_pattern: Optional[str] = None) -> None:
        """
        Enable git source for a package version.

        Args:
            category: Package category (e.g., 'dev-python')
            package: Package name
            version: Version string or '_all'
            git_url: Optional override git URL (None = auto-detect)
            tag_pattern: Optional override tag pattern

        Examples:
            >>> store = GitSourcePatchStore()
            >>> store.set_git_source('dev-python', 'faster-whisper', '_all')
            >>> store.get_git_source('dev-python', 'faster-whisper', '1.2.1')
            ('git', None, None)
        """
        pp = self._get_or_create_patch(category, package, version)
        pp.patch = GitSourcePatch('git', git_url, tag_pattern, time.time())
        self._dirty = True
        logger.info(f"Set git source for {category}/{package}/{version}"
                   + (f" URL: {git_url}" if git_url else " (auto-detect)"))

    def set_wheel_fallback(self, category: str, package: str, version: str) -> None:
        """
        Force wheel fallback for a package version (disable git).

        Args:
            category: Package category
            package: Package name
            version: Version string or '_all'

        Examples:
            >>> store = GitSourcePatchStore()
            >>> store.set_wheel_fallback('dev-python', 'some-package', '_all')
            >>> mode, _, _ = store.get_git_source('dev-python', 'some-package', '1.0')
            >>> mode
            'wheel'
        """
        pp = self._get_or_create_patch(category, package, version)
        pp.patch = GitSourcePatch('wheel', None, None, time.time())
        self._dirty = True
        logger.info(f"Set wheel fallback for {category}/{package}/{version}")

    def get_git_source(self, category: str, package: str, version: str
                      ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Get the git source configuration for a package version.

        Checks version-specific patches first, then _all patches.

        Args:
            category: Package category
            package: Package name
            version: Version string

        Returns:
            Tuple of (mode, git_url, tag_pattern) if patched, (None, None, None) otherwise

        Examples:
            >>> store = GitSourcePatchStore()
            >>> store.set_git_source('dev-python', 'test', '_all', 'https://github.com/u/r.git')
            >>> store.get_git_source('dev-python', 'test', '1.0')
            ('git', 'https://github.com/u/r.git', None)
            >>> store.get_git_source('dev-python', 'other', '1.0')
            (None, None, None)
        """
        # First check version-specific patch
        ver_key = f"{category}/{package}/{version}"
        if ver_key in self.patches and self.patches[ver_key].patch:
            patch = self.patches[ver_key].patch
            return (patch.mode, patch.git_url, patch.tag_pattern)

        # Then check _all patch
        all_key = f"{category}/{package}/_all"
        if all_key in self.patches and self.patches[all_key].patch:
            patch = self.patches[all_key].patch
            return (patch.mode, patch.git_url, patch.tag_pattern)

        return (None, None, None)

    def should_use_git(self, category: str, package: str, version: str) -> bool:
        """
        Check if git source should be used for a package version.

        Args:
            category: Package category
            package: Package name
            version: Version string

        Returns:
            True if git source is explicitly enabled, False if wheel is forced,
            None if no patch exists (use default behavior)

        Examples:
            >>> store = GitSourcePatchStore()
            >>> store.set_git_source('dev-python', 'test', '_all')
            >>> store.should_use_git('dev-python', 'test', '1.0')
            True
            >>> store.set_wheel_fallback('dev-python', 'other', '_all')
            >>> store.should_use_git('dev-python', 'other', '1.0')
            False
        """
        mode, _, _ = self.get_git_source(category, package, version)
        if mode == 'git':
            return True
        elif mode == 'wheel':
            return False
        return None  # No patch, use default

    def remove_patch(self, category: str, package: str, version: str) -> bool:
        """
        Remove the git source patch for a package version.

        Args:
            category: Package category
            package: Package name
            version: Version string or '_all'

        Returns:
            True if removed, False if not found
        """
        key = f"{category}/{package}/{version}"
        if key in self.patches:
            del self.patches[key]
            self._dirty = True
            logger.info(f"Removed git source patch for {category}/{package}/{version}")
            return True
        return False

    def has_patch(self, category: str, package: str, version: str) -> bool:
        """Check if a patch exists for a package version."""
        mode, _, _ = self.get_git_source(category, package, version)
        return mode is not None

    def get_package_versions_with_patches(self, category: str, package: str) -> List[str]:
        """
        Get all versions that have patches for a package.

        Args:
            category: Package category
            package: Package name

        Returns:
            List of version strings (including '_all' if present)
        """
        prefix = f"{category}/{package}/"
        versions = []
        for key in self.patches:
            if key.startswith(prefix) and self.patches[key].patch:
                version = key[len(prefix):]
                versions.append(version)
        return sorted(versions)

    def generate_patch_file(self, category: str, package: str, version: str) -> str:
        """
        Generate portable patch file content for a package version.

        Args:
            category: Package category
            package: Package name
            version: Version string

        Returns:
            Patch file content as string
        """
        mode, git_url, tag_pattern = self.get_git_source(category, package, version)
        lines = [
            f"# Git source patch for {category}/{package}/{version}",
            f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            ""
        ]

        if mode == 'git':
            if git_url and tag_pattern:
                lines.append(f"== git {git_url} {tag_pattern}")
            elif git_url:
                lines.append(f"== git {git_url}")
            else:
                lines.append("== git")
        elif mode == 'wheel':
            lines.append("== wheel")

        return '\n'.join(lines) + '\n'

    def parse_patch_file(self, content: str, category: str, package: str, version: str) -> int:
        """
        Parse and import patch from patch file content.

        Args:
            content: Patch file content
            category: Target package category
            package: Target package name
            version: Target version

        Returns:
            Number of patches imported (0 or 1)

        Examples:
            >>> store = GitSourcePatchStore()
            >>> content = '''
            ... # Patch
            ... == git https://github.com/user/repo.git
            ... '''
            >>> count = store.parse_patch_file(content, 'dev-python', 'test', '_all')
            >>> count
            1
            >>> mode, url, _ = store.get_git_source('dev-python', 'test', '1.0')
            >>> mode
            'git'
            >>> url
            'https://github.com/user/repo.git'
        """
        count = 0

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            if line.startswith('== '):
                parts = line[3:].strip().split(None, 2)  # Split into up to 3 parts
                if not parts:
                    continue

                mode = parts[0]
                if mode == 'git':
                    git_url = parts[1] if len(parts) > 1 else None
                    tag_pattern = parts[2] if len(parts) > 2 else None
                    self.set_git_source(category, package, version, git_url, tag_pattern)
                    count = 1
                    break
                elif mode == 'wheel':
                    self.set_wheel_fallback(category, package, version)
                    count = 1
                    break
                else:
                    logger.warning(f"Invalid source mode in patch file: {mode}")

        return count

    def list_patched_packages(self) -> List[Tuple[str, str, str]]:
        """
        List all packages that have patches.

        Returns:
            List of (category, package, version) tuples
        """
        result = []
        for key in sorted(self.patches.keys()):
            if self.patches[key].patch:
                parts = key.split('/')
                if len(parts) == 3:
                    result.append((parts[0], parts[1], parts[2]))
        return result

    @property
    def is_dirty(self) -> bool:
        """Check if there are unsaved changes."""
        return self._dirty
