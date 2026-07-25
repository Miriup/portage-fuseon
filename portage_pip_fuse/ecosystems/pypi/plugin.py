"""
PyPI ecosystem plugin implementation.

This module provides the EcosystemPlugin implementation for PyPI,
wrapping the existing portage-pip-fuse functionality.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Set, TYPE_CHECKING

from portage_pip_fuse.plugin import (
    EcosystemPlugin,
    EbuildGeneratorBase,
    MetadataProviderBase,
    PluginRegistry,
)

if TYPE_CHECKING:
    from argparse import ArgumentParser, Namespace
    from portage_pip_fuse.name_translator import NameTranslatorBase
    from portage_pip_fuse.source_provider import SourceProviderBase

logger = logging.getLogger(__name__)


class PyPIMetadataProvider(MetadataProviderBase):
    """
    Metadata provider for PyPI packages.

    This wraps the existing PyPIMetadataExtractor and HybridMetadataExtractor
    classes to provide the MetadataProviderBase interface.
    """

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        cache_ttl: int = 3600,
        use_sqlite: bool = True
    ):
        """
        Initialize the PyPI metadata provider.

        Args:
            cache_dir: Cache directory path
            cache_ttl: Cache time-to-live in seconds
            use_sqlite: Whether to use SQLite backend with API fallback
        """
        self.cache_dir = cache_dir
        self.cache_ttl = cache_ttl
        self.use_sqlite = use_sqlite
        self._extractor = None

    @property
    def extractor(self):
        """Lazy-initialize the metadata extractor."""
        if self._extractor is None:
            if self.use_sqlite:
                from portage_pip_fuse.hybrid_metadata import HybridMetadataExtractor
                self._extractor = HybridMetadataExtractor(
                    cache_ttl=self.cache_ttl,
                    cache_dir=self.cache_dir
                )
            else:
                from portage_pip_fuse.pip_metadata import PyPIMetadataExtractor
                self._extractor = PyPIMetadataExtractor(
                    cache_ttl=self.cache_ttl,
                    cache_dir=self.cache_dir
                )
        return self._extractor

    def get_package_info(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get complete package information from PyPI.

        Returns the raw PyPI JSON document, which is the de-facto internal
        metadata contract on the PyPI side: the SQLite backend converts its
        rows into this shape too.
        """
        return self.extractor.get_package_json(name)

    def get_package_versions(self, name: str) -> List[str]:
        """
        Get list of available versions for a package, newest first.

        The hybrid backend answers this directly; the plain JSON extractor has
        no such method, so the versions come from the package document.
        """
        getter = getattr(self.extractor, 'get_package_versions', None)
        if getter is not None:
            return getter(name)

        data = self.extractor.get_package_json(name)
        if not data:
            return []
        return list(data.get('releases', {}).keys())

    def get_version_info(self, name: str, version: str) -> Optional[Dict[str, Any]]:
        """Get detailed information for a specific version."""
        return self.extractor.get_complete_package_info(name, version)

    def list_packages(self) -> Set[str]:
        """
        List known packages.

        PyPI has no cheap full enumeration -- the simple index is a multi-
        megabyte scrape -- so, as on the RubyGems side, this reports the
        packages already in the local cache rather than every package on PyPI.
        """
        lister = getattr(self.extractor, '_list_cached_packages', None)
        if lister is None:
            # The hybrid extractor delegates to a JSON backend that has it.
            backend = getattr(self.extractor, 'json_backend', None)
            lister = getattr(backend, '_list_cached_packages', None)
        if lister is None:
            return set()
        return set(lister())

    def get_package_json(self, name: str) -> Optional[Dict[str, Any]]:
        """Get raw PyPI JSON for a package."""
        return self.extractor.get_package_json(name)


class PyPIEbuildGenerator(EbuildGeneratorBase):
    """
    Ebuild generator for PyPI packages.

    This wraps the existing EbuildDataExtractor to provide the
    EbuildGeneratorBase interface.
    """

    def __init__(self, cache_dir: Optional[str] = None, **kwargs):
        """
        Initialize the ebuild generator.

        Args:
            cache_dir: Cache directory path
            **kwargs: Additional configuration options
        """
        self.cache_dir = cache_dir
        self._extractor = None
        self.kwargs = kwargs

    @property
    def extractor(self):
        """Lazy-initialize the ebuild data extractor."""
        if self._extractor is None:
            from portage_pip_fuse.pip_metadata import EbuildDataExtractor
            self._extractor = EbuildDataExtractor(cache_dir=self.cache_dir)
        return self._extractor

    def generate_ebuild(
        self,
        package_info: Dict[str, Any],
        version: str,
        gentoo_name: str
    ) -> str:
        """
        Generate ebuild content for a package version.

        Not implemented on the PyPI side. Ebuild *formatting* still lives in
        PortagePipFS._format_ebuild rather than in this generator, so there is
        no method here to delegate to; prepare_ebuild_data() below exposes
        everything the formatter consumes.

        Raises:
            NotImplementedError: always, until formatting moves out of the
                filesystem layer
        """
        raise NotImplementedError(
            'PyPI ebuild formatting lives in PortagePipFS._format_ebuild, not '
            'in the plugin. Use prepare_ebuild_data() for the underlying data.'
        )

    def prepare_ebuild_data(
        self,
        package_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build the data dictionary the ebuild formatter consumes.

        Args:
            package_info: Complete package information

        Returns:
            Mapping with LICENSE, PYTHON_COMPAT, RDEPEND, BDEPEND and the
            source-selection flags
        """
        return self.extractor.prepare_ebuild_data(package_info)

    def get_inherit_eclasses(self, package_info: Dict[str, Any]) -> List[str]:
        """
        Get list of eclasses to inherit.

        Mirrors the three-way branch in PortagePipFS._format_ebuild: git
        sources use git-r3, wheels build with python-r1, and sdists use the
        pypi eclass.
        """
        data = self.extractor.prepare_ebuild_data(package_info)

        if data.get('use_git'):
            return ['distutils-r1', 'git-r3']
        if data.get('use_wheel'):
            return ['python-r1', 'pypi']
        return ['distutils-r1', 'pypi']

    def get_compat_variable(self) -> str:
        """Get the compatibility variable name."""
        return "PYTHON_COMPAT"

    def generate_compat_declaration(self, package_info: Dict[str, Any]) -> str:
        """Generate PYTHON_COMPAT declaration."""
        data = self.extractor.prepare_ebuild_data(package_info)
        return f"PYTHON_COMPAT=( {data.get('PYTHON_COMPAT', '')} )"

    def generate_dependencies(
        self,
        package_info: Dict[str, Any],
        version: str,
        dep_type: str = 'runtime'
    ) -> str:
        """
        Generate dependency declarations.

        Args:
            package_info: Complete package information
            version: Package version
            dep_type: One of 'runtime', 'build' or 'test'

        Returns:
            Dependency string; empty for 'test', which PyPI metadata does not
            express separately
        """
        data = self.extractor.prepare_ebuild_data(package_info)

        if dep_type == 'runtime':
            return data.get('RDEPEND', '')
        if dep_type == 'build':
            return data.get('BDEPEND', '')
        return ""


class PyPIPlugin(EcosystemPlugin):
    """
    PyPI ecosystem plugin.

    This plugin provides PyPI/pip integration for the portage-fuse system,
    enabling installation of Python packages through Portage.
    """

    @property
    def name(self) -> str:
        return "pypi"

    @property
    def display_name(self) -> str:
        return "PyPI"

    @property
    def default_category(self) -> str:
        return "dev-python"

    @property
    def default_repo_location(self) -> str:
        return "/var/db/repos/pypi"

    @property
    def repo_name(self) -> str:
        return "portage-pypi-fuse"

    def get_metadata_provider(
        self,
        cache_dir: Optional[str] = None,
        cache_ttl: int = 3600,
        use_sqlite: bool = True,
        **kwargs
    ) -> MetadataProviderBase:
        """Get the PyPI metadata provider."""
        return PyPIMetadataProvider(
            cache_dir=cache_dir,
            cache_ttl=cache_ttl,
            use_sqlite=use_sqlite
        )

    def get_ebuild_generator(self, **kwargs) -> EbuildGeneratorBase:
        """Get the Python ebuild generator."""
        return PyPIEbuildGenerator(**kwargs)

    def get_name_translator(self):
        """Get the PyPI -> Gentoo name translator."""
        from portage_pip_fuse.prefetcher import create_prefetched_translator
        return create_prefetched_translator()

    def get_source_providers(self, enable_git: bool = True, **kwargs) -> List['SourceProviderBase']:
        """Get the source providers for Python packages."""
        from portage_pip_fuse.source_provider import (
            SourceDistProvider,
            GitProvider,
            WheelProvider,
        )

        providers = [
            SourceDistProvider(),
            WheelProvider(),
        ]

        if enable_git:
            git_source_patch_store = kwargs.get('git_source_patch_store')
            providers.append(GitProvider(git_source_patch_store))

        # Sort by priority (highest first)
        providers.sort(key=lambda p: p.priority(), reverse=True)
        return providers

    def get_version_filters(self) -> List[Any]:
        """Get default version filters for Python packages."""
        from portage_pip_fuse.version_filter import (
            VersionFilterSourceDist,
            VersionFilterPythonCompat,
        )
        return [
            VersionFilterSourceDist(),
            VersionFilterPythonCompat(),
        ]

    def get_package_filters(self) -> List[Any]:
        """Get default package filters."""
        # Return empty list - filters are configured via CLI
        return []

    def register_cli_commands(self, parser: 'ArgumentParser') -> None:
        """Register pip-specific CLI commands."""
        # The 'pip' subcommand is already implemented in cli.py
        # This method could be used to add additional PyPI-specific commands
        pass

    def get_cli_handler(self, command: str) -> Optional[Callable[['Namespace'], int]]:
        """Get handler for a CLI command."""
        if command == 'pip':
            from portage_pip_fuse.cli import pip_command
            return lambda args: pip_command()
        return None

    def get_static_dirs(self) -> Set[str]:
        """Get static directories for PyPI filesystem."""
        dirs = super().get_static_dirs()
        # Add .sys virtual filesystem directories
        dirs.update({
            "/.sys",
            "/.sys/RDEPEND",
            "/.sys/RDEPEND/dev-python",
            "/.sys/RDEPEND-patch",
            "/.sys/RDEPEND-patch/dev-python",
            "/.sys/DEPEND",
            "/.sys/DEPEND/dev-python",
            "/.sys/DEPEND-patch",
            "/.sys/DEPEND-patch/dev-python",
            "/.sys/python-compat",
            "/.sys/python-compat/dev-python",
            "/.sys/python-compat-patch",
            "/.sys/python-compat-patch/dev-python",
            "/.sys/ebuild-append",
            "/.sys/ebuild-append/dev-python",
            "/.sys/ebuild-append-patch",
            "/.sys/ebuild-append-patch/dev-python",
            "/.sys/iuse",
            "/.sys/iuse/dev-python",
            "/.sys/iuse-patch",
            "/.sys/iuse-patch/dev-python",
            "/.sys/pep517",
            "/.sys/pep517/dev-python",
            "/.sys/pep517-patch",
            "/.sys/pep517-patch/dev-python",
            "/.sys/name-translation",
            "/.sys/git-source",
            "/.sys/git-source/dev-python",
            "/.sys/git-source-patch",
            "/.sys/git-source-patch/dev-python",
        })
        return dirs


# Register the plugin when the module is imported
PluginRegistry.register('pypi', PyPIPlugin)
