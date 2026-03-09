"""
RubyGems ecosystem plugin for portage-fuse.

This package provides the RubyGems integration for generating
dev-ruby ebuilds from Ruby Gem metadata.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

from portage_pip_fuse.ecosystems.rubygems.plugin import RubyGemsPlugin
from portage_pip_fuse.ecosystems.rubygems.filesystem import PortageGemFS, mount_rubygems_filesystem

__all__ = ['RubyGemsPlugin', 'PortageGemFS', 'mount_rubygems_filesystem']
