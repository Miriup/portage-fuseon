"""
Version filters for the npm ecosystem.

npm publishes roughly 3.5 million packages, an order of magnitude more than
PyPI, so filtering matters more here than in either existing ecosystem. Four
filters are provided, plus the ``os``/``cpu`` to KEYWORDS mapping used by ebuild
generation.

Two conventions are inherited from the RubyGems plugin deliberately:

- Filters are duck-typed rather than subclasses of
  :class:`portage_pip_fuse.version_filter.VersionFilterBase`, whose abstract
  methods name their first parameter ``pypi_name``. The shared
  :class:`~portage_pip_fuse.version_filter.VersionFilterChain` *is* reused,
  though, since it is genuinely ecosystem-neutral -- only a parameter name
  differs -- and a third copy of it would be pure duplication.
- Packages are not hidden for being unbuildable on this architecture. A
  platform-specific package gets narrow KEYWORDS instead, so the user sees
  "no KEYWORDS for your architecture" rather than a confusing build failure.

One filter departs from the usual per-version shape, and the reason matters.
``gentoo-version`` cannot decide in isolation whether a version is safe to
offer: npm compares an inline prerelease identifier such as ``beta25``
lexically, so it sorts *below* ``beta8``, while the PMS forms ``_beta25`` and
``_beta8`` compare numerically and swap. Order-safety is a property of the whole
version set, so the real work happens in ``filter_versions``, and
``should_include_version`` can only apply the weaker per-version test.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import logging
from typing import Any, Dict, List, Optional

from portage_pip_fuse.ecosystems.npm import node_targets, semver
from portage_pip_fuse.ecosystems.npm import version_translator

logger = logging.getLogger(__name__)

__all__ = [
    'DEFAULT_FILTERS',
    'OPTIONAL_FILTERS',
    'NodeCompatFilter',
    'GentooVersionFilter',
    'DeprecatedFilter',
    'HasBinFilter',
    'NpmVersionFilterRegistry',
    'os_cpu_to_keywords',
    'create_filter_chain',
]

#: Filters enabled unless explicitly disabled.
DEFAULT_FILTERS = ('gentoo-version', 'node-compat')

#: Filters available but off unless asked for.
OPTIONAL_FILTERS = ('deprecated', 'has-bin')


class NodeCompatFilter:
    """
    Filter versions by their declared ``engines.node`` range.

    The analogue of ``ruby-compat`` and ``python-compat``, but resolved against
    the Node versions actually installed rather than against a USE_EXPAND
    setting, because Node has no such variable.

    Permissive on missing or unparseable data: a package that declares no
    ``engines`` runs on any Node, and one that declares a range this code cannot
    parse is more likely to be usable than not. Hiding it would be the worse
    error.

    Examples:
        >>> f = NodeCompatFilter(node_versions=['22.22.2'])
        >>> f.should_include_version('pkg', '1.0.0',
        ...     {'engines': {'node': '>=18'}})
        True
        >>> f.should_include_version('pkg', '1.0.0',
        ...     {'engines': {'node': '>=99'}})
        False

        A version with no engines declaration is compatible with everything:

        >>> f.should_include_version('pkg', '1.0.0', {})
        True

        Real-world ranges with alternation work:

        >>> f.should_include_version('pkg', '1.0.0',
        ...     {'engines': {'node': '^12.0.0 || >= 14.0.0'}})
        True

        An old package requiring an ancient Node is excluded:

        >>> f.should_include_version('pkg', '1.0.0',
        ...     {'engines': {'node': '0.10.x'}})
        False
    """

    def __init__(self, node_versions: Optional[List[str]] = None):
        """
        Args:
            node_versions: Node versions to test against; detected from the
                system when omitted
        """
        self.node_versions = node_versions or node_targets.get_node_versions()

    @classmethod
    def get_filter_name(cls) -> str:
        return 'node-compat'

    def get_description(self) -> str:
        return 'Filters versions by engines.node against installed Node (%s)' % \
            ', '.join(self.node_versions)

    def filter_versions(
        self,
        name: str,
        versions_metadata: Dict[str, Dict],
    ) -> Dict[str, Dict]:
        return {
            version: metadata
            for version, metadata in versions_metadata.items()
            if self.should_include_version(name, version, metadata)
        }

    def should_include_version(
        self,
        name: str,
        version: str,
        metadata: Dict[str, Any],
    ) -> bool:
        engines = metadata.get('engines') or {}
        if not isinstance(engines, dict):
            return True

        requirement = engines.get('node')
        if not requirement or not isinstance(requirement, str):
            return True

        if not semver.is_range(requirement):
            logger.debug('Unparseable engines.node %r for %s-%s; including',
                         requirement, name, version)
            return True

        return any(semver.satisfies(node_version, requirement)
                   for node_version in self.node_versions)


class GentooVersionFilter:
    """
    Filter versions to those representable as PMS versions without reordering.

    Two distinct jobs, and only the first fits a per-version test:

    - the version must translate at all, which rejects commit-hash prereleases
      and dist-tag words like ``next`` or ``canary``; and
    - the surviving set must sort the same way under PMS as under npm.

    The second is why :meth:`filter_versions` does not simply loop over
    :meth:`should_include_version`. It delegates to
    :func:`version_translator.select_order_preserving`, which sees the whole
    list. Callers holding only one version get the weaker check and should treat
    it as necessary but not sufficient.

    Examples:
        >>> f = GentooVersionFilter()
        >>> sorted(f.filter_versions('pkg', {'1.0.0': {}, '1.1.0': {}}))
        ['1.0.0', '1.1.0']

        Untranslatable versions are dropped:

        >>> sorted(f.filter_versions('pkg', {'1.0.0': {}, '2.0.0-next.5': {}}))
        ['1.0.0']

        Where two spellings would invert the order, the npm-newer one wins:

        >>> sorted(f.filter_versions('pkg',
        ...     {'0.9.0-beta8': {}, '0.9.0-beta25': {}}))
        ['0.9.0-beta8']
    """

    @classmethod
    def get_filter_name(cls) -> str:
        return 'gentoo-version'

    def get_description(self) -> str:
        return 'Filters versions to those with order-preserving PMS equivalents'

    def filter_versions(
        self,
        name: str,
        versions_metadata: Dict[str, Dict],
    ) -> Dict[str, Dict]:
        kept = version_translator.select_order_preserving(
            list(versions_metadata))

        dropped = len(versions_metadata) - len(kept)
        if dropped:
            logger.debug('%s: dropped %d of %d versions as untranslatable or '
                         'order-inverting', name, dropped, len(versions_metadata))

        return {version: versions_metadata[version] for version in kept}

    def should_include_version(
        self,
        name: str,
        version: str,
        metadata: Dict[str, Any],
    ) -> bool:
        """
        Per-version translatability check.

        Necessary but not sufficient: order-safety needs the whole set, so a
        version passing this may still be dropped by :meth:`filter_versions`.
        """
        return version_translator.can_translate_version(version)


class DeprecatedFilter:
    """
    Exclude versions npm marks deprecated.

    Off by default. Deprecated npm versions still install and run, and
    deprecation is common enough that hiding it by default would make packages
    vanish for reasons the user never asked about -- the same reasoning that
    keeps platform-specific gems visible in the RubyGems plugin.

    Examples:
        >>> f = DeprecatedFilter(exclude_deprecated=True)
        >>> sorted(f.filter_versions('pkg', {
        ...     '1.0.0': {'deprecated': 'use 2.x'},
        ...     '2.0.0': {},
        ... }))
        ['2.0.0']

        Disabled, it is a no-op:

        >>> sorted(DeprecatedFilter().filter_versions('pkg', {
        ...     '1.0.0': {'deprecated': 'old'}, '2.0.0': {}}))
        ['1.0.0', '2.0.0']

        An empty deprecation string is not a deprecation:

        >>> f.should_include_version('pkg', '1.0.0', {'deprecated': ''})
        True
    """

    def __init__(self, exclude_deprecated: bool = False):
        """
        Args:
            exclude_deprecated: Drop deprecated versions when True
        """
        self.exclude_deprecated = exclude_deprecated

    @classmethod
    def get_filter_name(cls) -> str:
        return 'deprecated'

    def get_description(self) -> str:
        if self.exclude_deprecated:
            return 'Excludes versions marked deprecated on npm'
        return 'Includes deprecated versions'

    def filter_versions(
        self,
        name: str,
        versions_metadata: Dict[str, Dict],
    ) -> Dict[str, Dict]:
        if not self.exclude_deprecated:
            return versions_metadata
        return {
            version: metadata
            for version, metadata in versions_metadata.items()
            if self.should_include_version(name, version, metadata)
        }

    def should_include_version(
        self,
        name: str,
        version: str,
        metadata: Dict[str, Any],
    ) -> bool:
        if not self.exclude_deprecated:
            return True
        return not metadata.get('deprecated')


class HasBinFilter:
    """
    Restrict to versions that install a command.

    Off by default, and intended for browsing rather than for resolution: it
    reduces npm's millions of packages to the installable-tool subset, which is
    what a Gentoo user is usually looking for. Enabling it during dependency
    resolution would hide libraries that packages legitimately depend on.

    Examples:
        >>> f = HasBinFilter(require_bin=True)
        >>> sorted(f.filter_versions('pkg', {
        ...     '1.0.0': {'bin': {'tool': 'cli.js'}},
        ...     '2.0.0': {},
        ... }))
        ['1.0.0']

        A string ``bin`` counts too, since npm allows both shapes:

        >>> f.should_include_version('pkg', '1.0.0', {'bin': 'cli.js'})
        True
        >>> f.should_include_version('pkg', '1.0.0', {'bin': {}})
        False

        Disabled, it is a no-op:

        >>> sorted(HasBinFilter().filter_versions('pkg',
        ...     {'1.0.0': {}, '2.0.0': {}}))
        ['1.0.0', '2.0.0']
    """

    def __init__(self, require_bin: bool = False):
        """
        Args:
            require_bin: Keep only versions declaring a ``bin`` when True
        """
        self.require_bin = require_bin

    @classmethod
    def get_filter_name(cls) -> str:
        return 'has-bin'

    def get_description(self) -> str:
        if self.require_bin:
            return 'Restricts to packages installing a command'
        return 'Includes packages without commands'

    def filter_versions(
        self,
        name: str,
        versions_metadata: Dict[str, Dict],
    ) -> Dict[str, Dict]:
        if not self.require_bin:
            return versions_metadata
        return {
            version: metadata
            for version, metadata in versions_metadata.items()
            if self.should_include_version(name, version, metadata)
        }

    def should_include_version(
        self,
        name: str,
        version: str,
        metadata: Dict[str, Any],
    ) -> bool:
        if not self.require_bin:
            return True
        return bool(metadata.get('bin'))


#: npm ``os`` value to the Gentoo KEYWORDS fragment for each ``cpu``.
_OS_CPU_KEYWORDS = {
    ('linux', 'x64'): '~amd64',
    ('linux', 'arm64'): '~arm64',
    ('linux', 'ia32'): '~x86',
    ('linux', 'arm'): '~arm',
    ('linux', 'ppc64'): '~ppc64',
    ('darwin', 'x64'): '~x64-macos',
    ('darwin', 'arm64'): '~arm64-macos',
}

#: KEYWORDS for a package with no platform restrictions.
_PORTABLE_KEYWORDS = '~amd64 ~arm64'

#: Operating systems Gentoo has no keywords for. A package restricted to these
#: gets empty KEYWORDS: visible, so the user gets a clear message, but not
#: installable.
_UNSUPPORTED_OS = frozenset(('win32', 'sunos', 'aix', 'android'))


def os_cpu_to_keywords(
    os_values: Optional[List[str]] = None,
    cpu_values: Optional[List[str]] = None,
) -> str:
    """
    Map npm ``os`` and ``cpu`` restrictions to Gentoo KEYWORDS.

    Not a filter. Following the RubyGems plugin's philosophy, a package that
    cannot build here stays visible with empty KEYWORDS, so portage reports "no
    KEYWORDS for your architecture" instead of the package silently not
    existing, or failing mid-build.

    Both fields support negation with a ``!`` prefix, which npm defines as
    "anything except these".

    Args:
        os_values: npm ``os`` field
        cpu_values: npm ``cpu`` field

    Returns:
        A KEYWORDS string, possibly empty

    Examples:
        >>> os_cpu_to_keywords()
        '~amd64 ~arm64'
        >>> os_cpu_to_keywords(['linux'], ['x64'])
        '~amd64'
        >>> os_cpu_to_keywords(['linux'], ['arm64'])
        '~arm64'
        >>> os_cpu_to_keywords(['linux'])
        '~amd64 ~arm64'
        >>> os_cpu_to_keywords(['darwin'], ['arm64'])
        '~arm64-macos'
        >>> os_cpu_to_keywords(['linux', 'darwin'], ['x64'])
        '~amd64 ~x64-macos'

        Windows-only packages stay visible but uninstallable:

        >>> os_cpu_to_keywords(['win32'])
        ''
        >>> os_cpu_to_keywords(['win32'], ['x64'])
        ''

        Negation means "everything but":

        >>> os_cpu_to_keywords(['!win32'], ['x64'])
        '~amd64'
        >>> os_cpu_to_keywords(['linux'], ['!ia32'])
        '~amd64 ~arm64'
    """
    # Linux only by default. A package that states no 'os' restriction should
    # not be credited with macOS Prefix keywords it was never tested against;
    # darwin keywords are claimed only when the package asks for darwin.
    allowed_os = _resolve(os_values, ('linux',), _UNSUPPORTED_OS)
    allowed_cpu = _resolve(cpu_values, ('x64', 'arm64'), frozenset())

    if not allowed_os or not allowed_cpu:
        return ''

    keywords = []
    for operating_system in allowed_os:
        for cpu in allowed_cpu:
            keyword = _OS_CPU_KEYWORDS.get((operating_system, cpu))
            if keyword and keyword not in keywords:
                keywords.append(keyword)

    if not keywords:
        return ''

    return ' '.join(keywords)


def _resolve(values, defaults, unsupported) -> List[str]:
    """
    Resolve an npm ``os``/``cpu`` field to the values Gentoo cares about.

    An absent field means no restriction, so the defaults apply. A negated field
    means the defaults minus the exclusions.
    """
    if not values:
        return [value for value in defaults if value not in unsupported]

    negated = [value[1:] for value in values if value.startswith('!')]
    positive = [value for value in values if not value.startswith('!')]

    if positive:
        return [value for value in positive if value not in unsupported]

    return [value for value in defaults
            if value not in negated and value not in unsupported]


class NpmVersionFilterRegistry:
    """
    Registry of npm version filters.

    Kept separate from the shared
    :class:`~portage_pip_fuse.version_filter.VersionFilterRegistry` because that
    one is a single global namespace with no ecosystem partitioning, so two
    ecosystems wanting a filter of the same name would collide. RubyGems keeps
    its own registry for the same reason.

    Examples:
        >>> sorted(NpmVersionFilterRegistry.get_all_filters())
        ['deprecated', 'gentoo-version', 'has-bin', 'node-compat']
        >>> NpmVersionFilterRegistry.get_filter_class('node-compat') is NodeCompatFilter
        True
        >>> NpmVersionFilterRegistry.get_filter_class('nonexistent') is None
        True
    """

    _filters: Dict[str, type] = {}

    @classmethod
    def register(cls, name: str, filter_class: type) -> None:
        cls._filters[name] = filter_class

    @classmethod
    def get_filter_class(cls, name: str) -> Optional[type]:
        return cls._filters.get(name)

    @classmethod
    def get_all_filters(cls) -> Dict[str, type]:
        return cls._filters.copy()

    @classmethod
    def is_default(cls, name: str) -> bool:
        """Report whether a filter is enabled unless disabled."""
        return name in DEFAULT_FILTERS


NpmVersionFilterRegistry.register('node-compat', NodeCompatFilter)
NpmVersionFilterRegistry.register('gentoo-version', GentooVersionFilter)
NpmVersionFilterRegistry.register('deprecated', DeprecatedFilter)
NpmVersionFilterRegistry.register('has-bin', HasBinFilter)


def create_filter_chain(
    enabled_filters: Optional[List[str]] = None,
    disabled_filters: Optional[List[str]] = None,
    node_versions: Optional[List[str]] = None,
):
    """
    Build a filter chain from filter names.

    Reuses the shared
    :class:`~portage_pip_fuse.version_filter.VersionFilterChain`, which is
    ecosystem-neutral despite naming its first parameter ``pypi_name``.

    Args:
        enabled_filters: Filters to add on top of :data:`DEFAULT_FILTERS`
        disabled_filters: Filters to remove from the defaults
        node_versions: Passed to ``node-compat`` in place of detection

    Returns:
        A configured filter chain

    Raises:
        ValueError: if a named filter is not registered

    Examples:
        >>> chain = create_filter_chain(node_versions=['22.0.0'])
        >>> len(chain.filters)
        2
        >>> chain = create_filter_chain(disabled_filters=['node-compat'],
        ...                             node_versions=['22.0.0'])
        >>> [type(f).__name__ for f in chain.filters]
        ['GentooVersionFilter']
        >>> chain = create_filter_chain(enabled_filters=['has-bin'],
        ...                             node_versions=['22.0.0'])
        >>> sorted(type(f).__name__ for f in chain.filters)
        ['GentooVersionFilter', 'HasBinFilter', 'NodeCompatFilter']
    """
    from portage_pip_fuse.version_filter import VersionFilterChain

    names = list(DEFAULT_FILTERS)
    for name in enabled_filters or ():
        if name not in names:
            names.append(name)
    for name in disabled_filters or ():
        if name in names:
            names.remove(name)

    filters = []
    for name in names:
        filter_class = NpmVersionFilterRegistry.get_filter_class(name)
        if filter_class is None:
            raise ValueError('Unknown npm version filter: %r' % name)

        if filter_class is NodeCompatFilter:
            filters.append(filter_class(node_versions=node_versions))
        elif filter_class is DeprecatedFilter:
            filters.append(filter_class(exclude_deprecated=True))
        elif filter_class is HasBinFilter:
            filters.append(filter_class(require_bin=True))
        else:
            filters.append(filter_class())

    return VersionFilterChain(filters)
