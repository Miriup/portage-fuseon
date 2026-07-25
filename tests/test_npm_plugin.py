"""
Tests for the npm metadata provider, ebuild generator and plugin.

No network: the provider is exercised against captured registry documents, and
the ebuild generator against a stub provider. The one integration test that
needs real files drives the eclass harness with the generator's own output.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import base64
import doctest
import hashlib
import json
import os
import re
import subprocess
from unittest.mock import patch

import pytest

from portage_pip_fuse.ecosystems.npm import plugin as npm_plugin
from portage_pip_fuse.ecosystems.npm import name_translator as nt
from portage_pip_fuse.ecosystems.npm import version_translator as vt
from portage_pip_fuse.ecosystems.npm.plugin import (
    NpmEbuildGenerator,
    NpmMetadataProvider,
    NpmPlugin,
    integrity_to_hex,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _integrity(payload=b'x'):
    return 'sha512-' + base64.b64encode(hashlib.sha512(payload).digest()).decode()


#: Shaped like a real abbreviated packument, including the fields the abbreviated
#: form actually omits being absent.
CHALK_PACKUMENT = {
    'name': 'chalk',
    'dist-tags': {'latest': '4.1.2'},
    'versions': {
        '4.1.0': {'name': 'chalk', 'version': '4.1.0',
                  'dependencies': {'ansi-styles': '^4.1.0'},
                  'dist': {'tarball': 'https://r/chalk-4.1.0.tgz',
                           'integrity': _integrity(b'410')}},
        '4.1.2': {'name': 'chalk', 'version': '4.1.2',
                  'dependencies': {'ansi-styles': '^4.1.0',
                                   'supports-color': '^7.1.0'},
                  'engines': {'node': '>=10'},
                  'dist': {'tarball': 'https://r/chalk-4.1.2.tgz',
                           'integrity': _integrity(b'412')}},
        '5.0.0-next.1': {'name': 'chalk', 'version': '5.0.0-next.1',
                         'dist': {'tarball': 'https://r/chalk-5.tgz',
                                  'integrity': _integrity(b'5')}},
    },
}

ANSI_STYLES_PACKUMENT = {
    'name': 'ansi-styles',
    'versions': {
        '4.2.1': {'name': 'ansi-styles', 'version': '4.2.1', 'dist': {}},
        '4.3.0': {'name': 'ansi-styles', 'version': '4.3.0', 'dist': {}},
        '5.0.0': {'name': 'ansi-styles', 'version': '5.0.0', 'dist': {}},
        # A nightly the gentoo-version filter must hide, so a pin can never
        # select it.
        '4.9.0-dev.20240101': {'name': 'ansi-styles',
                               'version': '4.9.0-dev.20240101', 'dist': {}},
    },
}

SUPPORTS_COLOR_PACKUMENT = {
    'name': 'supports-color',
    'versions': {
        '7.2.0': {'name': 'supports-color', 'version': '7.2.0', 'dist': {}},
        '8.0.0': {'name': 'supports-color', 'version': '8.0.0', 'dist': {}},
    },
}

PACKUMENTS = {
    'chalk': CHALK_PACKUMENT,
    'ansi-styles': ANSI_STYLES_PACKUMENT,
    'supports-color': SUPPORTS_COLOR_PACKUMENT,
}


class StubProvider:
    """Offline stand-in for NpmMetadataProvider."""

    def __init__(self, packuments=None, full=None, sizes=None):
        self.packuments = packuments if packuments is not None else PACKUMENTS
        self.full = full or {}
        self.sizes = sizes or {}
        self.size_calls = []

    def get_package_info(self, name):
        return self.packuments.get(name)

    def get_versions_metadata(self, name):
        return dict((self.packuments.get(name) or {}).get('versions') or {})

    def get_package_versions(self, name):
        return list(self.get_versions_metadata(name))

    def get_version_info(self, name, version):
        return self.get_versions_metadata(name).get(version)

    def get_full_version_info(self, name, version):
        return self.full.get((name, version)) or self.get_version_info(name, version)

    def get_tarball_size(self, name, version):
        self.size_calls.append((name, version))
        return self.sizes.get((name, version))


@pytest.fixture
def provider():
    return StubProvider()


@pytest.fixture
def generator(provider):
    return NpmEbuildGenerator(metadata_provider=provider)


class TestIntegrityToHex:

    def test_converts_sha512(self):
        payload = b'hello'
        integrity = 'sha512-' + base64.b64encode(
            hashlib.sha512(payload).digest()).decode()
        algorithm, hexdigest = integrity_to_hex(integrity)
        assert algorithm == 'SHA512'
        assert hexdigest == hashlib.sha512(payload).hexdigest()
        assert len(hexdigest) == 128

    @pytest.mark.parametrize('integrity', [
        '', 'not-an-integrity', 'md5-abcd', 'sha512-!!!notbase64!!!', None,
    ])
    def test_rejects_unusable(self, integrity):
        assert integrity_to_hex(integrity) is None

    def test_algorithm_is_uppercased_for_manifest(self):
        assert integrity_to_hex(_integrity())[0] == 'SHA512'


class TestMetadataProvider:

    def test_scoped_names_are_fully_quoted(self):
        """
        A scoped name's '/' must be percent-encoded, or the registry reads it as
        a path separator and returns the scope instead of the package.
        """
        captured = {}

        def fake_get(quoted_path, accept):
            captured['path'] = quoted_path
            return {'name': '@vue/cli-service', 'versions': {}}

        instance = NpmMetadataProvider.__new__(NpmMetadataProvider)
        instance.registry = 'https://registry.npmjs.org'
        instance._get = fake_get
        assert instance._fetch('@vue/cli-service') is not None
        assert captured['path'] == '%40vue%2Fcli-service'
        assert '/' not in captured['path']

    def test_versions_are_sorted_newest_first(self):
        """
        Sorted by semver precedence, not filtered. The provider reports what the
        registry publishes; hiding prereleases is the filters' job, so
        5.0.0-next.1 legitimately outranks 4.1.2 here.
        """
        instance = NpmMetadataProvider.__new__(NpmMetadataProvider)
        instance.get_package_info = lambda name: CHALK_PACKUMENT
        versions = NpmMetadataProvider.get_package_versions(instance, 'chalk')

        from portage_pip_fuse.ecosystems.npm import semver
        parsed = [semver.parse_version(v) for v in versions]
        assert parsed == sorted(parsed, reverse=True)
        assert versions.index('4.1.2') < versions.index('4.1.0')
        assert set(versions) == set(CHALK_PACKUMENT['versions'])

    def test_unparseable_versions_are_dropped(self):
        instance = NpmMetadataProvider.__new__(NpmMetadataProvider)
        instance.get_package_info = lambda name: {
            'versions': {'1.0.0': {}, 'not-a-version': {}}}
        assert NpmMetadataProvider.get_package_versions(instance, 'x') == ['1.0.0']

    def test_missing_package_yields_no_versions(self):
        instance = NpmMetadataProvider.__new__(NpmMetadataProvider)
        instance.get_package_info = lambda name: None
        assert NpmMetadataProvider.get_package_versions(instance, 'x') == []

    def test_size_probe_reads_content_range(self):
        class FakeResponse:
            headers = {'Content-Range': 'bytes 0-0/11577'}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch('urllib.request.urlopen', return_value=FakeResponse()):
            assert NpmMetadataProvider._probe_size('https://r/x.tgz') == 11577

    def test_size_probe_falls_back_to_content_length(self):
        """Some mirrors ignore Range and send the whole body."""
        class FakeResponse:
            headers = {'Content-Length': '4242'}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch('urllib.request.urlopen', return_value=FakeResponse()):
            assert NpmMetadataProvider._probe_size('https://r/x.tgz') == 4242

    def test_size_probe_survives_failure(self):
        with patch('urllib.request.urlopen', side_effect=OSError('down')):
            assert NpmMetadataProvider._probe_size('https://r/x.tgz') is None


class TestDependencyCollection:

    def test_runtime_dependencies_only(self, generator):
        assert generator.collect_requirements({
            'dependencies': {'chalk': '^4.0.0'},
            'devDependencies': {'jest': '^29.0.0'},
        }) == {'chalk': '^4.0.0'}

    def test_required_peers_are_included(self, generator):
        assert sorted(generator.collect_requirements({
            'peerDependencies': {'react': '^18.0.0', 'less': '^4.0.0'},
            'peerDependenciesMeta': {'less': {'optional': True}},
        })) == ['react']

    def test_optional_dependencies_are_excluded_by_default(self, generator):
        """
        A hard portage dependency on an optionalDependency would turn an
        optional feature into a build failure.
        """
        assert generator.collect_requirements({
            'optionalDependencies': {'fsevents': '^2.0.0'},
        }) == {}

    def test_optional_dependencies_can_be_opted_into(self, provider):
        generator = NpmEbuildGenerator(metadata_provider=provider,
                                       include_optional=True)
        assert generator.collect_requirements({
            'optionalDependencies': {'fsevents': '^2.0.0'},
        }) == {'fsevents': '^2.0.0'}

    def test_dependencies_win_over_peers_of_the_same_name(self, generator):
        assert generator.collect_requirements({
            'dependencies': {'react': '^18.2.0'},
            'peerDependencies': {'react': '^17.0.0'},
        }) == {'react': '^18.2.0'}


class TestResolution:

    def test_resolves_to_the_highest_match(self, generator):
        assert generator.resolve_one('ansi-styles', '^4.1.0') == '4.3.0'

    def test_pins_never_select_a_filtered_version(self, generator):
        """
        The central correctness property. ansi-styles publishes
        4.9.0-dev.20240101, which is the highest semver match for ^4.1.0 but is
        hidden by gentoo-version. Resolving before filtering would pin RDEPEND
        to a version the overlay does not expose.
        """
        raw_best = '4.9.0-dev.20240101'
        from portage_pip_fuse.ecosystems.npm import semver
        assert semver.max_satisfying(
            list(ANSI_STYLES_PACKUMENT['versions']), '^4.1.0',
            include_prerelease=True) == raw_best
        assert generator.resolve_one('ansi-styles', '^4.1.0') == '4.3.0'

    @pytest.mark.parametrize('spec', [
        'latest', 'next', 'npm:vue-loader@^15.9.7',
        'git+https://github.com/u/r.git', 'file:../local', 'workspace:*',
        'https://example.com/p.tgz',
    ])
    def test_non_range_specifiers_are_unresolved(self, generator, spec):
        assert generator.resolve_one('anything', spec) is None

    def test_unknown_package_is_unresolved(self, generator):
        assert generator.resolve_one('no-such-package', '^1.0.0') is None

    def test_unsatisfiable_range_is_unresolved(self, generator):
        assert generator.resolve_one('ansi-styles', '^99.0.0') is None

    def test_without_a_provider_nothing_resolves(self):
        generator = NpmEbuildGenerator(metadata_provider=None)
        assert generator.resolve_one('ansi-styles', '^4.0.0') is None

    def test_resolve_dependencies_splits_resolved_and_not(self, generator):
        manifest = {
            'dependencies': {'ansi-styles': '^4.1.0', 'mystery': 'latest'},
        }
        pins, unresolved = generator.resolve_dependencies(manifest)
        assert pins == [('ansi-styles', '4.3.0')]
        assert unresolved == [('mystery', 'latest')]


class TestAtoms:

    def test_uses_pms_version_and_tilde(self, generator):
        assert generator._atom('chalk', '4.1.2') == '~dev-nodejs/chalk-4.1.2'

    def test_translates_scoped_names(self, generator):
        assert generator._atom('@vue/cli-service', '5.0.8') == \
            '~dev-nodejs/vue+cli-service-5.0.8'

    def test_prerelease_uses_the_pms_spelling(self, generator):
        """
        RDEPEND compares PMS versions; NPM_DEPS carries upstream ones. Mixing
        the two spellings is the mistake this guards.
        """
        assert generator._atom('pkg', '1.0.0-beta.1') == '~dev-nodejs/pkg-1.0.0_beta1'

    def test_untranslatable_version_yields_no_atom(self, generator):
        assert generator._atom('pkg', '1.0.0-next.5') is None

    def test_tilde_tolerates_revision_bumps(self, generator):
        """'~' matches any revision of the version, unlike '='."""
        assert generator._atom('chalk', '4.1.2').startswith('~')


class TestNodeFloor:

    @pytest.mark.parametrize('requirement,expected', [
        ('>=10', '>=net-libs/nodejs-18'),        # below default, so left alone
        ('>=0.10.0', '>=net-libs/nodejs-18'),
        ('>=18', '>=net-libs/nodejs-18'),
        ('>=20', '>=net-libs/nodejs-20'),
        ('^20.9.0 || >=21.1.0', '>=net-libs/nodejs-20'),
        ('>=22.0.0', '>=net-libs/nodejs-22'),
    ])
    def test_floor_comes_from_the_declared_range(self, generator,
                                                 requirement, expected):
        assert generator._node_floor({'engines': {'node': requirement}}) == expected

    def test_floor_ignores_the_installed_node(self, generator):
        """
        An earlier version derived the floor from the lowest *installed*
        satisfying version, so a package declaring '>=10' claimed it needed the
        build host's Node 22.
        """
        with patch.dict(os.environ, {'NODE_VERSIONS': '22.22.2'}):
            assert generator._node_floor(
                {'engines': {'node': '>=10'}}) == '>=net-libs/nodejs-18'

    @pytest.mark.parametrize('manifest', [
        {}, {'engines': None}, {'engines': ['node >=0.4']},
        {'engines': {'npm': '>=8'}}, {'engines': {'node': 'garbage'}},
    ])
    def test_falls_back_to_the_default(self, generator, manifest):
        assert generator._node_floor(manifest) == generator.node_dep


class TestEbuildGeneration:

    def _ebuild(self, generator, version='4.1.2'):
        manifest = dict(CHALK_PACKUMENT['versions'][version])
        manifest.update({'description': 'Terminal string styling done right',
                         'license': 'MIT',
                         'homepage': 'https://github.com/chalk/chalk#readme'})
        return generator.generate_ebuild(manifest, version, 'chalk')

    def test_shape(self, generator):
        text = self._ebuild(generator)
        assert text.startswith('# Copyright')
        assert 'EAPI=8' in text
        assert 'inherit npm' in text
        assert 'NPM_PN="chalk"' in text
        assert text.endswith('\n')

    def test_metadata_comes_from_the_full_manifest(self, generator):
        """
        The abbreviated packument omits description and license; using it would
        describe every package by its own name and license it
        all-rights-reserved.
        """
        text = self._ebuild(generator)
        assert 'DESCRIPTION="Terminal string styling done right"' in text
        assert 'LICENSE="MIT"' in text
        assert 'HOMEPAGE="https://github.com/chalk/chalk#readme"' in text

    def test_npm_deps_uses_upstream_versions(self, generator):
        text = self._ebuild(generator)
        deps = re.search(r'^NPM_DEPS="([^"]*)"$', text, re.M).group(1).split()
        assert sorted(deps) == ['ansi-styles@4.3.0', 'supports-color@7.2.0']

    def test_rdepend_uses_pms_atoms(self, generator):
        text = self._ebuild(generator)
        assert '~dev-nodejs/ansi-styles-4.3.0' in text
        assert '~dev-nodejs/supports-color-7.2.0' in text
        assert '>=net-libs/nodejs-18' in text

    def test_npm_deps_and_rdepend_agree(self, generator):
        """The eclass links what RDEPEND guarantees will be installed."""
        text = self._ebuild(generator)
        deps = re.search(r'^NPM_DEPS="([^"]*)"$', text, re.M).group(1).split()
        atoms = set(re.findall(r'~dev-nodejs/(\S+)', text))
        for spec in deps:
            name, _, version = spec.rpartition('@')
            expected = '%s-%s' % (nt.npm_to_gentoo(name),
                                  vt.translate_version(version))
            assert expected in atoms, spec
        assert len(deps) == len(atoms)

    def test_npm_pv_only_when_spellings_differ(self, generator):
        text = self._ebuild(generator)
        assert 'NPM_PV=' not in text, 'redundant when PV equals the npm version'

        manifest = {'name': 'pkg', 'version': '1.0.0-beta.1', 'dist': {}}
        prerelease = generator.generate_ebuild(manifest, '1.0.0-beta.1', 'pkg')
        assert 'NPM_PV="1.0.0-beta.1"' in prerelease

    def test_keywords_from_os_cpu(self, generator):
        manifest = {'name': 'pkg', 'version': '1.0.0', 'dist': {},
                    'os': ['linux'], 'cpu': ['x64']}
        assert 'KEYWORDS="~amd64"' in generator.generate_ebuild(
            manifest, '1.0.0', 'pkg')

    def test_windows_only_package_gets_empty_keywords(self, generator):
        manifest = {'name': 'pkg', 'version': '1.0.0', 'dist': {},
                    'os': ['win32']}
        assert 'KEYWORDS=""' in generator.generate_ebuild(manifest, '1.0.0', 'pkg')

    def test_unresolved_dependencies_are_recorded_as_comments(self, generator):
        manifest = {'name': 'pkg', 'version': '1.0.0', 'dist': {},
                    'dependencies': {'aliased': 'npm:other@^1.0.0'}}
        text = generator.generate_ebuild(manifest, '1.0.0', 'pkg')
        assert 'Unresolved dependencies' in text
        assert 'aliased: npm:other@^1.0.0' in text

    def test_dependency_free_package_omits_rdepend_block(self, generator):
        manifest = {'name': 'pkg', 'version': '1.0.0', 'dist': {}}
        text = generator.generate_ebuild(manifest, '1.0.0', 'pkg')
        assert 'NPM_DEPS' not in text
        assert 'RDEPEND' not in text

    def test_untranslatable_version_raises(self, generator):
        manifest = {'name': 'pkg', 'version': '1.0.0-next.5', 'dist': {}}
        with pytest.raises(ValueError, match='no PMS equivalent'):
            generator.generate_ebuild(manifest, '1.0.0-next.5', 'pkg')

    def test_node_dep_declared_only_when_above_default(self, generator):
        low = generator.generate_ebuild(
            {'name': 'p', 'version': '1.0.0', 'dist': {},
             'engines': {'node': '>=10'}}, '1.0.0', 'p')
        assert 'NPM_NODE_DEP=' not in low

        high = generator.generate_ebuild(
            {'name': 'p', 'version': '1.0.0', 'dist': {},
             'engines': {'node': '>=22'}}, '1.0.0', 'p')
        assert 'NPM_NODE_DEP=">=net-libs/nodejs-22"' in high


class TestLicenseHandling:

    @pytest.mark.parametrize('manifest,expected', [
        ({'license': 'MIT'}, 'MIT'),
        ({'license': 'Apache-2.0'}, 'Apache-2.0'),
        ({'license': 'BSD-3-Clause'}, 'BSD'),
        ({'license': {'type': 'MIT'}}, 'MIT'),
        ({'licenses': ['MIT']}, 'MIT'),
        ({'licenses': [{'type': 'MIT'}, {'type': 'Apache-2.0'}]},
         'MIT Apache-2.0'),
        ({}, 'all-rights-reserved'),
        ({'license': 'NotAKnownLicense'}, 'all-rights-reserved'),
    ])
    def test_translates_every_shape_npm_uses(self, generator, manifest, expected):
        assert generator._license(manifest) == expected


class TestEscaping:

    @pytest.mark.parametrize('raw,expected', [
        ('plain text', 'plain text'),
        ('a "quoted" word', 'a \\"quoted\\" word'),
        ('cost $5', 'cost \\$5'),
        ('run `cmd`', 'run \\`cmd\\`'),
        ('back\\slash', 'back\\\\slash'),
        ('multi\n  line', 'multi line'),
    ])
    def test_escapes_for_bash(self, raw, expected):
        assert NpmEbuildGenerator._escape(raw) == expected

    def test_description_with_quotes_stays_valid_bash(self, generator):
        manifest = {'name': 'pkg', 'version': '1.0.0', 'dist': {},
                    'description': 'He said "hi" and $PATH and `date`'}
        text = generator.generate_ebuild(manifest, '1.0.0', 'pkg')
        line = [ln for ln in text.splitlines() if ln.startswith('DESCRIPTION=')][0]
        # Round-trip through bash to prove the assignment parses.
        result = subprocess.run(['bash', '-c', '%s; printf %%s "$DESCRIPTION"' % line],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout == 'He said "hi" and $PATH and `date`'


class TestManifestEntry:

    def test_generates_a_dist_line(self, generator):
        manifest = CHALK_PACKUMENT['versions']['4.1.2']
        entry = generator.generate_manifest_entry(manifest, '4.1.2', 'chalk',
                                                  size=11577)
        assert entry.startswith('DIST chalk-4.1.2.tgz 11577 SHA512 ')
        assert len(entry.rsplit(' ', 1)[1]) == 128

    def test_filename_matches_the_eclass_rename(self, generator):
        """
        The eclass renames to ${P}.tgz because scoped packages publish a
        scope-less basename that would collide in DISTDIR.
        """
        manifest = {'name': '@vue/cli-service', 'version': '5.0.8',
                    'dist': {'integrity': _integrity()}}
        entry = generator.generate_manifest_entry(manifest, '5.0.8',
                                                  'vue+cli-service', size=100)
        assert entry.startswith('DIST vue+cli-service-5.0.8.tgz 100 ')

    def test_prerelease_filename_uses_pms_version(self, generator):
        manifest = {'name': 'pkg', 'version': '1.0.0-beta.1',
                    'dist': {'integrity': _integrity()}}
        entry = generator.generate_manifest_entry(manifest, '1.0.0-beta.1',
                                                  'pkg', size=1)
        assert 'pkg-1.0.0_beta1.tgz' in entry

    def test_falls_back_to_sha1_for_pre_integrity_packages(self, generator):
        manifest = {'name': 'old', 'version': '1.0.0',
                    'dist': {'shasum': 'a' * 40}}
        entry = generator.generate_manifest_entry(manifest, '1.0.0', 'old',
                                                  size=10)
        assert entry == 'DIST old-1.0.0.tgz 10 SHA1 ' + 'a' * 40

    def test_no_size_means_no_entry(self, generator):
        manifest = CHALK_PACKUMENT['versions']['4.1.2']
        assert generator.generate_manifest_entry(
            manifest, '4.1.2', 'chalk') is None

    def test_size_is_looked_up_when_not_supplied(self, provider):
        provider.sizes[('chalk', '4.1.2')] = 999
        generator = NpmEbuildGenerator(metadata_provider=provider)
        entry = generator.generate_manifest_entry(
            CHALK_PACKUMENT['versions']['4.1.2'], '4.1.2', 'chalk')
        assert ' 999 ' in entry
        assert provider.size_calls == [('chalk', '4.1.2')]

    def test_no_digest_means_no_entry(self, generator):
        manifest = {'name': 'p', 'version': '1.0.0', 'dist': {}}
        assert generator.generate_manifest_entry(
            manifest, '1.0.0', 'p', size=5) is None


class TestPlugin:

    def test_identity(self):
        plugin = NpmPlugin()
        assert plugin.name == 'npm'
        assert plugin.default_category == 'dev-nodejs'
        assert plugin.default_repo_location == '/var/db/repos/npm'
        assert plugin.repo_name == 'portage-npm-fuse'

    def test_registered_and_discoverable(self):
        from portage_pip_fuse.plugin import PluginRegistry, ensure_plugins_discovered
        ensure_plugins_discovered()
        assert 'npm' in PluginRegistry.get_all()
        assert isinstance(PluginRegistry.get('npm'), NpmPlugin)

    def test_listed_in_available_ecosystems(self):
        from portage_pip_fuse.ecosystems import AVAILABLE_ECOSYSTEMS
        assert 'npm' in AVAILABLE_ECOSYSTEMS

    def test_publishes_profiles_categories(self):
        """
        dev-python and dev-ruby exist in ::gentoo; dev-nodejs does not, so
        portage rejects packages in it unless the overlay declares the category.
        """
        files = NpmPlugin().get_static_files()
        assert files['/profiles/categories'] == b'dev-nodejs\n'

    def test_static_files_include_the_usual_repo_metadata(self):
        files = NpmPlugin().get_static_files()
        assert '/profiles/repo_name' in files
        assert '/metadata/layout.conf' in files

    def test_static_dirs_include_the_category_and_sys_controls(self):
        dirs = NpmPlugin().get_static_dirs()
        assert '/dev-nodejs' in dirs
        assert '/.sys/node-compat/dev-nodejs' in dirs
        assert '/.sys/RDEPEND-patch/dev-nodejs' in dirs

    def test_no_source_providers_by_design(self):
        """
        Every npm package has exactly one source and the eclass builds SRC_URI
        itself, so there is no sdist/wheel/git choice to arbitrate.
        """
        assert NpmPlugin().get_source_providers() == []

    def test_compat_variable(self):
        generator = NpmPlugin().get_ebuild_generator()
        assert generator.get_compat_variable() == 'NPM_NODE_DEP'

    def test_inherits_only_the_npm_eclass(self):
        generator = NpmPlugin().get_ebuild_generator()
        assert generator.get_inherit_eclasses({}) == ['npm']

    def test_generator_uses_the_plugin_category(self):
        generator = NpmPlugin().get_ebuild_generator()
        assert generator.category == 'dev-nodejs'

    def test_default_version_filters(self):
        names = [type(f).__name__ for f in NpmPlugin().get_version_filters()]
        assert sorted(names) == ['GentooVersionFilter', 'NodeCompatFilter']


class TestEclassIntegration:
    """
    Drive the eclass with the generator's own output, so a mismatch between what
    the generator emits and what the eclass consumes cannot pass unnoticed.
    """

    def test_generated_variables_produce_the_expected_store_layout(self, tmp_path,
                                                                   generator):
        pytest.importorskip('pytest')
        harness = os.path.join(REPO_ROOT, 'tests', 'npm_eclass_harness.sh')
        eclass = os.path.join(REPO_ROOT, 'eclass', 'npm.eclass')
        if not (os.path.exists(harness) and os.path.exists(eclass)):
            pytest.skip('eclass harness not available')

        manifest = dict(CHALK_PACKUMENT['versions']['4.1.2'])
        text = generator.generate_ebuild(manifest, '4.1.2', 'chalk')
        npm_deps = re.search(r'^NPM_DEPS="([^"]*)"$', text, re.M).group(1)

        work = tmp_path / 'work'
        (work / 'package').mkdir(parents=True)
        (work / 'package' / 'package.json').write_text(
            json.dumps({'name': 'chalk', 'version': '4.1.2'}))
        image = tmp_path / 'image'
        image.mkdir()

        env = dict(os.environ)
        env.update({'NPM_PN': 'chalk', 'NPM_PV': '4.1.2', 'PN': 'chalk',
                    'PV': '4.1.2', 'NPM_DEPS': npm_deps})
        result = subprocess.run(['bash', harness, eclass, str(work), str(image)],
                                capture_output=True, text=True, env=env)
        assert result.returncode == 0, result.stderr

        modules = (image / 'usr/lib/node_modules/.pnpm/chalk@4.1.2/node_modules')
        # Every NPM_DEPS entry became a symlink to the matching store entry.
        for spec in npm_deps.split():
            name, _, version = spec.rpartition('@')
            link = modules / name
            assert link.is_symlink(), spec
            assert '%s@%s' % (name, version) in os.readlink(link)

        # And every symlink is backed by an RDEPEND atom.
        atoms = set(re.findall(r'~dev-nodejs/(\S+)', text))
        for spec in npm_deps.split():
            name, _, version = spec.rpartition('@')
            assert '%s-%s' % (nt.npm_to_gentoo(name),
                              vt.translate_version(version)) in atoms


def test_doctests():
    results = doctest.testmod(npm_plugin, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed
