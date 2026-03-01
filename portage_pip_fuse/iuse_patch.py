"""
IUSE patching system for runtime modification of USE flags.

This module provides a virtual filesystem API for adding or removing USE flags
from packages that require custom build configuration (e.g., gevent needs
embed_cares and embed_libev flags).

Patch Operations:
- ADD (++): Add USE flag to IUSE
- REMOVE (--): Remove USE flag from IUSE

Patch File Format:
    ++ embed_cares          # Add USE flag
    ++ embed_libev          # Add another flag
    -- test                 # Remove test flag

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# Valid USE flag pattern (similar to phase names but allows hyphens)
USE_FLAG_PATTERN = re.compile(r'^[a-z][a-z0-9_-]*$')


def is_valid_use_flag(flag: str) -> bool:
    """
    Check if a USE flag name is valid.

    Valid USE flags must start with a lowercase letter and contain only
    lowercase letters, digits, underscores, and hyphens.

    Args:
        flag: The USE flag name to validate

    Returns:
        True if valid, False otherwise

    Examples:
        >>> is_valid_use_flag('embed_cares')
        True
        >>> is_valid_use_flag('test')
        True
        >>> is_valid_use_flag('cpu_flags_x86_sse2')
        True
        >>> is_valid_use_flag('.swp')
        False
        >>> is_valid_use_flag('4913')
        False
    """
    return bool(USE_FLAG_PATTERN.match(flag))


@dataclass
class IUSEPatch:
    """
    Represents a single IUSE modification.

    Attributes:
        operation: One of 'add' or 'remove'
        flag: USE flag name
        timestamp: Unix timestamp when patch was created

    Examples:
        >>> patch = IUSEPatch('add', 'embed_cares', 1700000000.0)
        >>> patch.operation
        'add'
        >>> patch.flag
        'embed_cares'
        >>> patch = IUSEPatch('remove', 'test', 1700000000.0)
        >>> patch.operation
        'remove'
    """
    operation: str  # 'add' or 'remove'
    flag: str       # USE flag name
    timestamp: float  # Unix timestamp

    def __post_init__(self):
        """Validate the patch operation."""
        if self.operation not in ('add', 'remove'):
            raise ValueError(f"Invalid operation: {self.operation}")
        if not self.flag:
            raise ValueError("USE flag is required")
        if not is_valid_use_flag(self.flag):
            raise ValueError(f"Invalid USE flag: {self.flag}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IUSEPatch':
        """Create from dictionary."""
        return cls(**data)

    def to_patch_line(self) -> str:
        """
        Convert to patch file format line.

        Returns:
            Patch file line (++ flag or -- flag)

        Examples:
            >>> patch = IUSEPatch('add', 'embed_cares', 0)
            >>> patch.to_patch_line()
            '++ embed_cares'
            >>> patch = IUSEPatch('remove', 'test', 0)
            >>> patch.to_patch_line()
            '-- test'
        """
        if self.operation == 'add':
            return f"++ {self.flag}"
        elif self.operation == 'remove':
            return f"-- {self.flag}"
        return ""

    @classmethod
    def from_patch_line(cls, line: str, timestamp: Optional[float] = None) -> Optional['IUSEPatch']:
        """
        Parse a patch file line.

        Args:
            line: Patch file line
            timestamp: Timestamp to use (default: current time)

        Returns:
            IUSEPatch or None if line is invalid

        Examples:
            >>> patch = IUSEPatch.from_patch_line('++ embed_cares')
            >>> patch.operation
            'add'
            >>> patch.flag
            'embed_cares'
            >>> patch = IUSEPatch.from_patch_line('-- test')
            >>> patch.operation
            'remove'
        """
        if timestamp is None:
            timestamp = time.time()

        line = line.strip()
        if not line or line.startswith('#'):
            return None

        if line.startswith('++ '):
            # Add: ++ flag
            flag = line[3:].strip()
            if flag and is_valid_use_flag(flag):
                return cls('add', flag, timestamp)
        elif line.startswith('-- '):
            # Remove: -- flag
            flag = line[3:].strip()
            if flag and is_valid_use_flag(flag):
                return cls('remove', flag, timestamp)

        return None


@dataclass
class PackageIUSEPatches:
    """
    Collection of IUSE patches for a specific package version.

    Attributes:
        category: Package category (e.g., 'dev-python')
        package: Package name (e.g., 'gevent')
        version: Version string or '_all' for all versions
        patches: List of IUSE patches

    Examples:
        >>> pp = PackageIUSEPatches('dev-python', 'gevent', '25.9.1', [])
        >>> pp.category
        'dev-python'
        >>> pp.is_all_versions
        False
        >>> pp_all = PackageIUSEPatches('dev-python', 'gevent', '_all', [])
        >>> pp_all.is_all_versions
        True
    """
    category: str
    package: str
    version: str  # Version string or '_all' for all versions
    patches: List[IUSEPatch] = field(default_factory=list)

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
            'patches': [p.to_dict() for p in self.patches]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PackageIUSEPatches':
        """Create from dictionary."""
        patches = [IUSEPatch.from_dict(p) for p in data.get('patches', [])]
        return cls(
            category=data['category'],
            package=data['package'],
            version=data['version'],
            patches=patches
        )


class IUSEPatchStore:
    """
    Storage and application of IUSE patches.

    This class manages patches that add or remove USE flags from packages,
    persisting them to JSON and applying them during ebuild generation.

    Attributes:
        storage_path: Path to the JSON file storing patches
        patches: Dictionary mapping package keys to PackageIUSEPatches

    Examples:
        >>> import tempfile
        >>> with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        ...     store = IUSEPatchStore(f.name)
        >>> store.add_flag('dev-python', 'gevent', '_all', 'embed_cares')
        >>> len(store.get_patches('dev-python', 'gevent', '_all'))
        1
        >>> import os; os.unlink(f.name)
    """

    def __init__(self, storage_path: Optional[str] = None):
        """
        Initialize the patch store.

        Args:
            storage_path: Path to JSON file for persistence (None for memory-only)
        """
        self.storage_path = Path(storage_path) if storage_path else None
        self.patches: Dict[str, PackageIUSEPatches] = {}
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
            iuse_patches = data.get('iuse_patches', [])
            for item in iuse_patches:
                pp = PackageIUSEPatches.from_dict(item)
                self.patches[pp.key] = pp

            logger.info(f"Loaded {len(self.patches)} IUSE patches from {self.storage_path}")

        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.error(f"Failed to load IUSE patches from {self.storage_path}: {e}")
            self.patches = {}

    def save(self) -> bool:
        """
        Save patches to JSON file atomically.

        This method preserves existing data in the file (like dependency_patches,
        python_compat_patches, ebuild_appends) and only updates the iuse_patches section.

        Returns:
            True if save was successful, False otherwise

        Examples:
            >>> import tempfile
            >>> with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            ...     store = IUSEPatchStore(f.name)
            >>> store.add_flag('dev-python', 'test', '1.0', 'embed_cares')
            >>> store.save()
            True
            >>> import os; os.unlink(f.name)
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

            # Update with our patches
            existing_data['version'] = existing_data.get('version', 1)
            if existing_data['version'] < 4:
                existing_data['version'] = 4
            existing_data['iuse_patches'] = [pp.to_dict() for pp in self.patches.values()]

            # Write to temporary file first
            temp_path = self.storage_path.with_suffix('.tmp')
            with temp_path.open('w', encoding='utf-8') as f:
                json.dump(existing_data, f, indent=2)

            # Atomic rename
            temp_path.rename(self.storage_path)
            self._dirty = False

            logger.debug(f"Saved {len(self.patches)} IUSE patches to {self.storage_path}")
            return True

        except OSError as e:
            logger.error(f"Failed to save IUSE patches to {self.storage_path}: {e}")
            return False

    def _get_or_create_patches(self, category: str, package: str, version: str) -> PackageIUSEPatches:
        """Get or create PackageIUSEPatches for a package/version."""
        key = f"{category}/{package}/{version}"
        if key not in self.patches:
            self.patches[key] = PackageIUSEPatches(category, package, version, [])
        return self.patches[key]

    def add_flag(self, category: str, package: str, version: str, flag: str) -> None:
        """
        Add a USE flag to IUSE.

        Args:
            category: Package category (e.g., 'dev-python')
            package: Package name
            version: Version string or '_all'
            flag: USE flag to add (e.g., 'embed_cares')

        Examples:
            >>> store = IUSEPatchStore()
            >>> store.add_flag('dev-python', 'gevent', '_all', 'embed_cares')
            >>> patches = store.get_patches('dev-python', 'gevent', '_all')
            >>> len(patches)
            1
            >>> patches[0].operation
            'add'
        """
        pp = self._get_or_create_patches(category, package, version)
        patch = IUSEPatch('add', flag, time.time())
        pp.patches.append(patch)
        self._dirty = True
        logger.info(f"Added USE flag {flag} to IUSE for {category}/{package}/{version}")

    def remove_flag(self, category: str, package: str, version: str, flag: str) -> None:
        """
        Remove a USE flag from IUSE.

        Args:
            category: Package category (e.g., 'dev-python')
            package: Package name
            version: Version string or '_all'
            flag: USE flag to remove (e.g., 'test')

        Examples:
            >>> store = IUSEPatchStore()
            >>> store.remove_flag('dev-python', 'gevent', '_all', 'test')
            >>> patches = store.get_patches('dev-python', 'gevent', '_all')
            >>> patches[0].operation
            'remove'
        """
        pp = self._get_or_create_patches(category, package, version)
        patch = IUSEPatch('remove', flag, time.time())
        pp.patches.append(patch)
        self._dirty = True
        logger.info(f"Removed USE flag {flag} from IUSE for {category}/{package}/{version}")

    def get_patches(self, category: str, package: str, version: str) -> List[IUSEPatch]:
        """
        Get all patches for a specific package version.

        Returns patches for both the specific version AND _all patches,
        with _all patches applied first, then version-specific patches.

        Args:
            category: Package category
            package: Package name
            version: Version string

        Returns:
            List of IUSEPatch objects in application order

        Examples:
            >>> store = IUSEPatchStore()
            >>> store.add_flag('dev-python', 'gevent', '_all', 'embed_cares')
            >>> store.remove_flag('dev-python', 'gevent', '25.9.1', 'test')
            >>> patches = store.get_patches('dev-python', 'gevent', '25.9.1')
            >>> len(patches)
            2
            >>> patches[0].flag
            'embed_cares'
            >>> patches[1].flag
            'test'
        """
        result = []

        # First apply _all patches (sorted by timestamp)
        all_key = f"{category}/{package}/_all"
        if all_key in self.patches:
            all_patches = sorted(self.patches[all_key].patches, key=lambda p: p.timestamp)
            result.extend(all_patches)

        # Then apply version-specific patches (sorted by timestamp)
        if version != '_all':
            ver_key = f"{category}/{package}/{version}"
            if ver_key in self.patches:
                ver_patches = sorted(self.patches[ver_key].patches, key=lambda p: p.timestamp)
                result.extend(ver_patches)

        return result

    def has_patches(self, category: str, package: str, version: str) -> bool:
        """Check if any patches exist for a package version."""
        return len(self.get_patches(category, package, version)) > 0

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
            if key.startswith(prefix):
                version = key[len(prefix):]
                versions.append(version)
        return sorted(versions)

    def get_current_flags(self, category: str, package: str, version: str) -> List[str]:
        """
        Get the current (patched) flags for a version.

        This returns only the flags added via patches, not original IUSE.
        Use for displaying in .sys/iuse/ directory.

        Args:
            category: Package category
            package: Package name
            version: Version string

        Returns:
            List of flags that have been added (not removed)
        """
        patches = self.get_patches(category, package, version)
        flags = set()
        for patch in patches:
            if patch.operation == 'add':
                flags.add(patch.flag)
            elif patch.operation == 'remove':
                flags.discard(patch.flag)
        return sorted(flags)

    def apply_patches(self, category: str, package: str, version: str,
                      iuse: List[str]) -> List[str]:
        """
        Apply patches to an IUSE list.

        Args:
            category: Package category
            package: Package name
            version: Version string
            iuse: Original list of USE flags

        Returns:
            Modified list of USE flags

        Examples:
            >>> store = IUSEPatchStore()
            >>> store.add_flag('dev-python', 'test', '1.0', 'embed_cares')
            >>> store.remove_flag('dev-python', 'test', '1.0', 'test')
            >>> iuse = ['doc', 'test']
            >>> result = store.apply_patches('dev-python', 'test', '1.0', iuse)
            >>> 'test' in result
            False
            >>> 'embed_cares' in result
            True
            >>> 'doc' in result
            True
        """
        patches = self.get_patches(category, package, version)
        if not patches:
            return iuse

        # Work with a copy
        result = list(iuse)

        for patch in patches:
            if patch.operation == 'add':
                # Add flag if not already present
                if patch.flag not in result:
                    result.append(patch.flag)

            elif patch.operation == 'remove':
                # Remove flag
                result = [flag for flag in result if flag != patch.flag]

        return result

    def generate_patch_file(self, category: str, package: str, version: str) -> str:
        """
        Generate portable patch file content for a package version.

        Args:
            category: Package category
            package: Package name
            version: Version string

        Returns:
            Patch file content as string

        Examples:
            >>> store = IUSEPatchStore()
            >>> store.add_flag('dev-python', 'gevent', '_all', 'embed_cares')
            >>> content = store.generate_patch_file('dev-python', 'gevent', '_all')
            >>> '++ embed_cares' in content
            True
        """
        patches = self.get_patches(category, package, version)
        lines = [
            f"# IUSE patches for {category}/{package}/{version}",
            f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            ""
        ]

        for patch in patches:
            lines.append(patch.to_patch_line())

        return '\n'.join(lines) + '\n'

    def parse_patch_file(self, content: str, category: str, package: str, version: str) -> int:
        """
        Parse and import patches from patch file content.

        Args:
            content: Patch file content
            category: Target package category
            package: Target package name
            version: Target version

        Returns:
            Number of patches imported

        Examples:
            >>> store = IUSEPatchStore()
            >>> content = '''
            ... # Patches
            ... ++ embed_cares
            ... ++ embed_libev
            ... -- test
            ... '''
            >>> count = store.parse_patch_file(content, 'dev-python', 'gevent', '_all')
            >>> count
            3
        """
        pp = self._get_or_create_patches(category, package, version)
        count = 0
        timestamp = time.time()

        for line in content.splitlines():
            patch = IUSEPatch.from_patch_line(line, timestamp)
            if patch:
                pp.patches.append(patch)
                count += 1
                timestamp += 0.001  # Ensure unique timestamps

        if count > 0:
            self._dirty = True
            logger.info(f"Imported {count} IUSE patches for {category}/{package}/{version}")

        return count

    def clear_patches(self, category: str, package: str, version: str) -> int:
        """
        Clear all patches for a specific package version.

        Returns:
            Number of patches cleared
        """
        key = f"{category}/{package}/{version}"
        if key in self.patches:
            count = len(self.patches[key].patches)
            del self.patches[key]
            self._dirty = True
            logger.info(f"Cleared {count} IUSE patches for {key}")
            return count
        return 0

    def list_patched_packages(self) -> List[Tuple[str, str, str]]:
        """
        List all packages that have patches.

        Returns:
            List of (category, package, version) tuples
        """
        result = []
        for key in sorted(self.patches.keys()):
            parts = key.split('/')
            if len(parts) == 3:
                result.append((parts[0], parts[1], parts[2]))
        return result

    @property
    def is_dirty(self) -> bool:
        """Check if there are unsaved changes."""
        return self._dirty

    def unlink_flag(self, category: str, package: str, version: str, flag: str) -> bool:
        """
        Remove a specific flag patch (for rm operation in filesystem).

        This removes the patch that added the flag, or adds a remove patch
        if the flag was not added via patches.

        Args:
            category: Package category
            package: Package name
            version: Version string
            flag: USE flag to remove

        Returns:
            True if action was taken, False if nothing to do
        """
        key = f"{category}/{package}/{version}"
        if key in self.patches:
            pp = self.patches[key]
            # Find and remove the 'add' patch for this flag
            original_count = len(pp.patches)
            pp.patches = [p for p in pp.patches if not (p.operation == 'add' and p.flag == flag)]
            if len(pp.patches) < original_count:
                self._dirty = True
                # Remove empty entries
                if not pp.patches:
                    del self.patches[key]
                logger.info(f"Removed IUSE add patch for {flag} from {category}/{package}/{version}")
                return True

        # If flag wasn't added via patch, add a remove patch
        # (This handles removing original IUSE flags)
        self.remove_flag(category, package, version, flag)
        return True
