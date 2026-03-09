"""
Source providers for RubyGems packages.

This module provides source provider implementations for Ruby gems,
supporting .gem files (primary) and git repositories (fallback).

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import logging
from typing import Any, Dict, List, Optional

from portage_pip_fuse.source_provider import (
    SourceProviderBase,
    SourceInfo,
)

logger = logging.getLogger(__name__)


class GemSourceProvider(SourceProviderBase):
    """
    Source provider for .gem files from RubyGems.org.

    This is the primary source provider for Ruby gems, using the
    standard .gem archive format from RubyGems.org.
    """

    def name(self) -> str:
        """Get provider name.

        Examples:
            >>> provider = GemSourceProvider()
            >>> provider.name()
            'gem'
        """
        return 'gem'

    def priority(self) -> int:
        """Get priority (highest for gems).

        Examples:
            >>> provider = GemSourceProvider()
            >>> provider.priority()
            100
        """
        return 100

    def can_provide(self, package_info: Dict[str, Any]) -> bool:
        """
        Check if package has a .gem file available.

        Examples:
            >>> provider = GemSourceProvider()
            >>> provider.can_provide({'gem_uri': 'https://rubygems.org/gems/rails-7.0.0.gem'})
            True
            >>> provider.can_provide({'gem_uri': None})
            False
            >>> provider.can_provide({})
            True  # Assume gems have .gem files by default
        """
        # Most gems have .gem files available
        # Only reject if explicitly marked as unavailable
        gem_uri = package_info.get('gem_uri')
        if gem_uri is not None:
            return bool(gem_uri)

        # Assume available if not explicitly marked
        return True

    def get_source_info(self, package_info: Dict[str, Any], version: str) -> Optional[SourceInfo]:
        """
        Get source info for gem-based package.

        Examples:
            >>> provider = GemSourceProvider()
            >>> info = provider.get_source_info({'name': 'rails'}, '7.0.0')
            >>> info.provider_name
            'gem'
            >>> 'ruby-fakegem' in info.eclass_inherits
            True
        """
        if not self.can_provide(package_info):
            return None

        name = package_info.get('name', '')
        gem_uri = package_info.get('gem_uri')

        if not gem_uri:
            # Construct standard RubyGems.org URL
            gem_uri = f"https://rubygems.org/gems/{name}-{version}.gem"

        return SourceInfo(
            provider_name='gem',
            eclass_inherits=['ruby-fakegem'],
            src_uri=gem_uri,
        )


class RubyGitProvider(SourceProviderBase):
    """
    Source provider for Ruby gems from git repositories.

    This provider is used when a gem is not available as a .gem file
    or when explicitly configured to use git source.

    Uses ruby-fakegem combined with git-r3 eclass.
    """

    # Known git hosting patterns for Ruby projects
    GIT_HOST_PATTERNS = {
        'github.com': lambda url: f"{url}.git" if not url.endswith('.git') else url,
        'gitlab.com': lambda url: f"{url}.git" if not url.endswith('.git') else url,
        'gitlab.gnome.org': lambda url: f"{url}.git" if not url.endswith('.git') else url,
        'bitbucket.org': lambda url: url,
        'codeberg.org': lambda url: f"{url}.git" if not url.endswith('.git') else url,
        'sr.ht': lambda url: url,
    }

    def __init__(self, git_source_patch_store=None):
        """
        Initialize the git provider.

        Args:
            git_source_patch_store: Optional patch store for manual overrides
        """
        self.git_source_patch_store = git_source_patch_store

    def name(self) -> str:
        """Get provider name.

        Examples:
            >>> provider = RubyGitProvider()
            >>> provider.name()
            'ruby-git'
        """
        return 'ruby-git'

    def priority(self) -> int:
        """Get priority (below gem, above nothing).

        Examples:
            >>> provider = RubyGitProvider()
            >>> provider.priority()
            75
        """
        return 75

    def can_provide(self, package_info: Dict[str, Any]) -> bool:
        """
        Check if package has a git repository URL.

        Examples:
            >>> provider = RubyGitProvider()
            >>> provider.can_provide({'source_code_uri': 'https://github.com/rails/rails'})
            True
            >>> provider.can_provide({'homepage_uri': 'https://github.com/rails/rails'})
            True
            >>> provider.can_provide({})
            False
        """
        git_url = self._extract_git_url(package_info)
        return git_url is not None

    def get_source_info(self, package_info: Dict[str, Any], version: str) -> Optional[SourceInfo]:
        """
        Get source info for git-based package.

        Examples:
            >>> provider = RubyGitProvider()
            >>> info = provider.get_source_info(
            ...     {'source_code_uri': 'https://github.com/rails/rails'},
            ...     '7.0.0'
            ... )
            >>> info.provider_name
            'ruby-git'
            >>> 'git-r3' in info.eclass_inherits
            True
        """
        if not self.can_provide(package_info):
            return None

        git_url = self._extract_git_url(package_info)
        if not git_url:
            return None

        normalized_url = self._normalize_git_url(git_url)

        return SourceInfo(
            provider_name='ruby-git',
            eclass_inherits=['ruby-fakegem', 'git-r3'],
            git_repo_uri=normalized_url,
            git_commit='v${PV}',  # Most common tag pattern
        )

    def _extract_git_url(self, package_info: Dict[str, Any]) -> Optional[str]:
        """
        Extract git repository URL from package metadata.

        Checks various fields in order of preference:
        1. source_code_uri
        2. homepage_uri (if it looks like a git host)
        3. project_uri (if it looks like a git host)
        """
        # Check source_code_uri first
        source_uri = package_info.get('source_code_uri', '')
        if source_uri and self._is_git_url(source_uri):
            return source_uri

        # Check homepage_uri
        homepage = package_info.get('homepage_uri', '')
        if homepage and self._is_git_url(homepage):
            return homepage

        # Check project_uri
        project_uri = package_info.get('project_uri', '')
        if project_uri and self._is_git_url(project_uri):
            return project_uri

        return None

    def _is_git_url(self, url: str) -> bool:
        """Check if URL is a git repository URL."""
        if not url:
            return False

        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.netloc.lower()

        # Check against known git hosts
        for pattern in self.GIT_HOST_PATTERNS:
            if pattern in host:
                return True

        # Check for explicit git:// or .git suffix
        if parsed.scheme == 'git' or url.endswith('.git'):
            return True

        return False

    def _normalize_git_url(self, url: str) -> str:
        """
        Normalize git URL for use with git-r3.eclass.

        - Ensure HTTPS (not git://)
        - Add .git suffix if needed
        - Remove tree/blob paths
        """
        import re
        from urllib.parse import urlparse, urlunparse

        # Handle SSH URLs (git@github.com:user/repo)
        if url.startswith('git@'):
            match = re.match(r'git@([^:]+):(.+)', url)
            if match:
                host, path = match.groups()
                url = f"https://{host}/{path}"

        parsed = urlparse(url)
        host = parsed.netloc.lower()

        # Remove tree/blob/commits paths
        path = parsed.path
        path = re.sub(r'/tree/[^/]+/?.*$', '', path)
        path = re.sub(r'/blob/[^/]+/?.*$', '', path)
        path = re.sub(r'/-/tree/[^/]+/?.*$', '', path)
        path = re.sub(r'/commits?/[^/]+/?.*$', '', path)

        # Ensure .git suffix for GitHub/GitLab
        for pattern, normalizer in self.GIT_HOST_PATTERNS.items():
            if pattern in host:
                if not path.endswith('.git'):
                    path = f"{path}.git"
                break

        # Ensure HTTPS
        scheme = 'https'

        normalized = urlunparse((scheme, host, path, '', '', ''))
        return normalized


class RubyGitForceProvider(RubyGitProvider):
    """
    Force git source for gems that don't have .gem files.

    This is used for gems that are only available from git
    (common in Gemfile.lock with git: sources).
    """

    def priority(self) -> int:
        """Highest priority when forced."""
        return 150

    def can_provide(self, package_info: Dict[str, Any]) -> bool:
        """
        Only provide if explicitly marked for git source.

        Examples:
            >>> provider = RubyGitForceProvider()
            >>> provider.can_provide({'_force_git_source': True, 'source_code_uri': 'https://github.com/...'})
            True
            >>> provider.can_provide({'source_code_uri': 'https://github.com/...'})
            False
        """
        if not package_info.get('_force_git_source'):
            return False
        return super().can_provide(package_info)


class SourceProviderChain:
    """
    Chain of source providers for Ruby gems.

    Tries providers in priority order and returns the first match.
    """

    def __init__(
        self,
        providers: Optional[List[SourceProviderBase]] = None,
        enable_git: bool = True,
        git_source_patch_store=None
    ):
        """
        Initialize the provider chain.

        Args:
            providers: Optional list of providers (uses defaults if None)
            enable_git: Whether to enable the git provider
            git_source_patch_store: Optional patch store for git overrides
        """
        if providers is not None:
            self.providers = sorted(providers, key=lambda p: p.priority(), reverse=True)
        else:
            # Default providers
            default_providers = [GemSourceProvider()]
            if enable_git:
                default_providers.append(RubyGitProvider(git_source_patch_store))
            self.providers = sorted(default_providers, key=lambda p: p.priority(), reverse=True)

    def get_source_info(self, package_info: Dict[str, Any], version: str) -> Optional[SourceInfo]:
        """
        Get source info from the first provider that can handle the package.

        Args:
            package_info: Complete package information dictionary
            version: Package version string

        Returns:
            SourceInfo from the first capable provider, or None
        """
        for provider in self.providers:
            if provider.can_provide(package_info):
                source_info = provider.get_source_info(package_info, version)
                if source_info:
                    logger.debug(f"Using {provider.name()} provider for package")
                    return source_info
        return None

    def get_provider_for_package(self, package_info: Dict[str, Any]) -> Optional[SourceProviderBase]:
        """
        Get the provider that would handle a package.

        Args:
            package_info: Complete package information dictionary

        Returns:
            The provider that can handle the package, or None
        """
        for provider in self.providers:
            if provider.can_provide(package_info):
                return provider
        return None
