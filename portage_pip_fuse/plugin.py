"""
Ecosystem Plugin System for portage-pip-fuse.

This module provides the base plugin infrastructure for supporting multiple
package ecosystems (PyPI, RubyGems, etc.) through a unified FUSE filesystem.

The plugin system allows different package sources to share common infrastructure
(FUSE mechanics, caching, CLI framework) while implementing ecosystem-specific
logic for metadata extraction, ebuild generation, and name translation.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    TYPE_CHECKING,
)

if TYPE_CHECKING:
    from argparse import ArgumentParser, Namespace
    from .name_translator import NameTranslatorBase
    from .source_provider import SourceProviderBase

logger = logging.getLogger(__name__)


@dataclass
class EbuildTemplate:
    """
    Template data for generating an ebuild file.

    This dataclass contains all the information needed to generate
    an ebuild from package metadata.

    Attributes:
        eapi: EAPI version (typically "8")
        inherit: List of eclasses to inherit
        description: Package description
        homepage: Package homepage URL
        src_uri: Source URI for downloading
        license: License identifier
        slot: Package slot (typically "0")
        keywords: Architecture keywords
        iuse: USE flags
        required_use: Required USE flag combinations
        depend: Build dependencies
        rdepend: Runtime dependencies
        bdepend: Build-time dependencies
        pdepend: Post dependencies
        extra_variables: Additional ebuild variables (e.g., EGIT_REPO_URI)
        phases: Dict of phase functions (e.g., {'src_prepare': '...'})
        append_content: Additional content to append to ebuild
    """
    eapi: str = "8"
    inherit: List[str] = field(default_factory=list)
    description: str = ""
    homepage: str = ""
    src_uri: Optional[str] = None
    license: str = ""
    slot: str = "0"
    keywords: str = "~amd64 ~arm64"
    iuse: str = ""
    required_use: str = ""
    depend: str = ""
    rdepend: str = ""
    bdepend: str = ""
    pdepend: str = ""
    extra_variables: Dict[str, str] = field(default_factory=dict)
    phases: Dict[str, str] = field(default_factory=dict)
    append_content: str = ""


@dataclass
class PackageMetadata:
    """
    Normalized package metadata from any ecosystem.

    This dataclass provides a common structure for package information
    that can be used across different ecosystems.

    Attributes:
        name: Package name in the source ecosystem
        version: Package version string
        description: Package description/summary
        homepage: Project homepage URL
        license: License identifier(s)
        authors: List of author names/emails
        dependencies: List of (name, version_constraint) tuples
        dev_dependencies: Development/test dependencies
        optional_dependencies: Dict of extra/group name -> dependencies
        source_url: URL for source distribution
        source_hash: SHA256 hash of source distribution
        requires_interpreter: Interpreter version requirements (e.g., ">=3.8")
        classifiers: List of classifier strings (PyPI-style)
        project_urls: Dict of URL types -> URLs
        git_repo_url: Git repository URL if available
        native_extensions: Whether package has native extensions
        extras: Dict of extra metadata specific to the ecosystem
    """
    name: str
    version: str
    description: str = ""
    homepage: str = ""
    license: str = ""
    authors: List[str] = field(default_factory=list)
    dependencies: List[Tuple[str, str]] = field(default_factory=list)
    dev_dependencies: List[Tuple[str, str]] = field(default_factory=list)
    optional_dependencies: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    source_url: Optional[str] = None
    source_hash: Optional[str] = None
    requires_interpreter: Optional[str] = None
    classifiers: List[str] = field(default_factory=list)
    project_urls: Dict[str, str] = field(default_factory=dict)
    git_repo_url: Optional[str] = None
    native_extensions: bool = False
    extras: Dict[str, Any] = field(default_factory=dict)


class MetadataProviderBase(ABC):
    """
    Abstract base class for ecosystem-specific metadata providers.

    Each ecosystem (PyPI, RubyGems, etc.) implements this interface
    to provide package information from its respective registry.
    """

    @abstractmethod
    def get_package_info(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get complete package information.

        Args:
            name: Package name in the ecosystem's format

        Returns:
            Package information dictionary or None if not found
        """
        pass

    @abstractmethod
    def get_package_versions(self, name: str) -> List[str]:
        """
        Get list of available versions for a package.

        Args:
            name: Package name

        Returns:
            List of version strings, sorted newest first
        """
        pass

    @abstractmethod
    def get_version_info(self, name: str, version: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information for a specific version.

        Args:
            name: Package name
            version: Version string

        Returns:
            Version metadata dictionary or None if not found
        """
        pass

    @abstractmethod
    def list_packages(self) -> Set[str]:
        """
        List all available packages.

        Returns:
            Set of package names
        """
        pass

    def normalize_metadata(self, raw_metadata: Dict[str, Any]) -> PackageMetadata:
        """
        Convert raw ecosystem metadata to normalized PackageMetadata.

        Subclasses should override this to handle ecosystem-specific formats.

        Args:
            raw_metadata: Raw metadata from the ecosystem API

        Returns:
            Normalized PackageMetadata instance
        """
        return PackageMetadata(
            name=raw_metadata.get('name', ''),
            version=raw_metadata.get('version', ''),
        )


class EbuildGeneratorBase(ABC):
    """
    Abstract base class for ecosystem-specific ebuild generators.

    Each ecosystem implements this interface to generate valid Gentoo
    ebuilds from package metadata.
    """

    @abstractmethod
    def generate_ebuild(
        self,
        package_info: Dict[str, Any],
        version: str,
        gentoo_name: str
    ) -> str:
        """
        Generate ebuild content for a package version.

        Args:
            package_info: Complete package information
            version: Version to generate ebuild for
            gentoo_name: Translated Gentoo package name

        Returns:
            Complete ebuild file content as string
        """
        pass

    @abstractmethod
    def get_inherit_eclasses(self, package_info: Dict[str, Any]) -> List[str]:
        """
        Get list of eclasses to inherit.

        Args:
            package_info: Package information

        Returns:
            List of eclass names
        """
        pass

    @abstractmethod
    def get_compat_variable(self) -> str:
        """
        Get the compatibility variable name for this ecosystem.

        Returns:
            Variable name (e.g., "PYTHON_COMPAT" or "USE_RUBY")
        """
        pass

    @abstractmethod
    def generate_compat_declaration(self, package_info: Dict[str, Any]) -> str:
        """
        Generate the compatibility declaration.

        Args:
            package_info: Package information

        Returns:
            Compatibility declaration string (e.g., "PYTHON_COMPAT=( python3_11 )")
        """
        pass

    @abstractmethod
    def generate_dependencies(
        self,
        package_info: Dict[str, Any],
        version: str,
        dep_type: str = 'runtime'
    ) -> str:
        """
        Generate dependency declarations.

        Args:
            package_info: Package information
            version: Package version
            dep_type: Type of dependencies ('runtime', 'build', 'test')

        Returns:
            Formatted dependency string for ebuild
        """
        pass


class EcosystemPlugin(ABC):
    """
    Base class for ecosystem plugins.

    Each supported package ecosystem (PyPI, RubyGems, etc.) implements
    this interface to integrate with the portage-fuse system.

    Plugins define ecosystem-specific behavior while sharing common
    infrastructure for FUSE filesystem operations, caching, and CLI.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Get the ecosystem name.

        Returns:
            Human-readable ecosystem name (e.g., "pypi", "rubygems")
        """
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """
        Get the display name for the ecosystem.

        Returns:
            Display name (e.g., "PyPI", "RubyGems")
        """
        pass

    @property
    @abstractmethod
    def default_category(self) -> str:
        """
        Get the default Gentoo category for packages.

        Returns:
            Category name (e.g., "dev-python", "dev-ruby")
        """
        pass

    @property
    @abstractmethod
    def default_repo_location(self) -> str:
        """
        Get the default repository location.

        Returns:
            Default mountpoint path (e.g., "/var/db/repos/pypi")
        """
        pass

    @property
    @abstractmethod
    def repo_name(self) -> str:
        """
        Get the repository name for portage.

        Returns:
            Repository name (e.g., "portage-pypi-fuse")
        """
        pass

    @abstractmethod
    def get_metadata_provider(
        self,
        cache_dir: Optional[str] = None,
        cache_ttl: int = 3600
    ) -> MetadataProviderBase:
        """
        Get the metadata provider for this ecosystem.

        Args:
            cache_dir: Cache directory path
            cache_ttl: Cache time-to-live in seconds

        Returns:
            Configured metadata provider instance
        """
        pass

    @abstractmethod
    def get_ebuild_generator(self, **kwargs) -> EbuildGeneratorBase:
        """
        Get the ebuild generator for this ecosystem.

        Args:
            **kwargs: Additional configuration options

        Returns:
            Configured ebuild generator instance
        """
        pass

    @abstractmethod
    def get_name_translator(self) -> 'NameTranslatorBase':
        """
        Get the name translator for this ecosystem.

        Returns:
            Name translator for ecosystem <-> Gentoo name mapping
        """
        pass

    @abstractmethod
    def get_source_providers(self, **kwargs) -> List['SourceProviderBase']:
        """
        Get the source providers for this ecosystem.

        Args:
            **kwargs: Configuration options (e.g., enable_git)

        Returns:
            List of source providers in priority order
        """
        pass

    def get_version_filters(self) -> List[Any]:
        """
        Get default version filters for this ecosystem.

        Returns:
            List of version filter instances
        """
        return []

    def get_package_filters(self) -> List[Any]:
        """
        Get default package filters for this ecosystem.

        Returns:
            List of package filter instances
        """
        return []

    def register_cli_commands(self, parser: 'ArgumentParser') -> None:
        """
        Register ecosystem-specific CLI commands.

        Override this method to add custom subcommands for the ecosystem.

        Args:
            parser: ArgumentParser to add commands to
        """
        pass

    def get_cli_handler(self, command: str) -> Optional[Callable[['Namespace'], int]]:
        """
        Get handler for an ecosystem-specific CLI command.

        Args:
            command: Command name

        Returns:
            Handler function or None if not found
        """
        return None

    def get_static_dirs(self) -> Set[str]:
        """
        Get additional static directories for the filesystem.

        Returns:
            Set of directory paths to create
        """
        return {
            "/",
            f"/{self.default_category}",
            "/profiles",
            "/metadata",
            "/eclass",
        }

    def get_static_files(self) -> Dict[str, bytes]:
        """
        Get static file contents for the filesystem.

        Returns:
            Dict mapping file paths to content bytes
        """
        return {
            "/profiles/repo_name": (self.repo_name + "\n").encode('utf-8'),
            "/metadata/layout.conf": self._generate_layout_conf().encode('utf-8'),
        }

    def _generate_layout_conf(self) -> str:
        """Generate layout.conf for the overlay."""
        return f"""repo-name = {self.repo_name}
masters = gentoo
thin-manifests = true
profile-formats = portage-2
cache-formats = md5-dict
"""


class PluginRegistry:
    """
    Registry for ecosystem plugins.

    This class maintains a registry of available plugins and provides
    methods for discovery and instantiation.
    """

    _plugins: Dict[str, Type[EcosystemPlugin]] = {}
    _instances: Dict[str, EcosystemPlugin] = {}

    @classmethod
    def register(cls, name: str, plugin_class: Type[EcosystemPlugin]) -> None:
        """
        Register a plugin class.

        Args:
            name: Plugin name (e.g., "pypi", "rubygems")
            plugin_class: Plugin class to register
        """
        cls._plugins[name] = plugin_class
        logger.debug(f"Registered plugin: {name}")

    @classmethod
    def get(cls, name: str, **kwargs) -> Optional[EcosystemPlugin]:
        """
        Get a plugin instance by name.

        Args:
            name: Plugin name
            **kwargs: Arguments to pass to plugin constructor

        Returns:
            Plugin instance or None if not found
        """
        if name not in cls._plugins:
            return None

        # Return cached instance if exists and no kwargs provided
        if name in cls._instances and not kwargs:
            return cls._instances[name]

        # Create new instance
        instance = cls._plugins[name](**kwargs)
        if not kwargs:
            cls._instances[name] = instance
        return instance

    @classmethod
    def get_all(cls) -> Dict[str, Type[EcosystemPlugin]]:
        """
        Get all registered plugin classes.

        Returns:
            Dict of plugin name -> plugin class
        """
        return cls._plugins.copy()

    @classmethod
    def list_plugins(cls) -> List[str]:
        """
        List all registered plugin names.

        Returns:
            List of plugin names
        """
        return list(cls._plugins.keys())

    @classmethod
    def discover_plugins(cls) -> None:
        """
        Discover and register plugins from the ecosystems package.

        This method attempts to import all ecosystem modules and
        register any plugins they define.
        """
        try:
            from portage_pip_fuse import ecosystems
            import pkgutil

            for importer, modname, ispkg in pkgutil.iter_modules(ecosystems.__path__):
                if ispkg:
                    try:
                        # Import the plugin module from each ecosystem package
                        plugin_module = __import__(
                            f'portage_pip_fuse.ecosystems.{modname}.plugin',
                            fromlist=['plugin']
                        )
                        # The plugin module should auto-register via module-level code
                        logger.debug(f"Discovered ecosystem plugin: {modname}")
                    except ImportError as e:
                        logger.debug(f"Could not import ecosystem {modname}: {e}")
                    except Exception as e:
                        logger.warning(f"Error loading ecosystem {modname}: {e}")
        except ImportError:
            logger.debug("ecosystems package not found, skipping plugin discovery")


# Auto-discover plugins when module is imported
def _auto_discover():
    """Auto-discover plugins on module import."""
    PluginRegistry.discover_plugins()


# Defer auto-discovery to avoid import cycles
# Plugins will be discovered on first access
_plugins_discovered = False


def ensure_plugins_discovered():
    """Ensure plugins have been discovered."""
    global _plugins_discovered
    if not _plugins_discovered:
        _plugins_discovered = True
        PluginRegistry.discover_plugins()
