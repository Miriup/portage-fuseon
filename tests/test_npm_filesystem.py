"""
Tests for the npm FUSE filesystem.

Path parsing and content generation are driven through a stub provider, using
the established ``__new__`` fixture so no FUSE mount, network or cache directory
is needed. A separate class performs a *real* mount when /dev/fuse and
fusermount are available, because the operations portage actually performs --
stat, readdir, open, read -- go through the kernel and a unit test cannot
exercise that path.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import doctest
import errno
import os
import shutil
import subprocess
import sys
import time

import base64
import hashlib

import pytest

fuse = pytest.importorskip('fuse')

from portage_pip_fuse.ecosystems.npm import filesystem as npm_fs  # noqa: E402
from portage_pip_fuse.ecosystems.npm import filters as npm_filters  # noqa: E402
from portage_pip_fuse.ecosystems.npm.filesystem import PortageNpmFS  # noqa: E402
from portage_pip_fuse.ecosystems.npm import name_translator as nt  # noqa: E402
from portage_pip_fuse.ecosystems.npm.plugin import (  # noqa: E402
    NpmEbuildGenerator,
    NpmPlugin,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dist(tarball, payload=b'x'):
    """Build a realistic dist block. Real packages always carry a digest, and
    without one generate_manifest_entry correctly refuses to emit a DIST line."""
    return {
        'tarball': tarball,
        'integrity': 'sha512-' + base64.b64encode(
            hashlib.sha512(payload).digest()).decode(),
    }


PACKUMENTS = {
    'chalk': {
        'name': 'chalk',
        'versions': {
            '4.1.0': {'name': 'chalk', 'version': '4.1.0',
                      'dist': _dist('https://r/a.tgz', b'a')},
            '4.1.2': {'name': 'chalk', 'version': '4.1.2',
                      'dependencies': {'ansi-styles': '^4.1.0'},
                      'dist': _dist('https://r/b.tgz', b'b')},
            '5.0.0-next.1': {'name': 'chalk', 'version': '5.0.0-next.1',
                             'dist': _dist('https://r/c.tgz', b'c')},
        },
    },
    '@vue/cli-service': {
        'name': '@vue/cli-service',
        'versions': {
            '5.0.8': {'name': '@vue/cli-service', 'version': '5.0.8',
                      'dist': _dist('https://r/d.tgz', b'd')},
        },
    },
    'ansi-styles': {
        'name': 'ansi-styles',
        'versions': {
            '4.3.0': {'name': 'ansi-styles', 'version': '4.3.0', 'dist': _dist('https://r/e.tgz', b'e')},
        },
    },
}

FULL = {
    ('chalk', '4.1.2'): {
        'name': 'chalk', 'version': '4.1.2',
        'description': 'Terminal string styling done right',
        'license': 'MIT', 'homepage': 'https://github.com/chalk/chalk#readme',
        'dependencies': {'ansi-styles': '^4.1.0'},
        'dist': _dist('https://r/b.tgz', b'b'),
    },
}


class StubProvider:
    def __init__(self):
        self.packuments = PACKUMENTS

    def get_package_info(self, name):
        return self.packuments.get(name)

    def get_versions_metadata(self, name):
        return dict((self.packuments.get(name) or {}).get('versions') or {})

    def get_package_versions(self, name):
        return list(self.get_versions_metadata(name))

    def get_version_info(self, name, version):
        return self.get_versions_metadata(name).get(version)

    def get_full_version_info(self, name, version):
        return FULL.get((name, version)) or self.get_version_info(name, version)

    def get_tarball_size(self, name, version):
        return 1234

    def list_packages(self):
        return set(self.packuments)


@pytest.fixture
def fs():
    """A filesystem wired to stubs, without running __init__."""
    instance = PortageNpmFS.__new__(PortageNpmFS)
    plugin = NpmPlugin()
    provider = StubProvider()

    instance.plugin = plugin
    instance.category = plugin.default_category
    instance.cache_ttl = 3600
    instance.mount_point = None
    instance.max_versions = 0
    instance.metadata_provider = provider
    instance.name_translator = nt.NpmNameTranslator()
    instance.version_filter_chain = npm_filters.create_filter_chain(
        node_versions=['22.22.2'])
    instance.ebuild_generator = NpmEbuildGenerator(
        metadata_provider=provider,
        category=instance.category,
        translator=instance.name_translator,
        version_filter_chain=instance.version_filter_chain,
    )
    instance._eclass_path = npm_fs._find_eclass()
    instance._static_files = dict(plugin.get_static_files())
    instance._content_cache = {}
    instance._versions_cache = {}
    return instance


class TestPathParsing:

    @pytest.mark.parametrize('path,expected', [
        ('/', {'type': 'root'}),
        ('/dev-nodejs', {'type': 'category', 'category': 'dev-nodejs'}),
        ('/profiles', {'type': 'profiles'}),
        ('/metadata', {'type': 'metadata'}),
        ('/eclass', {'type': 'eclass'}),
        ('/profiles/repo_name', {'type': 'profiles_file', 'filename': 'repo_name'}),
        ('/profiles/categories', {'type': 'profiles_file', 'filename': 'categories'}),
        ('/metadata/layout.conf',
         {'type': 'metadata_file', 'filename': 'layout.conf'}),
        ('/eclass/npm.eclass', {'type': 'eclass_file', 'filename': 'npm.eclass'}),
    ])
    def test_static_paths(self, fs, path, expected):
        assert fs._parse_path(path) == expected

    def test_package_path(self, fs):
        assert fs._parse_path('/dev-nodejs/chalk') == {
            'type': 'package', 'category': 'dev-nodejs', 'package': 'chalk'}

    def test_ebuild_path(self, fs):
        assert fs._parse_path('/dev-nodejs/chalk/chalk-4.1.2.ebuild') == {
            'type': 'ebuild', 'category': 'dev-nodejs', 'package': 'chalk',
            'version': '4.1.2', 'filename': 'chalk-4.1.2.ebuild'}

    def test_scoped_ebuild_path(self, fs):
        parsed = fs._parse_path(
            '/dev-nodejs/vue+cli-service/vue+cli-service-5.0.8.ebuild')
        assert parsed['package'] == 'vue+cli-service'
        assert parsed['version'] == '5.0.8'

    def test_prerelease_ebuild_path(self, fs):
        parsed = fs._parse_path('/dev-nodejs/pkg/pkg-1.0.0_beta1.ebuild')
        assert parsed['version'] == '1.0.0_beta1'

    def test_package_files(self, fs):
        assert fs._parse_path('/dev-nodejs/chalk/Manifest')['type'] == 'manifest'
        assert fs._parse_path('/dev-nodejs/chalk/metadata.xml')['type'] == \
            'package_metadata'

    @pytest.mark.parametrize('path', [
        '/dev-python', '/dev-ruby/rails', '/nonsense',
        '/dev-nodejs/chalk/nope', '/dev-nodejs/chalk/other-1.0.0.ebuild',
        '/profiles/nonsense', '/metadata/nonsense', '/eclass/other.eclass',
        '/dev-nodejs/chalk/chalk-.ebuild', '/dev-nodejs/chalk/a/b/c',
    ])
    def test_invalid_paths(self, fs, path):
        assert fs._parse_path(path)['type'] == 'invalid'

    def test_ebuild_prefix_must_match_the_package(self, fs):
        """A file named for a different package is not this package's ebuild."""
        assert fs._parse_path(
            '/dev-nodejs/chalk/ansi-styles-4.3.0.ebuild')['type'] == 'invalid'


class TestVisibleVersions:

    def test_filters_are_applied(self, fs):
        versions = fs._get_visible_versions('chalk')
        assert '5.0.0_next1' not in versions
        assert set(versions) == {'4.1.0', '4.1.2'}

    def test_keys_are_pms_versions_values_carry_npm_versions(self, fs):
        versions = fs._get_visible_versions('chalk')
        assert versions['4.1.2']['version'] == '4.1.2'

    def test_max_versions_caps_newest_first(self, fs):
        fs.max_versions = 1
        fs._versions_cache.clear()
        assert list(fs._get_visible_versions('chalk')) == ['4.1.2']

    def test_unknown_package_has_no_versions(self, fs):
        assert fs._get_visible_versions('no-such-package') == {}

    def test_results_are_cached(self, fs):
        first = fs._get_visible_versions('chalk')
        fs.metadata_provider.packuments = {}
        assert fs._get_visible_versions('chalk') == first

    def test_pms_version_maps_back_to_the_npm_version(self, fs):
        """
        Recovered from the recorded manifest, not by string transformation:
        untranslate_version is a hint, not an inverse.
        """
        assert fs._npm_version_for('chalk', '4.1.2') == '4.1.2'
        assert fs._npm_version_for('chalk', '9.9.9') is None


class TestContentGeneration:

    def test_repo_name(self, fs):
        content = fs._get_file_content(
            '/profiles/repo_name', fs._parse_path('/profiles/repo_name'))
        assert content == b'portage-npm-fuse\n'

    def test_categories_is_served(self, fs):
        """dev-nodejs does not exist in ::gentoo, so the overlay must declare it."""
        content = fs._get_file_content(
            '/profiles/categories', fs._parse_path('/profiles/categories'))
        assert content == b'dev-nodejs\n'

    def test_layout_conf(self, fs):
        content = fs._get_file_content(
            '/metadata/layout.conf', fs._parse_path('/metadata/layout.conf'))
        assert b'repo-name = portage-npm-fuse' in content
        assert b'masters = gentoo' in content

    def test_eclass_is_the_real_file(self, fs):
        """
        Served from disk, so the eclass portage sources is byte-identical to the
        one the eclass tests exercise.
        """
        if fs._eclass_path is None:
            pytest.skip('eclass not present')
        content = fs._get_file_content(
            '/eclass/npm.eclass', fs._parse_path('/eclass/npm.eclass'))
        with open(os.path.join(REPO_ROOT, 'eclass', 'npm.eclass'), 'rb') as handle:
            assert content == handle.read()

    def test_ebuild(self, fs):
        path = '/dev-nodejs/chalk/chalk-4.1.2.ebuild'
        content = fs._get_file_content(path, fs._parse_path(path)).decode()
        assert 'EAPI=8' in content
        assert 'inherit npm' in content
        assert 'NPM_PN="chalk"' in content
        assert 'DESCRIPTION="Terminal string styling done right"' in content
        assert 'LICENSE="MIT"' in content
        assert '~dev-nodejs/ansi-styles-4.3.0' in content

    def test_ebuild_for_filtered_version_is_absent(self, fs):
        path = '/dev-nodejs/chalk/chalk-5.0.0_next1.ebuild'
        assert fs._get_file_content(path, fs._parse_path(path)) is None

    def test_manifest(self, fs):
        path = '/dev-nodejs/chalk/Manifest'
        content = fs._get_file_content(path, fs._parse_path(path)).decode()
        lines = content.strip().splitlines()
        assert len(lines) == 2, 'one DIST line per visible version'
        assert all(line.startswith('DIST chalk-') for line in lines)
        assert '5.0.0' not in content

    def test_metadata_xml_records_the_upstream_name(self, fs):
        path = '/dev-nodejs/vue+cli-service/metadata.xml'
        content = fs._get_file_content(path, fs._parse_path(path)).decode()
        assert '<remote-id type="npm">@vue/cli-service</remote-id>' in content

    def test_unknown_package_has_no_content(self, fs):
        path = '/dev-nodejs/nope/nope-1.0.0.ebuild'
        assert fs._get_file_content(path, fs._parse_path(path)) is None

    def test_content_is_cached(self, fs):
        path = '/dev-nodejs/chalk/chalk-4.1.2.ebuild'
        first = fs._get_file_content(path, fs._parse_path(path))
        fs.metadata_provider.packuments = {}
        assert fs._get_file_content(path, fs._parse_path(path)) == first


class TestOperations:

    def test_getattr_directories(self, fs):
        import stat as stat_module
        for path in ('/', '/dev-nodejs', '/profiles', '/metadata', '/eclass',
                     '/dev-nodejs/chalk'):
            attrs = fs.getattr(path)
            assert stat_module.S_ISDIR(attrs['st_mode']), path

    def test_getattr_files_report_real_size(self, fs):
        import stat as stat_module
        path = '/dev-nodejs/chalk/chalk-4.1.2.ebuild'
        attrs = fs.getattr(path)
        assert stat_module.S_ISREG(attrs['st_mode'])
        assert attrs['st_size'] == len(
            fs._get_file_content(path, fs._parse_path(path)))

    @pytest.mark.parametrize('path', [
        '/dev-python', '/dev-nodejs/nope', '/dev-nodejs/chalk/nope',
        '/dev-nodejs/chalk/chalk-9.9.9.ebuild',
    ])
    def test_getattr_missing_raises_enoent(self, fs, path):
        with pytest.raises(fuse.FuseOSError) as info:
            fs.getattr(path)
        assert info.value.errno == errno.ENOENT

    def test_readdir_root(self, fs):
        entries = fs.readdir('/', None)
        assert set(entries) >= {'.', '..', 'dev-nodejs', 'profiles', 'metadata',
                                'eclass'}

    def test_readdir_profiles_includes_categories(self, fs):
        assert set(fs.readdir('/profiles', None)) >= {'repo_name', 'categories'}

    def test_readdir_eclass(self, fs):
        if fs._eclass_path is None:
            pytest.skip('eclass not present')
        assert 'npm.eclass' in fs.readdir('/eclass', None)

    def test_readdir_category_translates_names(self, fs):
        entries = fs.readdir('/dev-nodejs', None)
        assert 'chalk' in entries
        assert 'vue+cli-service' in entries, 'scoped name must be translated'
        assert '@vue/cli-service' not in entries

    def test_readdir_package(self, fs):
        entries = fs.readdir('/dev-nodejs/chalk', None)
        assert 'chalk-4.1.2.ebuild' in entries
        assert 'chalk-4.1.0.ebuild' in entries
        assert 'chalk-5.0.0_next1.ebuild' not in entries
        assert 'Manifest' in entries
        assert 'metadata.xml' in entries

    def test_readdir_unknown_package_raises(self, fs):
        with pytest.raises(fuse.FuseOSError):
            fs.readdir('/dev-nodejs/nope', None)

    def test_read_offsets(self, fs):
        path = '/dev-nodejs/chalk/chalk-4.1.2.ebuild'
        whole = fs._get_file_content(path, fs._parse_path(path))
        assert fs.read(path, 10, 0, None) == whole[:10]
        assert fs.read(path, 10, 5, None) == whole[5:15]
        assert fs.read(path, 10, len(whole), None) == b''

    def test_open_rejects_writes(self, fs):
        with pytest.raises(fuse.FuseOSError) as info:
            fs.open('/profiles/repo_name', os.O_WRONLY)
        assert info.value.errno == errno.EROFS

    def test_open_allows_reads(self, fs):
        assert fs.open('/profiles/repo_name', os.O_RDONLY) == 0

    def test_access_rejects_write_checks(self, fs):
        with pytest.raises(fuse.FuseOSError) as info:
            fs.access('/', os.W_OK)
        assert info.value.errno == errno.EROFS

    @pytest.mark.parametrize('operation,args', [
        ('create', ('/x', 0o644)),
        ('write', ('/x', b'data', 0, None)),
        ('unlink', ('/x',)),
        ('mkdir', ('/x', 0o755)),
        ('truncate', ('/x', 0)),
        ('rename', ('/x', '/y')),
        ('chmod', ('/x', 0o777)),
    ])
    def test_mutating_operations_are_refused(self, fs, operation, args):
        with pytest.raises(fuse.FuseOSError) as info:
            getattr(fs, operation)(*args)
        assert info.value.errno == errno.EROFS

    def test_statfs_reports_free_space(self, fs):
        stats = fs.statfs('/')
        assert stats['f_bavail'] > 0, 'a full filesystem would deter portage'
        assert stats['f_namemax'] >= 255


class TestPluginConsistency:

    def test_category_comes_from_the_plugin(self, fs):
        assert fs.category == NpmPlugin().default_category

    def test_static_files_come_from_the_plugin(self, fs):
        assert fs._static_files == dict(NpmPlugin().get_static_files())


@pytest.mark.skipif(
    not (os.path.exists('/dev/fuse') and shutil.which('fusermount')),
    reason='a real FUSE mount needs /dev/fuse and fusermount',
)
class TestRealMount:
    """
    Mount the filesystem for real.

    The operations portage performs go through the kernel, so a unit test on the
    Operations object cannot prove the mount works. This exercises the same
    lookups portage would: stat the tree, list it, read an ebuild and the
    eclass.
    """

    def _mount(self, tmp_path, cache_dir):
        mountpoint = tmp_path / 'mnt'
        mountpoint.mkdir()
        script = (
            'import sys; sys.path.insert(0, %r)\n'
            'from portage_pip_fuse.ecosystems.npm.filesystem import mount_npm_filesystem\n'
            'mount_npm_filesystem(%r, foreground=True, cache_dir=%r,\n'
            '                     max_versions=2, allow_other=False)\n'
            % (REPO_ROOT, str(mountpoint), str(cache_dir))
        )
        env = dict(os.environ, NODE_VERSIONS='22.22.2')
        process = subprocess.Popen([sys.executable, '-c', script],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env)
        for _ in range(60):
            time.sleep(0.25)
            if (mountpoint / 'profiles').is_dir():
                return mountpoint, process
        process.terminate()
        _out, err = process.communicate(timeout=10)
        pytest.skip('filesystem did not mount: %s' % err.decode()[-400:])

    @staticmethod
    def _unmount(mountpoint, process):
        subprocess.run(['fusermount', '-u', str(mountpoint)],
                       capture_output=True)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    def test_serves_a_usable_overlay(self, tmp_path):
        """One mount, several assertions: mounting is slow, correctness is not."""
        cache = tmp_path / 'cache'
        cache.mkdir()

        # Seed the cache offline so the test needs no network.
        from portage_pip_fuse.json_cache import JSONCache
        packuments = JSONCache(cache / 'npm', ttl=3600)
        for name, document in PACKUMENTS.items():
            packuments.set(name, document)
        manifests = JSONCache(cache / 'npm' / 'manifests', ttl=3600)
        for (name, version), manifest in FULL.items():
            manifests.set('%s/%s' % (name, version), manifest)
        sizes = JSONCache(cache / 'npm' / 'sizes', ttl=3600)
        for name, document in PACKUMENTS.items():
            for version in document['versions']:
                sizes.set('%s@%s' % (name, version), {'size': 4242})

        mountpoint, process = self._mount(tmp_path, cache)
        try:
            root = set(os.listdir(mountpoint))
            assert {'dev-nodejs', 'profiles', 'metadata', 'eclass'} <= root

            # The category portage would otherwise reject.
            categories = (mountpoint / 'profiles' / 'categories').read_text()
            assert categories.strip() == 'dev-nodejs'

            # Scoped names are translated in the listing.
            packages = set(os.listdir(mountpoint / 'dev-nodejs'))
            assert {'chalk', 'vue+cli-service'} <= packages

            # An ebuild is readable through the kernel.
            ebuild = (mountpoint / 'dev-nodejs' / 'chalk' /
                      'chalk-4.1.2.ebuild').read_text()
            assert 'inherit npm' in ebuild
            assert 'NPM_PN="chalk"' in ebuild

            # The eclass is byte-identical to the repository's copy.
            served = (mountpoint / 'eclass' / 'npm.eclass').read_bytes()
            with open(os.path.join(REPO_ROOT, 'eclass', 'npm.eclass'), 'rb') as fh:
                assert served == fh.read()

            # Manifest lines are present and sized.
            manifest = (mountpoint / 'dev-nodejs' / 'chalk' / 'Manifest').read_text()
            assert manifest.startswith('DIST chalk-')
            assert ' 4242 ' in manifest

            # Writes are refused by the kernel, not merely by Python.
            with pytest.raises(OSError) as info:
                (mountpoint / 'dev-nodejs' / 'chalk' / 'x').write_text('nope')
            assert info.value.errno == errno.EROFS

            # Unknown paths are ENOENT.
            assert not (mountpoint / 'dev-nodejs' / 'no-such-package').exists()
            assert not (mountpoint / 'dev-python').exists()
        finally:
            self._unmount(mountpoint, process)


def test_doctests():
    results = doctest.testmod(npm_fs, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed
