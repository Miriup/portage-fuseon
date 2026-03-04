"""
Source provider abstraction for determining how to fetch package source code.

This module provides an abstraction layer for different source types (sdist, git, wheel)
and a chain that tries providers in priority order.

Priority order: sdist (100) > git (75) > wheel (50)

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class SourceInfo:
    """
    Information about the source of a package.

    This dataclass encapsulates all the information needed to generate
    the appropriate ebuild structure for different source types.

    Attributes:
        provider_name: Name of the provider ('sdist', 'git', 'wheel')
        eclass_inherits: List of eclasses to inherit (e.g., ['distutils-r1', 'git-r3'])
        src_uri: Traditional SRC_URI for sdist/wheel
        git_repo_uri: Git repository URI for git-r3
        git_commit: Git commit/tag reference (e.g., 'v${PV}')
        extra_variables: Additional ebuild variables to set

    Examples:
        >>> info = SourceInfo(
        ...     provider_name='git',
        ...     eclass_inherits=['distutils-r1', 'git-r3'],
        ...     src_uri=None,
        ...     git_repo_uri='https://github.com/user/repo.git',
        ...     git_commit='v${PV}'
        ... )
        >>> info.provider_name
        'git'
        >>> 'git-r3' in info.eclass_inherits
        True
    """
    provider_name: str
    eclass_inherits: List[str] = field(default_factory=list)
    src_uri: Optional[str] = None
    git_repo_uri: Optional[str] = None
    git_commit: Optional[str] = None
    extra_variables: Dict[str, str] = field(default_factory=dict)

    def uses_git(self) -> bool:
        """Check if this source uses git.

        Examples:
            >>> info = SourceInfo(provider_name='git', git_repo_uri='https://github.com/user/repo.git')
            >>> info.uses_git()
            True
            >>> info2 = SourceInfo(provider_name='sdist', src_uri='https://pypi.org/...')
            >>> info2.uses_git()
            False
        """
        return self.git_repo_uri is not None


class SourceProviderBase(ABC):
    """
    Abstract base class for source providers.

    Each provider can check if it can provide source for a package
    and generate the appropriate SourceInfo.
    """

    @abstractmethod
    def name(self) -> str:
        """Get the provider name.

        Returns:
            Provider name string (e.g., 'sdist', 'git', 'wheel')
        """
        pass

    @abstractmethod
    def priority(self) -> int:
        """Get the provider priority.

        Higher values mean the provider is tried first.
        Typical values: sdist=100, git=75, wheel=50

        Returns:
            Integer priority value
        """
        pass

    @abstractmethod
    def can_provide(self, package_info: Dict[str, Any]) -> bool:
        """Check if this provider can provide source for the package.

        Args:
            package_info: Complete package information dictionary

        Returns:
            True if this provider can handle the package
        """
        pass

    @abstractmethod
    def get_source_info(self, package_info: Dict[str, Any], version: str) -> Optional[SourceInfo]:
        """Get source information for a package.

        Args:
            package_info: Complete package information dictionary
            version: Package version string

        Returns:
            SourceInfo if provider can handle the package, None otherwise
        """
        pass


class SourceDistProvider(SourceProviderBase):
    """
    Source provider for packages with source distributions (sdist).

    This is the preferred provider for Gentoo's build-from-source philosophy.
    """

    def name(self) -> str:
        """Get provider name.

        Examples:
            >>> provider = SourceDistProvider()
            >>> provider.name()
            'sdist'
        """
        return 'sdist'

    def priority(self) -> int:
        """Get priority (highest).

        Examples:
            >>> provider = SourceDistProvider()
            >>> provider.priority()
            100
        """
        return 100

    def can_provide(self, package_info: Dict[str, Any]) -> bool:
        """Check if package has a source distribution.

        Examples:
            >>> provider = SourceDistProvider()
            >>> provider.can_provide({'source_distribution': {'url': 'http://...'}})
            True
            >>> provider.can_provide({'source_distribution': None})
            False
            >>> provider.can_provide({})
            False
        """
        sdist = package_info.get('source_distribution')
        return sdist is not None and bool(sdist.get('url'))

    def get_source_info(self, package_info: Dict[str, Any], version: str) -> Optional[SourceInfo]:
        """Get source info for sdist-based package.

        Examples:
            >>> provider = SourceDistProvider()
            >>> info = provider.get_source_info(
            ...     {'source_distribution': {'url': 'https://pypi.org/pkg-1.0.tar.gz'}},
            ...     '1.0'
            ... )
            >>> info.provider_name
            'sdist'
            >>> info.uses_git()
            False
        """
        if not self.can_provide(package_info):
            return None

        sdist = package_info['source_distribution']
        return SourceInfo(
            provider_name='sdist',
            eclass_inherits=['distutils-r1', 'pypi'],
            src_uri=sdist.get('url'),
        )


class GitProvider(SourceProviderBase):
    """
    Source provider for packages with git repository URLs.

    This provider is used when a package has no sdist but has a known
    git repository URL in its project_urls metadata.
    """

    def __init__(self, git_source_patch_store=None):
        """Initialize the git provider.

        Args:
            git_source_patch_store: Optional patch store for manual overrides
        """
        self.git_source_patch_store = git_source_patch_store

    def name(self) -> str:
        """Get provider name.

        Examples:
            >>> provider = GitProvider()
            >>> provider.name()
            'git'
        """
        return 'git'

    def priority(self) -> int:
        """Get priority (between sdist and wheel).

        Examples:
            >>> provider = GitProvider()
            >>> provider.priority()
            75
        """
        return 75

    def can_provide(self, package_info: Dict[str, Any]) -> bool:
        """Check if package has a git repository URL.

        Examples:
            >>> provider = GitProvider()
            >>> provider.can_provide({'git_repo_url': 'https://github.com/user/repo'})
            True
            >>> provider.can_provide({'git_repo_url': None})
            False
            >>> provider.can_provide({})
            False
        """
        git_url = package_info.get('git_repo_url')
        return git_url is not None and bool(git_url)

    def get_source_info(self, package_info: Dict[str, Any], version: str) -> Optional[SourceInfo]:
        """Get source info for git-based package.

        Examples:
            >>> provider = GitProvider()
            >>> info = provider.get_source_info(
            ...     {'git_repo_url': 'https://github.com/user/repo'},
            ...     '1.0.0'
            ... )
            >>> info.provider_name
            'git'
            >>> info.git_repo_uri
            'https://github.com/user/repo.git'
            >>> info.git_commit
            'v${PV}'
            >>> info.uses_git()
            True
        """
        if not self.can_provide(package_info):
            return None

        from portage_pip_fuse.git_provider import normalize_git_url

        git_url = package_info['git_repo_url']
        normalized_url = normalize_git_url(git_url)

        return SourceInfo(
            provider_name='git',
            eclass_inherits=['distutils-r1', 'git-r3'],
            git_repo_uri=normalized_url,
            git_commit='v${PV}',  # Most common tag pattern
        )


class WheelProvider(SourceProviderBase):
    """
    Source provider for packages that only have wheel distributions.

    This is a fallback provider for pure-Python packages without sdist.
    """

    def name(self) -> str:
        """Get provider name.

        Examples:
            >>> provider = WheelProvider()
            >>> provider.name()
            'wheel'
        """
        return 'wheel'

    def priority(self) -> int:
        """Get priority (lowest).

        Examples:
            >>> provider = WheelProvider()
            >>> provider.priority()
            50
        """
        return 50

    def can_provide(self, package_info: Dict[str, Any]) -> bool:
        """Check if package has a wheel distribution.

        Examples:
            >>> provider = WheelProvider()
            >>> provider.can_provide({'wheel_distribution': {'url': 'http://...'}})
            True
            >>> provider.can_provide({'wheel_distribution': None})
            False
        """
        wheel = package_info.get('wheel_distribution')
        return wheel is not None and bool(wheel.get('url'))

    def get_source_info(self, package_info: Dict[str, Any], version: str) -> Optional[SourceInfo]:
        """Get source info for wheel-based package.

        Examples:
            >>> provider = WheelProvider()
            >>> info = provider.get_source_info(
            ...     {'wheel_distribution': {'url': 'https://pypi.org/pkg-1.0-py3-none-any.whl'}},
            ...     '1.0'
            ... )
            >>> info.provider_name
            'wheel'
            >>> info.uses_git()
            False
        """
        if not self.can_provide(package_info):
            return None

        wheel = package_info['wheel_distribution']
        return SourceInfo(
            provider_name='wheel',
            eclass_inherits=['python-r1'],
            src_uri=wheel.get('url'),
            extra_variables={
                'wheel_filename': wheel.get('filename', ''),
            }
        )


class SourceProviderChain:
    """
    Chain of source providers that tries them in priority order.

    The chain maintains a sorted list of providers and returns the first
    one that can handle the package.

    Examples:
        >>> chain = SourceProviderChain()
        >>> len(chain.providers)
        3
        >>> chain.providers[0].name()  # Highest priority first
        'sdist'
        >>> chain.providers[1].name()
        'git'
        >>> chain.providers[2].name()
        'wheel'
    """

    def __init__(self, providers: Optional[List[SourceProviderBase]] = None,
                 enable_git: bool = True, git_source_patch_store=None):
        """
        Initialize the provider chain.

        Args:
            providers: Optional list of providers (uses defaults if None)
            enable_git: Whether to enable the git provider (default True)
            git_source_patch_store: Optional patch store for git overrides
        """
        if providers is not None:
            self.providers = sorted(providers, key=lambda p: p.priority(), reverse=True)
        else:
            # Build default provider list
            default_providers = [
                SourceDistProvider(),
                WheelProvider(),
            ]
            if enable_git:
                default_providers.append(GitProvider(git_source_patch_store))
            self.providers = sorted(default_providers, key=lambda p: p.priority(), reverse=True)

    def get_source_info(self, package_info: Dict[str, Any], version: str) -> Optional[SourceInfo]:
        """
        Get source info from the first provider that can handle the package.

        Args:
            package_info: Complete package information dictionary
            version: Package version string

        Returns:
            SourceInfo from the first capable provider, or None if no provider can handle it

        Examples:
            >>> chain = SourceProviderChain()
            >>> # Sdist takes priority
            >>> info = chain.get_source_info({
            ...     'source_distribution': {'url': 'http://sdist.tar.gz'},
            ...     'git_repo_url': 'https://github.com/user/repo'
            ... }, '1.0')
            >>> info.provider_name
            'sdist'
            >>> # Falls back to git when no sdist
            >>> info = chain.get_source_info({
            ...     'source_distribution': None,
            ...     'git_repo_url': 'https://github.com/user/repo'
            ... }, '1.0')
            >>> info.provider_name
            'git'
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
