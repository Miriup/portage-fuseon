"""
Constants used across the portage-pip-fuse filesystem.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

# Repository name that identifies this FUSE filesystem to portage
REPO_NAME = "portage-pip-fuse"

# Default cache directory
DEFAULT_CACHE_DIR = "/tmp/portage-pip-fuse-cache"

# Cache time-to-live in seconds (1 hour)
DEFAULT_CACHE_TTL = 3600

# Maximum depth for dependency resolution
DEFAULT_MAX_DEPENDENCY_DEPTH = 10