"""
FUSE filesystem presenting npm as a Gentoo overlay.

Serves a synthetic ebuild repository: package directories under
``dev-nodejs/``, an ebuild per visible version, a Manifest, and the repository
metadata portage needs. Content is generated on read and cached, so mounting is
instant and work happens only for packages actually looked at.

Two things this filesystem must serve that neither existing ecosystem does:

- ``eclass/npm.eclass``. ::gentoo has no npm eclass, so the overlay supplies its
  own. It is read from the installed package rather than generated, so the file
  under test and the file portage sources are the same bytes.
- ``profiles/categories``. ``dev-python`` and ``dev-ruby`` exist in ::gentoo, but
  ``dev-nodejs`` does not, and portage refuses packages in a category no
  repository declares.

Scope: this is the read-only overlay. The ``.sys/`` patch-control tree that the
PyPI and RubyGems filesystems expose is not implemented yet; the patch stores it
would drive are ecosystem-generic and already shared, so wiring them up is
additive and does not change anything here.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import errno
import logging
import os
import stat
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fuse import FUSE, FuseOSError, Operations

from portage_pip_fuse.constants import DEFAULT_PATCH_FILE
from portage_pip_fuse.ecosystems.npm import filters as npm_filters
from portage_pip_fuse.ecosystems.npm import name_translator, version_translator
from portage_pip_fuse.ecosystems.npm.resolution_lock import ResolutionLockStore
from portage_pip_fuse.ecosystems.npm.plugin import (
    NpmEbuildGenerator,
    NpmMetadataProvider,
    NpmPlugin,
)

logger = logging.getLogger(__name__)

__all__ = ['PortageNpmFS', 'mount_npm_filesystem']

#: Directories that always exist, regardless of what packages are visible.
_STATIC_DIRS = ('/', '/profiles', '/metadata', '/eclass')

#: Files served per package directory.
_PACKAGE_FILES = ('Manifest', 'metadata.xml')


def _find_eclass() -> Optional[Path]:
    """
    Locate ``npm.eclass`` inside the installed package.

    Serving the real file rather than a generated copy means the eclass portage
    sources is byte-identical to the one the test suite exercises.
    """
    # portage_pip_fuse/ecosystems/npm/filesystem.py -> repository root
    candidate = Path(__file__).resolve().parents[3] / 'eclass' / 'npm.eclass'
    if candidate.is_file():
        return candidate

    packaged = Path(__file__).resolve().parent / 'eclass' / 'npm.eclass'
    if packaged.is_file():
        return packaged

    logger.warning('npm.eclass not found; the overlay will not provide it')
    return None


class PortageNpmFS(Operations):
    """
    Read-only FUSE filesystem exposing npm packages as ebuilds.

    Examples:
        >>> fs = PortageNpmFS.__new__(PortageNpmFS)
        >>> fs.category = 'dev-nodejs'
        >>> fs._parse_path('/')
        {'type': 'root'}
        >>> fs._parse_path('/dev-nodejs')
        {'type': 'category', 'category': 'dev-nodejs'}
    """

    def __init__(
        self,
        cache_ttl: int = 3600,
        cache_dir: Optional[str] = None,
        filter_config: Optional[Dict[str, Any]] = None,
        mount_point: Optional[str] = None,
        node_versions: Optional[List[str]] = None,
        registry: Optional[str] = None,
        max_versions: int = 0,
        patch_file: Optional[str] = None,
        no_locks: bool = False,
    ):
        """
        Args:
            cache_ttl: Seconds generated content and metadata stay fresh
            cache_dir: Metadata cache directory
            filter_config: ``enabled_filters`` / ``disabled_filters`` lists
            mount_point: Where the filesystem is mounted, for logging
            node_versions: Override the detected Node versions
            registry: Alternative registry base URL
            max_versions: Cap the ebuilds offered per package, newest first;
                0 means no cap. A package with 3000 published versions produces
                3000 ebuilds otherwise, which makes ``emerge --sync``-style tree
                walks slow for no benefit.
            patch_file: Where dependency-pin locks are stored; defaults to the
                shared patches.json
            no_locks: Resolve afresh every time. Ebuilds then change whenever a
                dependency publishes a new version, which is occasionally what
                you want and usually not.
        """
        self.plugin = NpmPlugin()
        self.category = self.plugin.default_category
        self.cache_ttl = cache_ttl
        self.mount_point = mount_point
        self.max_versions = max_versions

        self.metadata_provider = NpmMetadataProvider(
            cache_dir=cache_dir, cache_ttl=cache_ttl, registry=registry)
        self.name_translator = name_translator.NpmNameTranslator()

        config = filter_config or {}
        self.version_filter_chain = npm_filters.create_filter_chain(
            enabled_filters=config.get('enabled_filters'),
            disabled_filters=config.get('disabled_filters'),
            node_versions=node_versions,
        )
        if no_locks:
            self.resolution_lock = None
        else:
            self.resolution_lock = ResolutionLockStore(
                storage_path=patch_file or str(DEFAULT_PATCH_FILE),
                mount_point=mount_point,
            )

        self.ebuild_generator = NpmEbuildGenerator(
            metadata_provider=self.metadata_provider,
            category=self.category,
            translator=self.name_translator,
            version_filter_chain=self.version_filter_chain,
            resolution_lock=self.resolution_lock,
        )

        self._eclass_path = _find_eclass()
        self._static_files = dict(self.plugin.get_static_files())
        self._content_cache: Dict[str, Any] = {}
        self._versions_cache: Dict[str, Any] = {}

        logger.info('npm overlay ready at %s (filters: %s)',
                    mount_point or '?', self.version_filter_chain.get_description())

    # -- path parsing ---------------------------------------------------------

    def _parse_path(self, path: str) -> Dict[str, str]:
        """
        Parse a path into its overlay components.

        Args:
            path: Absolute path within the mount

        Returns:
            A dictionary with at least a ``type`` key

        Examples:
            >>> fs = PortageNpmFS.__new__(PortageNpmFS)
            >>> fs.category = 'dev-nodejs'
            >>> fs._parse_path('/')
            {'type': 'root'}
            >>> fs._parse_path('/dev-nodejs')
            {'type': 'category', 'category': 'dev-nodejs'}
            >>> fs._parse_path('/dev-nodejs/chalk')
            {'type': 'package', 'category': 'dev-nodejs', 'package': 'chalk'}

            An ebuild path yields the version it names:

            >>> sorted(fs._parse_path(
            ...     '/dev-nodejs/chalk/chalk-4.1.2.ebuild').items())
            [('category', 'dev-nodejs'), ('filename', 'chalk-4.1.2.ebuild'), \
('package', 'chalk'), ('type', 'ebuild'), ('version', '4.1.2')]

            Scoped packages keep their '+' through path parsing:

            >>> sorted(fs._parse_path(
            ...     '/dev-nodejs/vue+cli-service/vue+cli-service-5.0.8.ebuild'
            ... ).items())
            [('category', 'dev-nodejs'), ('filename', 'vue+cli-service-5.0.8.ebuild'), \
('package', 'vue+cli-service'), ('type', 'ebuild'), ('version', '5.0.8')]

            Repository metadata:

            >>> fs._parse_path('/profiles/repo_name')
            {'type': 'profiles_file', 'filename': 'repo_name'}
            >>> fs._parse_path('/profiles/categories')
            {'type': 'profiles_file', 'filename': 'categories'}
            >>> fs._parse_path('/metadata/layout.conf')
            {'type': 'metadata_file', 'filename': 'layout.conf'}
            >>> fs._parse_path('/eclass/npm.eclass')
            {'type': 'eclass_file', 'filename': 'npm.eclass'}
            >>> fs._parse_path('/eclass')
            {'type': 'eclass'}

            Anything else is rejected rather than guessed at:

            >>> fs._parse_path('/dev-python')
            {'type': 'invalid'}
            >>> fs._parse_path('/dev-nodejs/chalk/nope')
            {'type': 'invalid'}
        """
        path = path.strip('/')
        if not path:
            return {'type': 'root'}

        parts = path.split('/')

        if parts[0] == 'profiles':
            if len(parts) == 1:
                return {'type': 'profiles'}
            if len(parts) == 2 and parts[1] in ('repo_name', 'categories'):
                return {'type': 'profiles_file', 'filename': parts[1]}
            return {'type': 'invalid'}

        if parts[0] == 'metadata':
            if len(parts) == 1:
                return {'type': 'metadata'}
            if len(parts) == 2 and parts[1] == 'layout.conf':
                return {'type': 'metadata_file', 'filename': parts[1]}
            return {'type': 'invalid'}

        if parts[0] == 'eclass':
            if len(parts) == 1:
                return {'type': 'eclass'}
            if len(parts) == 2 and parts[1] == 'npm.eclass':
                return {'type': 'eclass_file', 'filename': parts[1]}
            return {'type': 'invalid'}

        if parts[0] != self.category:
            return {'type': 'invalid'}

        if len(parts) == 1:
            return {'type': 'category', 'category': parts[0]}

        if len(parts) == 2:
            return {'type': 'package', 'category': parts[0], 'package': parts[1]}

        if len(parts) == 3:
            category, package, filename = parts

            if filename == 'Manifest':
                return {'type': 'manifest', 'category': category,
                        'package': package, 'filename': filename}
            if filename == 'metadata.xml':
                return {'type': 'package_metadata', 'category': category,
                        'package': package, 'filename': filename}

            prefix = package + '-'
            if filename.startswith(prefix) and filename.endswith('.ebuild'):
                version = filename[len(prefix):-len('.ebuild')]
                if version:
                    return {'type': 'ebuild', 'category': category,
                            'package': package, 'version': version,
                            'filename': filename}

        return {'type': 'invalid'}

    # -- lookups --------------------------------------------------------------

    def _gentoo_to_npm(self, gentoo_name: str) -> Optional[str]:
        """Translate a Gentoo package name back to its npm name."""
        return self.name_translator.gentoo_to_npm(gentoo_name)

    def _get_visible_versions(self, npm_name: str) -> Dict[str, Dict[str, Any]]:
        """
        Get the versions this overlay exposes, as ``{pms_version: manifest}``.

        Applies the filter chain, then translates to PMS versions. Two npm
        versions can translate to one PMS version -- ``1.0.0-beta.1`` and
        ``1.0.0-beta1`` both give ``1.0.0_beta1`` -- so the order-preserving
        selection inside ``gentoo-version`` is what keeps this a function rather
        than a collision.
        """
        cached = self._versions_cache.get(npm_name)
        if cached is not None:
            versions, stamp = cached
            if time.time() - stamp < self.cache_ttl:
                return versions

        raw = self.metadata_provider.get_versions_metadata(npm_name)
        if not raw:
            self._versions_cache[npm_name] = ({}, time.time())
            return {}

        filtered = self.version_filter_chain.filter_versions(npm_name, raw)

        ordered = version_translator.select_order_preserving(list(filtered))
        if self.max_versions > 0:
            ordered = ordered[:self.max_versions]

        visible: Dict[str, Dict[str, Any]] = {}
        for npm_version in ordered:
            pms_version = version_translator.translate_version(npm_version)
            if pms_version is None:
                continue
            manifest = dict(filtered[npm_version])
            manifest.setdefault('version', npm_version)
            visible[pms_version] = manifest

        self._versions_cache[npm_name] = (visible, time.time())
        return visible

    def _npm_version_for(self, npm_name: str, pms_version: str) -> Optional[str]:
        """
        Map a PMS version back to the npm version it came from.

        Not done by string transformation: ``untranslate_version`` is a hint
        rather than an inverse, so the answer comes from the manifest the
        forward pass recorded.
        """
        manifest = self._get_visible_versions(npm_name).get(pms_version)
        if manifest is None:
            return None
        return manifest.get('version')

    # -- content generation ---------------------------------------------------

    def _get_file_content(self, path: str, parsed: Dict[str, str]) -> Optional[bytes]:
        """Generate or fetch a file's content, caching the result."""
        cached = self._content_cache.get(path)
        if cached is not None:
            content, stamp = cached
            if time.time() - stamp < self.cache_ttl:
                return content
            del self._content_cache[path]

        content: Optional[bytes] = None
        kind = parsed['type']

        if kind == 'profiles_file':
            content = self._static_files.get('/profiles/%s' % parsed['filename'])
        elif kind == 'metadata_file':
            content = self._static_files.get('/metadata/%s' % parsed['filename'])
        elif kind == 'eclass_file':
            content = self._read_eclass()
        elif kind == 'ebuild':
            content = self._generate_ebuild(parsed)
        elif kind == 'manifest':
            content = self._generate_manifest(parsed)
        elif kind == 'package_metadata':
            content = self._generate_metadata_xml(parsed)

        if content is not None:
            self._content_cache[path] = (content, time.time())

        return content

    def _read_eclass(self) -> Optional[bytes]:
        """Read npm.eclass from disk."""
        if self._eclass_path is None:
            return None
        try:
            return self._eclass_path.read_bytes()
        except OSError as exc:
            logger.error('Cannot read %s: %s', self._eclass_path, exc)
            return None

    def _generate_ebuild(self, parsed: Dict[str, str]) -> Optional[bytes]:
        """Generate an ebuild for one package version."""
        npm_name = self._gentoo_to_npm(parsed['package'])
        if npm_name is None:
            return None

        pms_version = parsed['version']
        npm_version = self._npm_version_for(npm_name, pms_version)
        if npm_version is None:
            return None

        manifest = self.metadata_provider.get_full_version_info(
            npm_name, npm_version)
        if not manifest:
            return None

        try:
            text = self.ebuild_generator.generate_ebuild(
                manifest, npm_version, parsed['package'])
        except ValueError as exc:
            logger.debug('Cannot generate ebuild for %s-%s: %s',
                         parsed['package'], pms_version, exc)
            return None

        return text.encode('utf-8')

    def _generate_manifest(self, parsed: Dict[str, str]) -> Optional[bytes]:
        """
        Generate the Manifest for a package.

        One DIST line per visible version. Sizes come from ranged requests the
        provider caches permanently, so this is expensive only the first time a
        package is read.
        """
        npm_name = self._gentoo_to_npm(parsed['package'])
        if npm_name is None:
            return None

        lines = []
        for pms_version, manifest in sorted(
                self._get_visible_versions(npm_name).items()):
            npm_version = manifest.get('version')
            if not npm_version:
                continue
            entry = self.ebuild_generator.generate_manifest_entry(
                manifest, npm_version, parsed['package'])
            if entry:
                lines.append(entry)

        if not lines:
            return b''

        return ('\n'.join(lines) + '\n').encode('utf-8')

    def _generate_metadata_xml(self, parsed: Dict[str, str]) -> Optional[bytes]:
        """Generate a minimal metadata.xml recording the upstream package."""
        npm_name = self._gentoo_to_npm(parsed['package'])
        if npm_name is None:
            return None

        escaped = (npm_name.replace('&', '&amp;')
                   .replace('<', '&lt;').replace('>', '&gt;'))
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE pkgmetadata SYSTEM '
            '"https://www.gentoo.org/dtd/metadata.dtd">\n'
            '<pkgmetadata>\n'
            '\t<upstream>\n'
            '\t\t<remote-id type="npm">%s</remote-id>\n'
            '\t</upstream>\n'
            '</pkgmetadata>\n'
        ) % escaped
        return xml.encode('utf-8')

    # -- FUSE operations ------------------------------------------------------

    def _attributes(self, is_dir: bool, size: int = 0) -> Dict[str, Any]:
        """Build a stat dictionary for a read-only entry."""
        now = time.time()
        mode = (stat.S_IFDIR | 0o555) if is_dir else (stat.S_IFREG | 0o444)
        return {
            'st_mode': mode,
            'st_nlink': 2 if is_dir else 1,
            'st_size': size,
            'st_uid': os.getuid(),
            'st_gid': os.getgid(),
            'st_atime': now,
            'st_mtime': now,
            'st_ctime': now,
        }

    def getattr(self, path, fh=None):
        """Get attributes for a path."""
        parsed = self._parse_path(path)
        kind = parsed['type']

        if kind == 'invalid':
            raise FuseOSError(errno.ENOENT)

        if kind in ('root', 'profiles', 'metadata', 'eclass', 'category'):
            return self._attributes(is_dir=True)

        if kind == 'package':
            npm_name = self._gentoo_to_npm(parsed['package'])
            if npm_name is None or not self._get_visible_versions(npm_name):
                raise FuseOSError(errno.ENOENT)
            return self._attributes(is_dir=True)

        content = self._get_file_content(path, parsed)
        if content is None:
            raise FuseOSError(errno.ENOENT)
        return self._attributes(is_dir=False, size=len(content))

    def readdir(self, path, fh):
        """List a directory."""
        parsed = self._parse_path(path)
        kind = parsed['type']
        entries = ['.', '..']

        if kind == 'root':
            entries.extend([self.category, 'profiles', 'metadata', 'eclass'])
        elif kind == 'profiles':
            entries.extend(['repo_name', 'categories'])
        elif kind == 'metadata':
            entries.append('layout.conf')
        elif kind == 'eclass':
            if self._eclass_path is not None:
                entries.append('npm.eclass')
        elif kind == 'category':
            entries.extend(self._list_packages())
        elif kind == 'package':
            entries.extend(self._list_package_files(parsed['package']))
        else:
            raise FuseOSError(errno.ENOENT)

        return entries

    def _list_packages(self) -> List[str]:
        """
        List package directories.

        npm has no cheap enumeration -- the replication endpoint is millions of
        documents -- so this reports the packages already in the metadata cache.
        Portage does not need a full listing to resolve a named atom; it stats
        the path directly, which works for any package whether listed or not.
        """
        names = []
        for npm_name in sorted(self.metadata_provider.list_packages()):
            gentoo_name = self.name_translator.npm_to_gentoo(npm_name)
            if gentoo_name is not None:
                names.append(gentoo_name)
        return names

    def _list_package_files(self, gentoo_name: str) -> List[str]:
        """List the files inside a package directory."""
        npm_name = self._gentoo_to_npm(gentoo_name)
        if npm_name is None:
            raise FuseOSError(errno.ENOENT)

        versions = self._get_visible_versions(npm_name)
        if not versions:
            raise FuseOSError(errno.ENOENT)

        entries = ['%s-%s.ebuild' % (gentoo_name, version)
                   for version in sorted(versions)]
        entries.extend(_PACKAGE_FILES)
        return entries

    def read(self, path, length, offset, fh):
        """Read from a generated file."""
        parsed = self._parse_path(path)
        content = self._get_file_content(path, parsed)
        if content is None:
            raise FuseOSError(errno.ENOENT)
        return content[offset:offset + length]

    def open(self, path, flags):
        """Open a file, rejecting writes."""
        # O_WRONLY is 1 and O_RDWR is 2, so anything in the low two bits is a
        # write request. This overlay is generated, so there is nothing to
        # write back to.
        if flags & (os.O_WRONLY | os.O_RDWR):
            raise FuseOSError(errno.EROFS)

        parsed = self._parse_path(path)
        if parsed['type'] == 'invalid':
            raise FuseOSError(errno.ENOENT)
        if self._get_file_content(path, parsed) is None:
            raise FuseOSError(errno.ENOENT)
        return 0

    def access(self, path, mode):
        """Check access, denying writes."""
        if mode & os.W_OK:
            raise FuseOSError(errno.EROFS)

        parsed = self._parse_path(path)
        if parsed['type'] == 'invalid':
            raise FuseOSError(errno.ENOENT)
        return 0

    def statfs(self, path):
        """Report filesystem statistics.

        The numbers are nominal: nothing is stored, but portage reads them and a
        zero-size filesystem would look full.
        """
        return {
            'f_bsize': 4096,
            'f_frsize': 4096,
            'f_blocks': 1 << 20,
            'f_bfree': 1 << 19,
            'f_bavail': 1 << 19,
            'f_files': 1 << 16,
            'f_ffree': 1 << 15,
            'f_favail': 1 << 15,
            'f_namemax': 255,
        }

    def release(self, path, fh):
        """Release a file handle."""
        return 0

    def destroy(self, path):
        """
        Persist newly recorded dependency pins on unmount.

        Locks are recorded lazily as ebuilds are generated, so without this the
        first mount's decisions are lost and the next mount resolves afresh --
        defeating the point of locking.
        """
        if self.resolution_lock is not None and self.resolution_lock.is_dirty:
            if self.resolution_lock.save():
                logger.info('Saved dependency pin locks')
            else:
                logger.error('Failed to save dependency pin locks')

    # -- read-only rejections -------------------------------------------------

    def _readonly(self, *args, **kwargs):
        """Reject every mutating operation."""
        raise FuseOSError(errno.EROFS)

    create = _readonly
    write = _readonly
    truncate = _readonly
    unlink = _readonly
    mkdir = _readonly
    rmdir = _readonly
    symlink = _readonly
    rename = _readonly
    link = _readonly
    chmod = _readonly
    chown = _readonly
    utimens = _readonly


def mount_npm_filesystem(
    mountpoint: str,
    foreground: bool = False,
    debug: bool = False,
    cache_ttl: int = 3600,
    cache_dir: Optional[str] = None,
    filter_config: Optional[Dict[str, Any]] = None,
    node_versions: Optional[List[str]] = None,
    registry: Optional[str] = None,
    max_versions: int = 0,
    patch_file: Optional[str] = None,
    no_locks: bool = False,
    allow_other: bool = True,
):
    """
    Mount the npm FUSE filesystem.

    Args:
        mountpoint: Where to mount
        foreground: Stay in the foreground instead of daemonising
        debug: Enable FUSE debug output
        cache_ttl: Seconds content and metadata stay fresh
        cache_dir: Metadata cache directory
        filter_config: ``enabled_filters`` / ``disabled_filters`` lists
        node_versions: Override the detected Node versions
        registry: Alternative registry base URL
        max_versions: Cap ebuilds per package, newest first; 0 for no cap
        patch_file: Where dependency-pin locks are stored
        no_locks: Resolve dependencies afresh instead of reusing locked pins
        allow_other: Let other users, notably portage, read the mount
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)

    logger.info('Mounting npm FUSE filesystem at %s', mountpoint)

    filesystem = PortageNpmFS(
        cache_ttl=cache_ttl,
        cache_dir=cache_dir,
        filter_config=filter_config,
        mount_point=mountpoint,
        node_versions=node_versions,
        registry=registry,
        max_versions=max_versions,
        patch_file=patch_file,
        no_locks=no_locks,
    )

    # Timeouts are zeroed so a package appearing in the cache becomes visible
    # immediately, rather than after the kernel's attribute cache expires.
    FUSE(filesystem, mountpoint, nothreads=False, foreground=foreground,
         debug=debug, allow_other=allow_other, ro=True,
         entry_timeout=0, attr_timeout=0, negative_timeout=0)
