"""
Ecosystem plugins for portage-fuse.

This package contains ecosystem-specific implementations for different
package registries (PyPI, RubyGems, etc.).

Each ecosystem is a subpackage containing:
- plugin.py: EcosystemPlugin implementation
- metadata.py: MetadataProviderBase implementation
- ebuild.py: EbuildGeneratorBase implementation
- filters.py: Ecosystem-specific filters (optional)
- name_translator.py: Name translation rules (optional)

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

from typing import List

# List of available ecosystems
AVAILABLE_ECOSYSTEMS: List[str] = ['pypi', 'rubygems']

__all__ = ['AVAILABLE_ECOSYSTEMS']
