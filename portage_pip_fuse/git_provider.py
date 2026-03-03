"""
Git repository URL detection and normalization for wheel-only packages.

This module provides functions to extract git repository URLs from PyPI
project_urls metadata and normalize them for use with git-r3.eclass.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import logging
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

# Known git hosting platforms and their URL patterns
GIT_HOSTS = {
    'github.com',
    'gitlab.com',
    'gitlab.gnome.org',
    'gitlab.freedesktop.org',
    'codeberg.org',
    'bitbucket.org',
    'git.sr.ht',        # SourceHut
    'git.savannah.gnu.org',
    'git.savannah.nongnu.org',
    'gitea.com',
}

# URL key names in project_urls that typically point to source repositories
# Ordered by reliability (most reliable first)
REPOSITORY_URL_KEYS = [
    'Repository',
    'Source',
    'Source Code',
    'Code',
    'GitHub',
    'GitLab',
    'Bitbucket',
    'Git',
    'VCS',
    'Download',   # Only used if it matches a known git host
    'Homepage',   # Only used if it matches a known git host
]


def extract_git_url(project_urls: Dict[str, str]) -> Optional[str]:
    """
    Extract a git repository URL from PyPI project_urls metadata.

    This function searches through the project_urls dictionary for known
    repository URL keys and returns the first valid git URL found.

    Args:
        project_urls: Dictionary of URL labels to URLs from PyPI metadata

    Returns:
        Git repository URL if found, None otherwise

    Examples:
        >>> extract_git_url({'Repository': 'https://github.com/user/repo'})
        'https://github.com/user/repo'
        >>> extract_git_url({'Source': 'https://gitlab.com/user/repo'})
        'https://gitlab.com/user/repo'
        >>> extract_git_url({'GitHub': 'https://github.com/user/repo'})
        'https://github.com/user/repo'
        >>> extract_git_url({'Homepage': 'https://github.com/user/repo'})
        'https://github.com/user/repo'
        >>> extract_git_url({'Homepage': 'https://example.com'})  # Not a git host
        >>> extract_git_url({})
    """
    if not project_urls:
        return None

    # Normalize keys to lowercase for case-insensitive matching
    normalized_urls = {k.lower().strip(): v for k, v in project_urls.items()}

    for key in REPOSITORY_URL_KEYS:
        key_lower = key.lower()
        if key_lower in normalized_urls:
            url = normalized_urls[key_lower]
            if is_git_host_url(url):
                logger.debug(f"Found git URL under '{key}': {url}")
                return url
            elif key not in ('Homepage', 'Download'):
                # For explicit repo keys, trust the label even if not a known host
                # (might be self-hosted git)
                # But Homepage/Download can point to non-git URLs, so require known host
                logger.debug(f"Found potential git URL under '{key}': {url}")
                return url

    return None


def is_git_host_url(url: str) -> bool:
    """
    Check if a URL points to a known git hosting platform.

    Args:
        url: URL to check

    Returns:
        True if the URL is from a known git host

    Examples:
        >>> is_git_host_url('https://github.com/user/repo')
        True
        >>> is_git_host_url('https://gitlab.com/user/repo')
        True
        >>> is_git_host_url('https://codeberg.org/user/repo')
        True
        >>> is_git_host_url('https://bitbucket.org/user/repo')
        True
        >>> is_git_host_url('https://example.com/repo')
        False
        >>> is_git_host_url('invalid-url')
        False
    """
    try:
        parsed = urlparse(url)
        return parsed.hostname in GIT_HOSTS if parsed.hostname else False
    except Exception:
        return False


def normalize_git_url(url: str) -> str:
    """
    Normalize a git URL for use with git-r3.eclass.

    This function converts various URL formats to the canonical clone URL format:
    - Removes /tree/main, /tree/master, /blob/..., etc. suffixes
    - Converts SSH URLs to HTTPS
    - Adds .git suffix if missing (for GitHub/GitLab)
    - Handles various special cases

    Args:
        url: Git repository URL in various formats

    Returns:
        Normalized HTTPS clone URL

    Examples:
        >>> normalize_git_url('https://github.com/user/repo')
        'https://github.com/user/repo.git'
        >>> normalize_git_url('https://github.com/user/repo.git')
        'https://github.com/user/repo.git'
        >>> normalize_git_url('https://github.com/user/repo/tree/main')
        'https://github.com/user/repo.git'
        >>> normalize_git_url('https://github.com/user/repo/blob/main/README.md')
        'https://github.com/user/repo.git'
        >>> normalize_git_url('git@github.com:user/repo.git')
        'https://github.com/user/repo.git'
        >>> normalize_git_url('ssh://git@github.com/user/repo.git')
        'https://github.com/user/repo.git'
        >>> normalize_git_url('https://gitlab.com/user/repo/-/tree/main')
        'https://gitlab.com/user/repo.git'
        >>> normalize_git_url('https://bitbucket.org/user/repo/src/master/')
        'https://bitbucket.org/user/repo.git'
    """
    url = url.strip()

    # Handle git@ SSH format (git@github.com:user/repo.git)
    if url.startswith('git@'):
        match = re.match(r'git@([^:]+):(.+)', url)
        if match:
            host, path = match.groups()
            url = f'https://{host}/{path}'

    # Handle ssh:// format
    if url.startswith('ssh://'):
        url = url.replace('ssh://', 'https://', 1)
        # Remove git@ from URL path if present
        url = re.sub(r'https://git@', 'https://', url)

    # Parse the URL
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    # Get the path and clean it
    path = parsed.path

    # Remove common tree/blob/src suffixes
    # GitHub: /tree/branch, /blob/branch/file
    # GitLab: /-/tree/branch, /-/blob/branch/file (note: /-/ is GitLab-specific)
    # Bitbucket: /src/branch/, /src/branch/file
    # GitLab patterns must come first (more specific)
    path = re.sub(r'/-/tree/[^/]+.*$', '', path)
    path = re.sub(r'/-/blob/[^/]+.*$', '', path)
    path = re.sub(r'/-/commits/[^/]+.*$', '', path)
    # GitHub patterns (less specific)
    path = re.sub(r'/tree/[^/]+.*$', '', path)
    path = re.sub(r'/blob/[^/]+.*$', '', path)
    path = re.sub(r'/src/[^/]+.*$', '', path)
    path = re.sub(r'/commits/[^/]+.*$', '', path)
    path = re.sub(r'/issues/?.*$', '', path)
    path = re.sub(r'/pulls?/?.*$', '', path)
    path = re.sub(r'/releases/?.*$', '', path)
    path = re.sub(r'/tags/?.*$', '', path)

    # Remove trailing slashes
    path = path.rstrip('/')

    # Add .git suffix if missing (for common hosts)
    needs_git_suffix = parsed.hostname in {
        'github.com', 'gitlab.com', 'gitlab.gnome.org',
        'gitlab.freedesktop.org', 'codeberg.org', 'bitbucket.org',
    }
    if needs_git_suffix and not path.endswith('.git'):
        path = path + '.git'

    # Reconstruct the URL
    normalized = urlunparse((
        'https',
        parsed.hostname or '',
        path,
        '',
        '',
        ''
    ))

    return normalized


def detect_version_tag(version: str) -> str:
    """
    Map a package version to the most likely git tag pattern.

    Most projects use 'v{version}' (e.g., v1.2.3) but some use plain version
    numbers or other patterns.

    Args:
        version: Package version string

    Returns:
        Git tag pattern using ${PV} variable

    Examples:
        >>> detect_version_tag('1.0.0')
        'v${PV}'
        >>> detect_version_tag('1.2.3')
        'v${PV}'
    """
    # Most common pattern is v{version}
    # The ebuild will substitute ${PV} at build time
    return 'v${PV}'


def get_tag_patterns() -> List[str]:
    """
    Get a list of common git tag patterns to try.

    Returns:
        List of tag patterns in order of likelihood

    Examples:
        >>> patterns = get_tag_patterns()
        >>> 'v${PV}' in patterns
        True
        >>> '${PV}' in patterns
        True
    """
    return [
        'v${PV}',           # Most common: v1.2.3
        '${PV}',            # Plain version: 1.2.3
        'release-${PV}',    # release-1.2.3
        'rel-${PV}',        # rel-1.2.3
        'ver-${PV}',        # ver-1.2.3
        '${PN}-${PV}',      # package-1.2.3
    ]


def validate_git_url(url: str) -> bool:
    """
    Validate that a URL is a properly formatted git repository URL.

    Args:
        url: URL to validate

    Returns:
        True if the URL appears to be a valid git repository URL

    Examples:
        >>> validate_git_url('https://github.com/user/repo.git')
        True
        >>> validate_git_url('https://github.com/user/repo')
        True
        >>> validate_git_url('git@github.com:user/repo.git')
        True
        >>> validate_git_url('invalid')
        False
        >>> validate_git_url('')
        False
    """
    if not url:
        return False

    # Handle git@ format
    if url.startswith('git@'):
        return bool(re.match(r'git@[^:]+:.+', url))

    # Handle regular URLs
    try:
        parsed = urlparse(url)
        return bool(parsed.scheme in ('https', 'http', 'ssh') and parsed.hostname)
    except Exception:
        return False
