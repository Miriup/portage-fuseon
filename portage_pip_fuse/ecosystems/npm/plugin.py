"""
npm ecosystem plugin: metadata provider, ebuild generator and plugin class.

This is where everything else in the package meets portage. The generator emits
ebuilds for ``npm.eclass``, pinning each dependency to a concrete version chosen
by :mod:`.semver` -- one level deep, leaving the transitive walk to portage.

Two decisions here are load-bearing and easy to get subtly wrong:

**Pins must reference versions the overlay actually offers.** Resolution runs
against the dependency's *filtered* version list, not its raw one. Picking the
highest semver match without filtering would routinely pin to a nightly or a
commit-hash prerelease that ``gentoo-version`` then hides, producing an ebuild
whose RDEPEND names something that does not exist in the tree.

**Two version spellings coexist and must not be mixed.** ``NPM_DEPS`` and the
store paths use *upstream* npm versions, because that is what the tarballs and
the eclass's symlink targets are keyed on; RDEPEND atoms use *PMS* versions,
because that is what portage compares. ``1.0.0-beta.1`` and ``1.0.0_beta1`` are
the same release wearing different clothes.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from portage_pip_fuse import gentoo_license
# HTTP_TIMEOUT is a (connect, read) tuple for requests; urllib needs a
# scalar, so the read timeout is used directly.
from portage_pip_fuse.constants import HTTP_READ_TIMEOUT, find_cache_dir
from portage_pip_fuse.json_cache import JSONCache
from portage_pip_fuse.plugin import (
    EcosystemPlugin,
    EbuildGeneratorBase,
    MetadataProviderBase,
    PluginRegistry,
)

from . import filters as npm_filters
from . import name_translator, semver, version_translator

if TYPE_CHECKING:
    from argparse import ArgumentParser, Namespace

logger = logging.getLogger(__name__)

__all__ = [
    'NpmMetadataProvider',
    'NpmEbuildGenerator',
    'NpmPlugin',
]

#: Default Gentoo category. ::gentoo has no such category, so the overlay must
#: publish it in profiles/categories -- see NpmPlugin.get_static_files.
DEFAULT_CATEGORY = 'dev-nodejs'

#: Tarball sizes and digests never change for a published version, so they are
#: cached effectively forever rather than on the metadata TTL.
IMMUTABLE_CACHE_TTL = 10 * 365 * 24 * 3600


class NpmMetadataProvider(MetadataProviderBase):
    """
    Fetch package metadata from an npm registry.

    Uses the abbreviated packument (``application/vnd.npm.install-v1+json``),
    which carries everything needed -- versions, dependencies, engines, os, cpu,
    bin, dist -- at a fraction of the full document's size.

    One thing the packument does *not* carry is the tarball's byte size, which a
    Manifest DIST line requires. :meth:`get_tarball_size` recovers it with a
    one-byte ranged request and caches the answer permanently.
    """

    REGISTRY_BASE = 'https://registry.npmjs.org'
    ABBREVIATED_ACCEPT = 'application/vnd.npm.install-v1+json'
    USER_AGENT = 'portage-npm-fuse/0.1.0'

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        cache_ttl: int = 3600,
        registry: Optional[str] = None,
    ):
        """
        Args:
            cache_dir: Cache directory; discovered via find_cache_dir when unset
            cache_ttl: Seconds a packument stays fresh
            registry: Registry base URL, for mirrors or a private registry
        """
        self.registry = (registry or self.REGISTRY_BASE).rstrip('/')
        self.cache_ttl = cache_ttl

        base = find_cache_dir(cache_dir)
        self.cache_dir = base / 'npm' if hasattr(base, '__truediv__') else base
        self._packuments = JSONCache(self.cache_dir, ttl=cache_ttl)
        self._manifests = JSONCache(self.cache_dir / 'manifests',
                                    ttl=IMMUTABLE_CACHE_TTL)
        self._sizes = JSONCache(self.cache_dir / 'sizes',
                                ttl=IMMUTABLE_CACHE_TTL)

        logger.info('npm metadata cache initialised at %s', self.cache_dir)

    # -- registry access ------------------------------------------------------

    def _fetch(self, path: str) -> Optional[Any]:
        """GET a package path as an abbreviated packument."""
        # A scoped name's '/' must survive as %2f, or the registry reads it as a
        # path separator and returns the scope rather than the package.
        return self._get(urllib.parse.quote(path, safe=''),
                         accept=self.ABBREVIATED_ACCEPT)

    def _fetch_raw(self, quoted_path: str) -> Optional[Any]:
        """GET an already-quoted path without asking for the abbreviated form."""
        return self._get(quoted_path, accept='application/json')

    def _get(self, quoted_path: str, accept: str) -> Optional[Any]:
        """GET a registry path, returning parsed JSON or None."""
        url = '%s/%s' % (self.registry, quoted_path)
        request = urllib.request.Request(url, headers={
            'Accept': accept,
            'User-Agent': self.USER_AGENT,
        })

        try:
            with urllib.request.urlopen(request, timeout=HTTP_READ_TIMEOUT) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                logger.debug('npm registry has no %s', quoted_path)
            else:
                logger.warning('npm registry error %s for %s', exc.code, quoted_path)
            return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.error('Failed to fetch %s: %s', url, exc)
            return None

    # -- MetadataProviderBase -------------------------------------------------

    def get_package_info(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get the abbreviated packument for a package.

        Args:
            name: npm package name, possibly scoped

        Returns:
            Packument dictionary, or None if the package does not exist
        """
        cached = self._packuments.get(name)
        if cached is not None:
            return cached or None

        document = self._fetch(name)
        if document is None:
            # Cache the negative result too; a missing package stays missing for
            # at least the TTL, and FUSE listings retry aggressively.
            self._packuments.set(name, {})
            return None

        self._packuments.set(name, document)
        return document

    def get_package_versions(self, name: str) -> List[str]:
        """
        Get published versions, newest first.

        Args:
            name: npm package name

        Returns:
            Upstream version strings, newest first; empty if unknown
        """
        document = self.get_package_info(name)
        if not document:
            return []

        versions = list((document.get('versions') or {}).keys())
        parsed = [(semver.parse_version(v), v) for v in versions]
        parsed = [(p, v) for p, v in parsed if p is not None]
        parsed.sort(key=lambda item: item[0], reverse=True)
        return [v for _p, v in parsed]

    def get_version_info(self, name: str, version: str) -> Optional[Dict[str, Any]]:
        """
        Get one version's manifest.

        Args:
            name: npm package name
            version: Upstream version string

        Returns:
            Manifest dictionary, or None if unknown
        """
        document = self.get_package_info(name)
        if not document:
            return None
        return (document.get('versions') or {}).get(version)

    def list_packages(self) -> Set[str]:
        """
        List known packages.

        npm has no cheap full enumeration -- the replication endpoint is
        millions of documents -- so this reports what is already cached, as the
        RubyGems and PyPI providers do.
        """
        # Release documents live in a separate cache directory, so nothing here
        # is version-keyed; and a scoped name's sanitised '/' looks like one.
        return set(self._packuments.list_cached(exclude_versioned=False))

    # -- npm-specific ---------------------------------------------------------

    def get_versions_metadata(self, name: str) -> Dict[str, Dict[str, Any]]:
        """
        Get every version's manifest, keyed by version.

        This is the shape the version filters consume.

        Args:
            name: npm package name

        Returns:
            Mapping of version to manifest
        """
        document = self.get_package_info(name)
        if not document:
            return {}
        return dict(document.get('versions') or {})

    def get_full_version_info(
        self,
        name: str,
        version: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a version's *full* manifest.

        The abbreviated packument omits ``description`` and ``license``, so
        ebuild generation cannot use it: every package would come out described
        by its own name and licensed all-rights-reserved. The single-version
        endpoint returns the complete manifest and is small -- a few kilobytes --
        so it is fetched separately and cached alongside the packument.

        Args:
            name: npm package name
            version: Upstream version string

        Returns:
            Full manifest, or the abbreviated one as a fallback
        """
        cache_key = '%s/%s' % (name, version)
        cached = self._manifests.get(cache_key)
        if cached is not None:
            return cached or self.get_version_info(name, version)

        quoted = '%s/%s' % (urllib.parse.quote(name, safe=''),
                            urllib.parse.quote(version, safe=''))
        document = self._fetch_raw(quoted)
        if document is None:
            self._manifests.set(cache_key, {})
            return self.get_version_info(name, version)

        self._manifests.set(cache_key, document)
        return document

    def get_dist_tags(self, name: str) -> Dict[str, str]:
        """Get the package's dist-tags, e.g. ``{'latest': '1.2.3'}``."""
        document = self.get_package_info(name)
        if not document:
            return {}
        return dict(document.get('dist-tags') or {})

    def get_tarball_size(self, name: str, version: str) -> Optional[int]:
        """
        Get the tarball's size in bytes.

        A Manifest DIST line needs the size, and the packument does not publish
        it, so this issues a one-byte ranged request and reads the total from the
        ``Content-Range`` header. Published tarballs are immutable, so the answer
        is cached permanently -- without that, generating a Manifest would cost
        one request per version every time.

        Args:
            name: npm package name
            version: Upstream version string

        Returns:
            Size in bytes, or None if it could not be determined
        """
        cache_key = '%s@%s' % (name, version)
        cached = self._sizes.get(cache_key)
        if isinstance(cached, dict) and 'size' in cached:
            return cached['size']

        manifest = self.get_version_info(name, version)
        if not manifest:
            return None

        tarball = (manifest.get('dist') or {}).get('tarball')
        if not tarball:
            return None

        size = self._probe_size(tarball)
        if size is not None:
            self._sizes.set(cache_key, {'size': size})
        return size

    @staticmethod
    def _probe_size(tarball: str) -> Optional[int]:
        """Read a tarball's length from a one-byte ranged request."""
        request = urllib.request.Request(tarball, headers={'Range': 'bytes=0-0'})
        try:
            with urllib.request.urlopen(request, timeout=HTTP_READ_TIMEOUT) as response:
                content_range = response.headers.get('Content-Range')
                if content_range and '/' in content_range:
                    total = content_range.rsplit('/', 1)[1].strip()
                    if total.isdigit():
                        return int(total)
                # Some mirrors ignore Range and send the whole body.
                length = response.headers.get('Content-Length')
                if length and length.isdigit():
                    return int(length)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.debug('Could not size %s: %s', tarball, exc)
        return None


def integrity_to_hex(integrity: str) -> Optional[Tuple[str, str]]:
    """
    Convert an npm ``dist.integrity`` value to a Manifest-ready digest.

    npm encodes Subresource Integrity, ``<algorithm>-<base64>``; Manifest wants
    an uppercase algorithm name and a hex digest.

    Args:
        integrity: An SRI string, e.g. ``sha512-oKnbhFyR...``

    Returns:
        ``(ALGORITHM, hexdigest)``, or None if unparseable

    Examples:
        >>> integrity_to_hex('sha512-' + __import__('base64').b64encode(
        ...     bytes(range(64))).decode())[0]
        'SHA512'
        >>> len(integrity_to_hex('sha512-' + __import__('base64').b64encode(
        ...     bytes(range(64))).decode())[1])
        128
        >>> integrity_to_hex('not-an-integrity') is None
        True
        >>> integrity_to_hex('') is None
        True
    """
    if not integrity or '-' not in integrity:
        return None

    algorithm, _, encoded = integrity.partition('-')
    if algorithm.lower() not in ('sha1', 'sha256', 'sha384', 'sha512'):
        return None

    try:
        digest = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return None

    if not digest:
        return None

    return algorithm.upper(), digest.hex()


class NpmEbuildGenerator(EbuildGeneratorBase):
    """
    Generate ebuilds for ``npm.eclass``.

    The generator resolves each dependency range to a concrete version, so the
    ebuild carries exact pins and portage does the transitive work. Resolution
    consults the dependency's *filtered* version list, so a pin can never name a
    version the overlay hides.
    """

    def __init__(
        self,
        metadata_provider: Optional[NpmMetadataProvider] = None,
        category: str = DEFAULT_CATEGORY,
        node_dep: str = '>=net-libs/nodejs-18',
        translator: Optional[name_translator.NpmNameTranslator] = None,
        version_filter_chain: Optional[Any] = None,
        include_optional: bool = False,
    ):
        """
        Args:
            metadata_provider: Used to look up dependency version lists; without
                one, dependencies degrade to unversioned atoms
            category: Gentoo category for generated atoms
            node_dep: Runtime dependency atom for Node
            translator: Name translator; a fresh one is created when omitted
            version_filter_chain: Applied to a dependency's versions before
                pinning, so pins match what the overlay exposes
            include_optional: Include optionalDependencies in the pins. Off by
                default: npm treats them as best-effort, and a hard portage
                dependency on one turns an optional feature into a build failure
        """
        self.metadata_provider = metadata_provider
        self.category = category
        self.node_dep = node_dep
        self.translator = translator or name_translator.NpmNameTranslator()
        self.include_optional = include_optional

        if version_filter_chain is None and metadata_provider is not None:
            version_filter_chain = npm_filters.create_filter_chain()
        self.version_filter_chain = version_filter_chain

    # -- EbuildGeneratorBase --------------------------------------------------

    def get_inherit_eclasses(self, package_info: Dict[str, Any]) -> List[str]:
        """
        Get the eclasses to inherit.

        Always just ``npm``: the eclass handles unpack, store layout, symlinks
        and wrappers, and nothing in the manifest changes that.
        """
        return ['npm']

    def get_compat_variable(self) -> str:
        """
        Get the runtime-compatibility variable name.

        Node has no interpreter-target variable to parallel ``PYTHON_COMPAT`` or
        ``USE_RUBY``, because ``net-libs/nodejs`` is one slotted package rather
        than a family. The nearest equivalent is the eclass's Node dependency
        atom.
        """
        return 'NPM_NODE_DEP'

    def generate_compat_declaration(self, package_info: Dict[str, Any]) -> str:
        """
        Generate the Node dependency declaration.

        Honours the package's own ``engines.node`` when it implies a floor above
        the default, so a package needing Node 20 does not silently install
        against Node 18.
        """
        requirement = self._node_floor(package_info)
        return '%s="%s"' % (self.get_compat_variable(), requirement)

    def _node_floor(self, manifest: Dict[str, Any]) -> str:
        """
        Derive a Node dependency atom from ``engines.node``.

        The floor comes from the declared range, not from whatever Node is
        installed on the machine generating the ebuild. An earlier version took
        the lowest *installed* satisfying version, which made a package
        declaring '>=10' claim it needed the build host's Node 22.
        """
        engines = manifest.get('engines')
        if not isinstance(engines, dict):
            return self.node_dep

        requirement = engines.get('node')
        if not requirement or not isinstance(requirement, str):
            return self.node_dep

        major = semver.min_satisfying_major(requirement)
        if major is None:
            return self.node_dep

        # Gentoo carries no ancient Node, so a floor below the configured
        # default is not worth expressing and would only look odd.
        default_major = self._default_major()
        if default_major is not None and major <= default_major:
            return self.node_dep

        return '>=net-libs/nodejs-%d' % major

    def _default_major(self) -> Optional[int]:
        """Extract the major version from the configured default Node atom."""
        digits = ''.join(
            character for character in self.node_dep.rpartition('-')[2]
            if character.isdigit() or character == '.'
        )
        try:
            return int(digits.split('.')[0])
        except (ValueError, IndexError):
            return None

    def generate_dependencies(
        self,
        package_info: Dict[str, Any],
        version: str,
        dep_type: str = 'runtime',
    ) -> str:
        """
        Generate a dependency declaration.

        Args:
            package_info: The version's manifest
            version: Upstream version string
            dep_type: ``runtime`` or ``build``; ``test`` yields nothing, since
                npm's devDependencies are not run by the ebuild

        Returns:
            Newline-separated atoms, possibly empty
        """
        if dep_type not in ('runtime', 'build'):
            return ''
        node_atom = self._node_floor(package_info)
        if dep_type == 'build':
            # The eclass needs Node itself to read package.json; nothing else.
            return node_atom

        pins, _unresolved = self.resolve_dependencies(package_info)
        atoms = [self._atom(npm_name, npm_version)
                 for npm_name, npm_version in pins]
        atoms = [atom for atom in atoms if atom]
        return '\n\t'.join([node_atom] + atoms)

    # -- resolution -----------------------------------------------------------

    def collect_requirements(
        self,
        manifest: Dict[str, Any],
    ) -> Dict[str, str]:
        """
        Collect the dependency ranges an ebuild must satisfy.

        ``dependencies`` always; non-optional ``peerDependencies`` too, because
        the package will not work without them and portage has no concept of a
        peer; ``optionalDependencies`` only when asked for.

        ``devDependencies`` are always excluded: npm packages ship built, so the
        ebuild never runs the package's own build or test tooling.

        Args:
            manifest: A version's manifest

        Returns:
            Mapping of npm package name to range

        Examples:
            >>> generator = NpmEbuildGenerator()
            >>> generator.collect_requirements({
            ...     'dependencies': {'chalk': '^4.0.0'},
            ...     'devDependencies': {'jest': '^29.0.0'},
            ... })
            {'chalk': '^4.0.0'}

            Optional peers are skipped, required peers are kept:

            >>> sorted(generator.collect_requirements({
            ...     'peerDependencies': {'react': '^18.0.0', 'less': '^4.0.0'},
            ...     'peerDependenciesMeta': {'less': {'optional': True}},
            ... }))
            ['react']
        """
        requirements: Dict[str, str] = {}

        for name, spec in (manifest.get('dependencies') or {}).items():
            requirements[name] = spec

        peer_meta = manifest.get('peerDependenciesMeta') or {}
        for name, spec in (manifest.get('peerDependencies') or {}).items():
            if (peer_meta.get(name) or {}).get('optional'):
                continue
            requirements.setdefault(name, spec)

        if self.include_optional:
            for name, spec in (manifest.get('optionalDependencies') or {}).items():
                requirements.setdefault(name, spec)

        return requirements

    def resolve_dependencies(
        self,
        manifest: Dict[str, Any],
    ) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
        """
        Resolve dependency ranges to concrete upstream versions.

        Args:
            manifest: A version's manifest

        Returns:
            ``(pins, unresolved)`` where pins are ``(npm_name, npm_version)``
            pairs sorted by name, and unresolved are ``(npm_name, spec)`` pairs
            that could not be pinned -- dist-tags, git or file specifiers, or
            ranges no published version satisfies
        """
        pins: List[Tuple[str, str]] = []
        unresolved: List[Tuple[str, str]] = []

        for npm_name, spec in sorted(self.collect_requirements(manifest).items()):
            resolved = self.resolve_one(npm_name, spec)
            if resolved is None:
                unresolved.append((npm_name, spec))
            else:
                pins.append((npm_name, resolved))

        return pins, unresolved

    def resolve_one(self, npm_name: str, spec: str) -> Optional[str]:
        """
        Resolve one dependency range to a concrete version.

        Candidate versions are filtered before selection, so the pin always
        names a version the overlay actually offers. Selecting first and
        filtering afterwards would routinely pin to nightly builds that
        ``gentoo-version`` then hides.

        Args:
            npm_name: Dependency name
            spec: Version range, or a specifier this cannot handle

        Returns:
            Upstream version string, or None when unresolvable
        """
        if not semver.is_range(spec):
            # A dist-tag, git URL, file: path or workspace: protocol.
            return None
        if self.metadata_provider is None:
            return None

        versions_metadata = self.metadata_provider.get_versions_metadata(npm_name)
        if not versions_metadata:
            return None

        if self.version_filter_chain is not None:
            versions_metadata = self.version_filter_chain.filter_versions(
                npm_name, versions_metadata)

        return semver.max_satisfying(list(versions_metadata), spec)

    def _atom(self, npm_name: str, npm_version: str) -> Optional[str]:
        """
        Build a portage atom pinning one dependency.

        Uses ``~`` rather than ``=`` so a revision bump on the dependency still
        satisfies it, matching what the RubyGems generator does.
        """
        gentoo_name = self.translator.npm_to_gentoo(npm_name)
        pms_version = version_translator.translate_version(npm_version)
        if gentoo_name is None or pms_version is None:
            logger.debug('Cannot express dependency %s@%s as an atom',
                         npm_name, npm_version)
            return None
        return '~%s/%s-%s' % (self.category, gentoo_name, pms_version)

    # -- ebuild text ----------------------------------------------------------

    def generate_ebuild(
        self,
        package_info: Dict[str, Any],
        version: str,
        gentoo_name: str,
    ) -> str:
        """
        Generate the full ebuild text for one package version.

        Args:
            package_info: The version's manifest
            version: Upstream version string
            gentoo_name: Translated Gentoo package name

        Returns:
            Complete ebuild content
        """
        npm_name = package_info.get('name') or gentoo_name
        pms_version = version_translator.translate_version(version)
        if pms_version is None:
            raise ValueError('version %r has no PMS equivalent' % version)

        pins, unresolved = self.resolve_dependencies(package_info)

        npm_deps = ' '.join('%s@%s' % (name, ver) for name, ver in pins)
        rdepend_atoms = [atom for atom in
                         (self._atom(name, ver) for name, ver in pins) if atom]

        keywords = npm_filters.os_cpu_to_keywords(
            package_info.get('os'), package_info.get('cpu'))

        lines = [
            '# Copyright 1999-2026 Gentoo Authors',
            '# Distributed under the terms of the GNU General Public License v2',
            '',
            'EAPI=8',
            '',
            'NPM_PN="%s"' % npm_name,
        ]

        # NPM_PV is only needed when the two spellings diverge; the eclass
        # defaults it to PV.
        if version != pms_version:
            lines.append('NPM_PV="%s"' % version)

        if npm_deps:
            lines.append('NPM_DEPS="%s"' % npm_deps)

        lines.extend([
            '',
            'inherit npm',
            '',
            'DESCRIPTION="%s"' % self._escape(
                package_info.get('description') or npm_name),
            'HOMEPAGE="%s"' % self._homepage(package_info, npm_name),
            '',
            'LICENSE="%s"' % self._license(package_info),
            'KEYWORDS="%s"' % keywords,
        ])

        node_atom = self._node_floor(package_info)
        if node_atom != self.node_dep:
            # Only stated when the package needs more than the eclass default.
            lines.insert(lines.index('inherit npm') - 1,
                         'NPM_NODE_DEP="%s"' % node_atom)

        if rdepend_atoms:
            lines.extend(['', 'RDEPEND="%s' % node_atom])
            for atom in rdepend_atoms:
                lines.append('\t%s' % atom)
            lines.append('"')

        if unresolved:
            lines.append('')
            lines.append('# Unresolved dependencies, declared with specifiers')
            lines.append('# portage cannot express:')
            for name, spec in unresolved:
                lines.append('#   %s: %s' % (name, spec))

        return '\n'.join(lines) + '\n'

    def generate_manifest_entry(
        self,
        package_info: Dict[str, Any],
        version: str,
        gentoo_name: str,
        size: Optional[int] = None,
    ) -> Optional[str]:
        """
        Generate a Manifest DIST line for a version's tarball.

        The filename matches the ``-> ${P}.tgz`` rename the eclass performs,
        which exists because scoped packages publish a scope-less basename and
        would otherwise collide in DISTDIR.

        Args:
            package_info: The version's manifest
            version: Upstream version string
            gentoo_name: Translated Gentoo package name
            size: Tarball size; looked up via the provider when omitted

        Returns:
            A DIST line, or None if size or digest is unavailable
        """
        dist = package_info.get('dist') or {}
        digest = integrity_to_hex(dist.get('integrity') or '')
        pms_version = version_translator.translate_version(version)

        if pms_version is None:
            return None

        if size is None and self.metadata_provider is not None:
            npm_name = package_info.get('name') or gentoo_name
            size = self.metadata_provider.get_tarball_size(npm_name, version)

        if size is None:
            logger.debug('No size for %s-%s; omitting Manifest entry',
                         gentoo_name, version)
            return None

        filename = '%s-%s.tgz' % (gentoo_name, pms_version)
        parts = ['DIST', filename, str(size)]

        if digest is not None:
            parts.extend(digest)
        elif dist.get('shasum'):
            # Pre-2017 packages predate integrity and carry only a sha1.
            parts.extend(['SHA1', dist['shasum']])
        else:
            return None

        return ' '.join(parts)

    # -- helpers --------------------------------------------------------------

    def _license(self, manifest: Dict[str, Any]) -> str:
        """Translate the manifest's license metadata to a Gentoo LICENSE."""
        declared = manifest.get('license')
        if isinstance(declared, str):
            return gentoo_license.translate(declared)

        # An object form, {"type": "MIT", ...}, appears in older packages.
        if isinstance(declared, dict) and declared.get('type'):
            return gentoo_license.translate(declared['type'])

        # The legacy plural field is a list, possibly of objects.
        legacy = manifest.get('licenses')
        if isinstance(legacy, list):
            names = []
            for entry in legacy:
                if isinstance(entry, str):
                    names.append(entry)
                elif isinstance(entry, dict) and entry.get('type'):
                    names.append(entry['type'])
            if names:
                return gentoo_license.translate_list(names)

        return gentoo_license.UNKNOWN_LICENSE

    @staticmethod
    def _homepage(manifest: Dict[str, Any], npm_name: str) -> str:
        """Pick a homepage, defaulting to the package's registry page."""
        homepage = manifest.get('homepage')
        if isinstance(homepage, str) and homepage.startswith(('http://', 'https://')):
            return NpmEbuildGenerator._escape(homepage)
        return 'https://www.npmjs.com/package/%s' % npm_name

    @staticmethod
    def _escape(text: str) -> str:
        """
        Escape a string for a double-quoted bash assignment.

        Backslashes first, or the escapes added afterwards get double-escaped.

        Examples:
            >>> NpmEbuildGenerator._escape('a "quoted" word')
            'a \\\\"quoted\\\\" word'
            >>> NpmEbuildGenerator._escape('cost $5 `now`')
            'cost \\\\$5 \\\\`now\\\\`'
        """
        text = str(text).replace('\\', '\\\\')
        for character in ('"', '$', '`'):
            text = text.replace(character, '\\' + character)
        return ' '.join(text.split())


class NpmPlugin(EcosystemPlugin):
    """
    npm ecosystem plugin.

    Examples:
        >>> plugin = NpmPlugin()
        >>> plugin.name
        'npm'
        >>> plugin.default_category
        'dev-nodejs'
        >>> plugin.repo_name
        'portage-npm-fuse'
    """

    @property
    def name(self) -> str:
        return 'npm'

    @property
    def display_name(self) -> str:
        return 'npm'

    @property
    def default_category(self) -> str:
        return DEFAULT_CATEGORY

    @property
    def default_repo_location(self) -> str:
        return '/var/db/repos/npm'

    @property
    def repo_name(self) -> str:
        return 'portage-npm-fuse'

    def get_metadata_provider(
        self,
        cache_dir: Optional[str] = None,
        cache_ttl: int = 3600,
        **kwargs: Any,
    ) -> NpmMetadataProvider:
        """Get the npm metadata provider."""
        return NpmMetadataProvider(
            cache_dir=cache_dir, cache_ttl=cache_ttl, **kwargs)

    def get_ebuild_generator(self, **kwargs: Any) -> NpmEbuildGenerator:
        """Get the npm ebuild generator."""
        kwargs.setdefault('category', self.default_category)
        return NpmEbuildGenerator(**kwargs)

    def get_name_translator(self) -> name_translator.NpmNameTranslator:
        """Get the npm name translator."""
        return name_translator.NpmNameTranslator()

    def get_source_providers(self, **kwargs: Any) -> List[Any]:
        """
        Get source providers.

        Empty by design. Every npm package has exactly one source, the registry
        tarball, and the eclass builds its SRC_URI itself. There is no
        sdist-versus-wheel-versus-git choice to arbitrate, so the provider chain
        has nothing to do.
        """
        return []

    def get_version_filters(self) -> List[Any]:
        """Get the default version filters."""
        return list(npm_filters.create_filter_chain().filters)

    def get_static_files(self) -> Dict[str, bytes]:
        """
        Get the overlay's static files.

        Adds ``profiles/categories``, which the other ecosystems do not need:
        ``dev-python`` and ``dev-ruby`` exist in ::gentoo, but ``dev-nodejs``
        does not, so portage will not accept packages in it unless the overlay
        declares it.
        """
        files = super().get_static_files()
        files['/profiles/categories'] = (self.default_category + '\n').encode('utf-8')
        return files

    def get_static_dirs(self) -> Set[str]:
        """Get the overlay's static directories, including the .sys controls."""
        dirs = super().get_static_dirs()
        category = self.default_category

        for control in ('RDEPEND', 'DEPEND', 'node-compat', 'ebuild-append',
                        'iuse', 'slot', 'git-source', 'resolution-lock'):
            dirs.add('/.sys/%s' % control)
            dirs.add('/.sys/%s/%s' % (control, category))
            dirs.add('/.sys/%s-patch' % control)
            dirs.add('/.sys/%s-patch/%s' % (control, category))

        dirs.add('/.sys')
        dirs.add('/.sys/name-translation')
        return dirs

    def register_cli_commands(self, parser: 'ArgumentParser') -> None:
        """Register npm CLI subcommands. Wired up when cli.py lands."""

    def get_cli_handler(self, command: str) -> Optional[Callable[['Namespace'], int]]:
        """Get a CLI handler. Wired up when cli.py lands."""
        return None


PluginRegistry.register('npm', NpmPlugin)
