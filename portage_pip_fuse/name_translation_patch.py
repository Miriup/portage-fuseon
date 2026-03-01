"""
Manual package name translation patches for PyPI-to-Gentoo mappings.

This module provides a virtual filesystem API for overriding the default
PyPI-to-Gentoo package name translation for packages that don't follow
the standard dev-python/* naming convention.

Primary use case: Packages like pytorch that exist in sci-ml/ instead of
dev-python/, allowing dependencies on "torch" to resolve to "sci-ml/pytorch".

Patch File Format (/.sys/name-translation/{pypi_name}):
    sci-ml/pytorch

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any

from .constants import get_mount_point_key

# Import canonicalize_name from pip's vendored packaging if available
try:
    from pip._vendor.packaging.utils import canonicalize_name as pip_canonicalize_name
except ImportError:
    # Fallback implementation based on PEP 503
    _canonicalize_regex = re.compile(r"[-_.]+")

    def pip_canonicalize_name(name: str) -> str:
        """Normalize a PyPI package name according to PEP 503."""
        return _canonicalize_regex.sub("-", name).lower()


logger = logging.getLogger(__name__)

# Current patch file format version (shared with other patch stores)
PATCH_FILE_VERSION = 3

# Regex for validating Gentoo atoms: category/package-name
# Category: lowercase letters, numbers, plus, underscore, hyphen, dot
# Package: lowercase letters, numbers, plus, underscore, hyphen
GENTOO_ATOM_PATTERN = re.compile(r'^[a-z0-9+_.-]+/[a-z0-9+_-]+$')


def is_valid_gentoo_atom(atom: str) -> bool:
    """
    Check if a string is a valid Gentoo atom (category/package).

    Args:
        atom: The string to validate

    Returns:
        True if valid Gentoo atom format, False otherwise

    Examples:
        >>> is_valid_gentoo_atom('sci-ml/pytorch')
        True
        >>> is_valid_gentoo_atom('dev-python/requests')
        True
        >>> is_valid_gentoo_atom('app-misc/my_package')
        True
        >>> is_valid_gentoo_atom('pytorch')
        False
        >>> is_valid_gentoo_atom('SCI-ML/pytorch')
        False
        >>> is_valid_gentoo_atom('')
        False
        >>> is_valid_gentoo_atom('dev-python/')
        False
        >>> is_valid_gentoo_atom('/requests')
        False
    """
    if not atom or '/' not in atom:
        return False
    return bool(GENTOO_ATOM_PATTERN.match(atom))


def normalize_pypi_name(name: str) -> str:
    """
    Normalize a PyPI package name for consistent lookups.

    Uses PEP 503 normalization (lowercase, hyphens replace [-_.]).

    Args:
        name: PyPI package name to normalize

    Returns:
        Normalized name

    Examples:
        >>> normalize_pypi_name('torch')
        'torch'
        >>> normalize_pypi_name('PyTorch')
        'pytorch'
        >>> normalize_pypi_name('some_package')
        'some-package'
        >>> normalize_pypi_name('Some.Package')
        'some-package'
    """
    return pip_canonicalize_name(name)


@dataclass
class NameTranslationMapping:
    """
    Represents a manual PyPI-to-Gentoo package name mapping.

    Attributes:
        pypi_name: Normalized PyPI package name (e.g., 'torch')
        gentoo_atom: Full Gentoo atom (e.g., 'sci-ml/pytorch')
        timestamp: Unix timestamp when mapping was created

    Examples:
        >>> mapping = NameTranslationMapping('torch', 'sci-ml/pytorch', 1700000000.0)
        >>> mapping.pypi_name
        'torch'
        >>> mapping.gentoo_atom
        'sci-ml/pytorch'
        >>> mapping.category
        'sci-ml'
        >>> mapping.package
        'pytorch'
    """
    pypi_name: str
    gentoo_atom: str
    timestamp: float

    def __post_init__(self):
        """Validate the mapping."""
        if not is_valid_gentoo_atom(self.gentoo_atom):
            raise ValueError(f"Invalid Gentoo atom: {self.gentoo_atom}")
        # Normalize the PyPI name
        self.pypi_name = normalize_pypi_name(self.pypi_name)

    @property
    def category(self) -> str:
        """Extract category from atom."""
        return self.gentoo_atom.split('/')[0]

    @property
    def package(self) -> str:
        """Extract package name from atom."""
        return self.gentoo_atom.split('/')[1]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'NameTranslationMapping':
        """Create from dictionary."""
        return cls(**data)


class NameTranslationPatchStore:
    """
    Storage and management of manual package name translation mappings.

    This class manages mappings that override the default PyPI-to-Gentoo
    package name translation, persisting them to JSON and applying them
    during ebuild generation.

    Attributes:
        storage_path: Path to the JSON file storing patches
        mount_point: Canonical mount point for namespaced configuration
        mappings: Dictionary mapping normalized PyPI names to NameTranslationMapping

    Examples:
        >>> import tempfile
        >>> with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        ...     store = NameTranslationPatchStore(f.name)
        >>> store.set_mapping('torch', 'sci-ml/pytorch')
        >>> store.get_mapping('torch')
        'sci-ml/pytorch'
        >>> store.get_mapping('PyTorch')  # Also works with non-normalized names
        'sci-ml/pytorch'
        >>> store.list_mappings()
        ['torch']
        >>> import os; os.unlink(f.name)
    """

    def __init__(self, storage_path: Optional[str] = None, mount_point: Optional[str] = None):
        """
        Initialize the patch store.

        Args:
            storage_path: Path to JSON file for persistence (None for memory-only)
            mount_point: Mount point path for namespaced configuration

        Note:
            WARNING: Race conditions with concurrent mounts

            When multiple FUSE instances share the same patches.json file,
            concurrent saves may cause one instance's changes to be lost.
            Each instance reads full file, modifies its section, writes back.

            Mitigation: Each mount point has isolated namespace.
            For guaranteed isolation: use separate --patch-file per mount.
        """
        self.storage_path = Path(storage_path) if storage_path else None
        self.mount_point = get_mount_point_key(mount_point) if mount_point else None
        self.mappings: Dict[str, NameTranslationMapping] = {}
        self._dirty = False

        if self.storage_path and self.storage_path.exists():
            self._load()

    def _load(self) -> None:
        """Load mappings from JSON file."""
        if not self.storage_path or not self.storage_path.exists():
            return

        try:
            with self.storage_path.open('r', encoding='utf-8') as f:
                data = json.load(f)

            self.mappings = {}
            version = data.get('version', 1)

            if version >= 3 and 'mount_points' in data:
                # v3 format: mount_points -> {mount_point -> {name_translations: [...]}}
                if self.mount_point and self.mount_point in data['mount_points']:
                    mp_data = data['mount_points'][self.mount_point]
                    for item in mp_data.get('name_translations', []):
                        mapping = NameTranslationMapping.from_dict(item)
                        self.mappings[mapping.pypi_name] = mapping

            logger.info(
                f"Loaded {len(self.mappings)} name translation mappings from {self.storage_path}"
                + (f" (mount: {self.mount_point})" if self.mount_point else "")
            )

        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.error(f"Failed to load name translation mappings from {self.storage_path}: {e}")
            self.mappings = {}

    def save(self) -> bool:
        """
        Save mappings to JSON file atomically.

        This method preserves existing data in the file (other mount points,
        and other patch types) and only updates the name_translations section
        for this mount point.

        Returns:
            True if save was successful, False otherwise

        Examples:
            >>> import tempfile
            >>> with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            ...     store = NameTranslationPatchStore(f.name)
            >>> store.set_mapping('torch', 'sci-ml/pytorch')
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

            old_version = existing_data.get('version', 1)

            # Migrate to v3 format if needed
            if old_version < 3:
                existing_data['version'] = PATCH_FILE_VERSION
                if 'mount_points' not in existing_data:
                    existing_data['mount_points'] = {}
            else:
                existing_data['version'] = PATCH_FILE_VERSION
                if 'mount_points' not in existing_data:
                    existing_data['mount_points'] = {}

            # Update mappings for this mount point
            mp_key = self.mount_point or '_default'
            if mp_key not in existing_data['mount_points']:
                existing_data['mount_points'][mp_key] = {}
            existing_data['mount_points'][mp_key]['name_translations'] = [
                mapping.to_dict() for mapping in self.mappings.values()
            ]

            # Write to temporary file first
            temp_path = self.storage_path.with_suffix('.tmp')
            with temp_path.open('w', encoding='utf-8') as f:
                json.dump(existing_data, f, indent=2)

            # Atomic rename
            temp_path.rename(self.storage_path)
            self._dirty = False

            logger.debug(
                f"Saved {len(self.mappings)} name translation mappings to {self.storage_path}"
                + (f" (mount: {self.mount_point})" if self.mount_point else "")
            )
            return True

        except OSError as e:
            logger.error(f"Failed to save name translation mappings to {self.storage_path}: {e}")
            return False

    def set_mapping(self, pypi_name: str, gentoo_atom: str) -> None:
        """
        Set a manual name translation mapping.

        Args:
            pypi_name: PyPI package name (will be normalized)
            gentoo_atom: Full Gentoo atom (e.g., 'sci-ml/pytorch')

        Raises:
            ValueError: If gentoo_atom is not a valid Gentoo atom

        Examples:
            >>> store = NameTranslationPatchStore()
            >>> store.set_mapping('torch', 'sci-ml/pytorch')
            >>> store.get_mapping('torch')
            'sci-ml/pytorch'
            >>> store.set_mapping('PyTorch', 'sci-ml/pytorch')  # Normalizes to 'pytorch'
            >>> store.get_mapping('pytorch')
            'sci-ml/pytorch'
        """
        if not is_valid_gentoo_atom(gentoo_atom):
            raise ValueError(
                f"Invalid Gentoo atom: {gentoo_atom}. "
                f"Expected format: category/package (e.g., 'sci-ml/pytorch')"
            )

        normalized_name = normalize_pypi_name(pypi_name)
        mapping = NameTranslationMapping(normalized_name, gentoo_atom, time.time())
        self.mappings[normalized_name] = mapping
        self._dirty = True
        logger.info(f"Set name translation: {normalized_name} -> {gentoo_atom}")

    def get_mapping(self, pypi_name: str) -> Optional[str]:
        """
        Get the Gentoo atom for a PyPI package name.

        Args:
            pypi_name: PyPI package name (will be normalized for lookup)

        Returns:
            Full Gentoo atom (e.g., 'sci-ml/pytorch') if mapped, None otherwise

        Examples:
            >>> store = NameTranslationPatchStore()
            >>> store.set_mapping('torch', 'sci-ml/pytorch')
            >>> store.get_mapping('torch')
            'sci-ml/pytorch'
            >>> store.get_mapping('PyTorch')  # Case-insensitive lookup
            'sci-ml/pytorch'
            >>> store.get_mapping('requests') is None
            True
        """
        normalized_name = normalize_pypi_name(pypi_name)
        mapping = self.mappings.get(normalized_name)
        return mapping.gentoo_atom if mapping else None

    def remove_mapping(self, pypi_name: str) -> bool:
        """
        Remove a name translation mapping.

        Args:
            pypi_name: PyPI package name (will be normalized)

        Returns:
            True if removed, False if not found

        Examples:
            >>> store = NameTranslationPatchStore()
            >>> store.set_mapping('torch', 'sci-ml/pytorch')
            >>> store.remove_mapping('torch')
            True
            >>> store.get_mapping('torch') is None
            True
            >>> store.remove_mapping('torch')
            False
        """
        normalized_name = normalize_pypi_name(pypi_name)
        if normalized_name in self.mappings:
            del self.mappings[normalized_name]
            self._dirty = True
            logger.info(f"Removed name translation for: {normalized_name}")
            return True
        return False

    def has_mapping(self, pypi_name: str) -> bool:
        """
        Check if a mapping exists for a PyPI package name.

        Args:
            pypi_name: PyPI package name (will be normalized)

        Returns:
            True if a mapping exists, False otherwise

        Examples:
            >>> store = NameTranslationPatchStore()
            >>> store.has_mapping('torch')
            False
            >>> store.set_mapping('torch', 'sci-ml/pytorch')
            >>> store.has_mapping('torch')
            True
        """
        normalized_name = normalize_pypi_name(pypi_name)
        return normalized_name in self.mappings

    def list_mappings(self) -> List[str]:
        """
        List all mapped PyPI package names.

        Returns:
            Sorted list of normalized PyPI package names that have mappings

        Examples:
            >>> store = NameTranslationPatchStore()
            >>> store.set_mapping('torch', 'sci-ml/pytorch')
            >>> store.set_mapping('tensorflow', 'sci-ml/tensorflow')
            >>> store.list_mappings()
            ['tensorflow', 'torch']
        """
        return sorted(self.mappings.keys())

    def get_all_mappings(self) -> Dict[str, str]:
        """
        Get all mappings as a dictionary.

        Returns:
            Dictionary mapping PyPI names to Gentoo atoms

        Examples:
            >>> store = NameTranslationPatchStore()
            >>> store.set_mapping('torch', 'sci-ml/pytorch')
            >>> store.get_all_mappings()
            {'torch': 'sci-ml/pytorch'}
        """
        return {name: mapping.gentoo_atom for name, mapping in self.mappings.items()}

    def apply_to_translator(self, translator) -> None:
        """
        Apply mappings to a name translator (if it supports custom mappings).

        This is a hook for future integration with the name translator.
        Currently a no-op as the translator doesn't support custom mappings yet.

        Args:
            translator: NameTranslatorBase instance
        """
        # Future: translator.add_custom_mappings(self.get_all_mappings())
        pass

    @property
    def is_dirty(self) -> bool:
        """Check if there are unsaved changes."""
        return self._dirty
