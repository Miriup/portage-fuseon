"""
PyPI ecosystem plugin for portage-fuse.

This package provides the PyPI/pip integration for generating
dev-python ebuilds from Python Package Index metadata.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

from portage_pip_fuse.ecosystems.pypi.plugin import PyPIPlugin

__all__ = ['PyPIPlugin']
