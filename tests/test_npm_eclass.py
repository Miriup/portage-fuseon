"""
Tests for eclass/npm.eclass.

Portage cannot be installed in most development environments, but the eclass's
real product -- the on-disk store layout and its symlink targets -- can be
verified without it. ``tests/npm_eclass_harness.sh`` stubs the portage bash
helpers and runs the eclass phases against a throwaway image directory; these
tests drive that harness and then check the result, including whether node
itself resolves modules through the tree.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import collections
import json
import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ECLASS = os.path.join(REPO_ROOT, 'eclass', 'npm.eclass')
HARNESS = os.path.join(REPO_ROOT, 'tests', 'npm_eclass_harness.sh')

STORE = 'usr/lib/node_modules/.pnpm'

pytestmark = pytest.mark.skipif(
    shutil.which('bash') is None, reason='bash required to run the eclass harness'
)


def _needs_node():
    if shutil.which('node') is None:
        pytest.skip('node required for module-resolution checks')


def _make_tarball_dir(base, name, version, main_source, bin_map=None,
                      bundled_dep=None):
    """Lay out an unpacked npm tarball, i.e. a directory containing package/."""
    work = base / ('wd_' + name.replace('/', '_').replace('@', ''))
    pkg = work / 'package'
    pkg.mkdir(parents=True)

    manifest = {'name': name, 'version': version, 'main': 'index.js'}
    if bin_map:
        manifest['bin'] = bin_map
    (pkg / 'package.json').write_text(json.dumps(manifest))
    (pkg / 'index.js').write_text(main_source)

    if bin_map:
        bindir = pkg / 'bin'
        bindir.mkdir(exist_ok=True)
        for rel in bin_map.values():
            target = pkg / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                'const t = require("../index.js");\n'
                'console.log(t.speak());\n'
            )

    if bundled_dep:
        bundled = pkg / 'node_modules' / bundled_dep
        bundled.mkdir(parents=True)
        (bundled / 'package.json').write_text('{"name":"%s"}' % bundled_dep)

    return work


def _install(work, image, npm_pn, npm_pv, pn, pv, deps='', npm_bin=None):
    """Run the eclass phases for one package into a shared image directory."""
    env = dict(os.environ)
    env.update({
        'NPM_PN': npm_pn, 'NPM_PV': npm_pv,
        'PN': pn, 'PV': pv, 'NPM_DEPS': deps,
    })
    if npm_bin is not None:
        env['NPM_BIN'] = npm_bin

    result = subprocess.run(
        ['bash', HARNESS, ECLASS, str(work), str(image)],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, \
        'harness failed:\nstdout:\n%s\nstderr:\n%s' % (result.stdout, result.stderr)
    assert 'harness: ok' in result.stdout
    return result


@pytest.fixture
def image(tmp_path):
    target = tmp_path / 'image'
    target.mkdir()
    return target


class TestStorePaths:

    def test_unscoped_package_lands_at_its_own_root(self, tmp_path, image):
        work = _make_tarball_dir(tmp_path, 'chalk', '4.1.2',
                                 'module.exports = {};')
        _install(work, image, 'chalk', '4.1.2', 'chalk', '4.1.2')

        pkg = image / STORE / 'chalk@4.1.2' / 'node_modules' / 'chalk'
        assert (pkg / 'package.json').is_file()
        assert (pkg / 'index.js').is_file()
        # Regression: cp -R into a pre-created destination nested the tree as
        # <name>/package/, because ${NPM_PN%/*} leaves an unscoped name intact.
        assert not (pkg / 'package').exists(), 'package tree nested one level too deep'

    def test_scoped_package_keeps_its_scope_directory(self, tmp_path, image):
        work = _make_tarball_dir(tmp_path, '@acme/tool', '1.0.0',
                                 'module.exports = {};')
        _install(work, image, '@acme/tool', '1.0.0', 'acme-tool', '1.0.0')

        # The store entry flattens the scope with '+', as pnpm does, so the
        # store stays one directory deep...
        entry = image / STORE / '@acme+tool@1.0.0'
        assert entry.is_dir()
        # ...but inside node_modules the real scope directory is preserved,
        # because that is what node resolves against.
        pkg = entry / 'node_modules' / '@acme' / 'tool'
        assert (pkg / 'package.json').is_file()
        assert not (pkg / 'package').exists()

    def test_bundled_node_modules_is_removed(self, tmp_path, image):
        work = _make_tarball_dir(tmp_path, 'chalk', '4.1.2',
                                 'module.exports = {};', bundled_dep='sneaky')
        _install(work, image, 'chalk', '4.1.2', 'chalk', '4.1.2')

        pkg = image / STORE / 'chalk@4.1.2' / 'node_modules' / 'chalk'
        assert not (pkg / 'node_modules').exists(), \
            'bundled deps would shadow the store symlinks'


class TestDependencySymlinks:

    def test_unscoped_dep_target(self, tmp_path, image):
        work = _make_tarball_dir(tmp_path, 'chalk', '4.1.2',
                                 'module.exports = {};')
        _install(work, image, 'chalk', '4.1.2', 'chalk', '4.1.2',
                 deps='ansi-styles@4.3.0')

        link = image / STORE / 'chalk@4.1.2' / 'node_modules' / 'ansi-styles'
        assert link.is_symlink()
        assert os.readlink(link) == '../../ansi-styles@4.3.0/node_modules/ansi-styles'

    def test_scoped_dep_target_climbs_one_level_further(self, tmp_path, image):
        work = _make_tarball_dir(tmp_path, 'chalk', '4.1.2',
                                 'module.exports = {};')
        _install(work, image, 'chalk', '4.1.2', 'chalk', '4.1.2',
                 deps='@types/node@20.1.0')

        link = image / STORE / 'chalk@4.1.2' / 'node_modules' / '@types' / 'node'
        assert link.is_symlink()
        # One extra '..' because the link sits inside a @scope directory.
        assert os.readlink(link) == \
            '../../../@types+node@20.1.0/node_modules/@types/node'

    def test_multiple_deps(self, tmp_path, image):
        work = _make_tarball_dir(tmp_path, 'chalk', '4.1.2',
                                 'module.exports = {};')
        _install(work, image, 'chalk', '4.1.2', 'chalk', '4.1.2',
                 deps='ansi-styles@4.3.0 supports-color@7.2.0 @types/node@20.1.0')

        modules = image / STORE / 'chalk@4.1.2' / 'node_modules'
        assert (modules / 'ansi-styles').is_symlink()
        assert (modules / 'supports-color').is_symlink()
        assert (modules / '@types' / 'node').is_symlink()

    def test_no_deps_is_fine(self, tmp_path, image):
        work = _make_tarball_dir(tmp_path, 'leaf', '1.0.0',
                                 'module.exports = {};')
        _install(work, image, 'leaf', '1.0.0', 'leaf', '1.0.0', deps='')

        modules = image / STORE / 'leaf@1.0.0' / 'node_modules'
        assert [p.name for p in modules.iterdir()] == ['leaf']

    def test_malformed_spec_fails_loudly(self, tmp_path, image):
        """A spec without a version must abort rather than link nonsense."""
        work = _make_tarball_dir(tmp_path, 'chalk', '4.1.2',
                                 'module.exports = {};')
        env = dict(os.environ)
        env.update({'NPM_PN': 'chalk', 'NPM_PV': '4.1.2', 'PN': 'chalk',
                    'PV': '4.1.2', 'NPM_DEPS': 'ansi-styles'})
        result = subprocess.run(
            ['bash', HARNESS, ECLASS, str(work), str(image)],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode != 0
        assert 'cannot parse dependency spec' in result.stderr


class TestBinWrappers:

    def test_wrapper_is_generated_from_package_json(self, tmp_path, image):
        work = _make_tarball_dir(
            tmp_path, '@acme/tool', '1.0.0',
            'module.exports = { speak: () => "hi" };',
            bin_map={'acme-tool': 'bin/cli.js'})
        _install(work, image, '@acme/tool', '1.0.0', 'acme-tool', '1.0.0')

        wrapper = image / 'usr' / 'bin' / 'acme-tool'
        assert wrapper.is_file()
        assert os.access(wrapper, os.X_OK)

        body = wrapper.read_text()
        assert body.startswith('#!/bin/sh')
        # The wrapper execs the script in place; node's own upward search finds
        # the entry's node_modules, so no NODE_PATH is needed.
        assert 'NODE_PATH' not in body
        assert ('/usr/lib/node_modules/.pnpm/@acme+tool@1.0.0/node_modules/'
                '@acme/tool/bin/cli.js') in body
        assert '"$@"' in body

    def test_no_bin_field_yields_no_wrapper(self, tmp_path, image):
        work = _make_tarball_dir(tmp_path, 'leaf', '1.0.0',
                                 'module.exports = {};')
        _install(work, image, 'leaf', '1.0.0', 'leaf', '1.0.0')
        assert not (image / 'usr' / 'bin').exists()

    def test_npm_bin_dash_suppresses_wrappers(self, tmp_path, image):
        work = _make_tarball_dir(tmp_path, 'tool', '1.0.0',
                                 'module.exports = { speak: () => "hi" };',
                                 bin_map={'tool': 'bin/cli.js'})
        _install(work, image, 'tool', '1.0.0', 'tool', '1.0.0', npm_bin='-')
        assert not (image / 'usr' / 'bin').exists()

    def test_npm_bin_override_is_honoured(self, tmp_path, image):
        work = _make_tarball_dir(tmp_path, 'tool', '1.0.0',
                                 'module.exports = { speak: () => "hi" };',
                                 bin_map={'tool': 'bin/cli.js'})
        _install(work, image, 'tool', '1.0.0', 'tool', '1.0.0',
                 npm_bin='renamed:bin/cli.js')

        assert (image / 'usr' / 'bin' / 'renamed').is_file()
        assert not (image / 'usr' / 'bin' / 'tool').exists()

    def test_missing_bin_target_warns_and_continues(self, tmp_path, image):
        work = _make_tarball_dir(tmp_path, 'tool', '1.0.0',
                                 'module.exports = {};')
        result = _install(work, image, 'tool', '1.0.0', 'tool', '1.0.0',
                          npm_bin='ghost:bin/absent.js')

        assert 'points at missing' in result.stderr
        assert not (image / 'usr' / 'bin' / 'ghost').exists()


class TestNodeResolvesThroughTheStore:
    """
    The design rests on node's ordinary resolution walking up to the store
    entry's node_modules. These tests verify that with the real interpreter
    rather than by inspecting paths.
    """

    def _build_chain(self, tmp_path, image):
        leaf = _make_tarball_dir(tmp_path, 'ansi-styles', '4.3.0',
                                 'module.exports = { bold: "**" };')
        _install(leaf, image, 'ansi-styles', '4.3.0', 'ansi-styles', '4.3.0')

        mid = _make_tarball_dir(
            tmp_path, 'chalk', '4.1.2',
            'const a = require("ansi-styles");\n'
            'module.exports = { red: s => a.bold + s };')
        _install(mid, image, 'chalk', '4.1.2', 'chalk', '4.1.2',
                 deps='ansi-styles@4.3.0')

        top = _make_tarball_dir(
            tmp_path, '@acme/tool', '1.0.0',
            'const c = require("chalk");\n'
            'module.exports = { speak: () => c.red("hi") };',
            bin_map={'acme-tool': 'bin/cli.js'})
        _install(top, image, '@acme/tool', '1.0.0', 'acme-tool', '1.0.0',
                 deps='chalk@4.1.2')

    def test_transitive_require_chain(self, tmp_path, image):
        _needs_node()
        self._build_chain(tmp_path, image)

        entry = (image / STORE / '@acme+tool@1.0.0' / 'node_modules' /
                 '@acme' / 'tool')
        result = subprocess.run(
            ['node', '-e', 'process.stdout.write(require("./index.js").speak())'],
            cwd=entry, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        # @acme/tool -> chalk -> ansi-styles, all through relative symlinks.
        assert result.stdout == '**hi'

    def test_wrapper_runs(self, tmp_path, image):
        _needs_node()
        self._build_chain(tmp_path, image)

        # Rewrite the absolute store path to the staged image, which is what
        # portage's ${D} prefix would resolve to on a real merge.
        wrapper = image / 'usr' / 'bin' / 'acme-tool'
        staged = tmp_path / 'acme-tool'
        staged.write_text(
            wrapper.read_text().replace('/usr/lib', str(image / 'usr' / 'lib'))
        )
        staged.chmod(0o755)

        result = subprocess.run([str(staged)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert '**hi' in result.stdout


class TestVersionsAreCoInstallable:
    """
    The central claim of the layout: because store entries are version-keyed,
    two versions of one package occupy disjoint paths and portage sees no
    collision.
    """

    def _install_two_versions(self, tmp_path, image):
        for version, body in [('4.1.2', 'module.exports = { red: s => "v4:" + s };'),
                              ('5.0.0', 'module.exports = { red: s => "v5:" + s };')]:
            work = tmp_path / ('src' + version)
            work.mkdir()
            pkg = work / 'package'
            pkg.mkdir()
            (pkg / 'package.json').write_text(
                json.dumps({'name': 'chalk', 'version': version, 'main': 'index.js'}))
            (pkg / 'index.js').write_text(body)
            _install(work, image, 'chalk', version, 'chalk', version)

    def test_no_absolute_path_is_claimed_twice(self, tmp_path, image):
        self._install_two_versions(tmp_path, image)

        base = image / STORE
        owners = collections.defaultdict(set)
        for entry in base.iterdir():
            for dirpath, _dirs, files in os.walk(entry):
                for name in files:
                    installed = os.path.relpath(
                        os.path.join(dirpath, name), image)
                    owners['/' + installed].add(entry.name)

        collisions = {p: sorted(o) for p, o in owners.items() if len(o) > 1}
        assert collisions == {}, 'store entries must not share install paths'
        assert {'chalk@4.1.2', 'chalk@5.0.0'} <= {e.name for e in base.iterdir()}

    def test_each_version_resolves_to_its_own_code(self, tmp_path, image):
        _needs_node()
        self._install_two_versions(tmp_path, image)

        for version, expected in [('4.1.2', 'v4:x'), ('5.0.0', 'v5:x')]:
            module = (image / STORE / ('chalk@' + version) / 'node_modules' /
                      'chalk' / 'index.js')
            result = subprocess.run(
                ['node', '-e',
                 'process.stdout.write(require(process.argv[1]).red("x"))',
                 str(module)],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, result.stderr
            assert result.stdout == expected


class TestEclassMetadata:

    def _run_and_capture_vars(self, tmp_path, image, npm_pn, npm_pv, pn, pv):
        """Source the eclass in the harness and echo the globals it sets."""
        work = _make_tarball_dir(tmp_path, npm_pn, npm_pv, 'module.exports={};')
        script = (
            'source %s\n'
            'echo "SRC_URI=${SRC_URI}"\n'
            'echo "S=${S}"\n'
            'echo "SLOT=${SLOT}"\n'
            'echo "RDEPEND=${RDEPEND}"\n'
        ) % ECLASS
        env = dict(os.environ)
        env.update({
            'NPM_PN': npm_pn, 'NPM_PV': npm_pv, 'PN': pn, 'PV': pv,
            'P': '%s-%s' % (pn, pv), 'PF': '%s-%s' % (pn, pv),
            'EAPI': '8', 'WORKDIR': str(work), 'ED': str(image),
        })
        result = subprocess.run(
            ['bash', '-c', 'die() { echo "$*" >&2; exit 1; }; '
                           'EXPORT_FUNCTIONS() { :; }; ' + script],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, result.stderr
        return dict(
            line.split('=', 1) for line in result.stdout.strip().splitlines()
        )

    def test_src_uri_renames_the_tarball(self, tmp_path, image):
        """
        Scoped packages publish a scope-less basename, so two different scopes
        can produce the same filename. The '-> ${P}.tgz' rename is what keeps
        DISTDIR unambiguous.
        """
        variables = self._run_and_capture_vars(
            tmp_path, image, '@vue/cli-service', '5.0.8', 'vue-cli-service', '5.0.8')

        assert variables['SRC_URI'] == (
            'https://registry.npmjs.org/@vue/cli-service/-/cli-service-5.0.8.tgz'
            ' -> vue-cli-service-5.0.8.tgz'
        )

    def test_unscoped_src_uri(self, tmp_path, image):
        variables = self._run_and_capture_vars(
            tmp_path, image, 'chalk', '4.1.2', 'chalk', '4.1.2')
        assert variables['SRC_URI'] == (
            'https://registry.npmjs.org/chalk/-/chalk-4.1.2.tgz -> chalk-4.1.2.tgz'
        )

    def test_upstream_version_drives_the_url_not_the_pms_version(self, tmp_path, image):
        """
        A prerelease translates to a different PMS spelling, so SRC_URI must use
        NPM_PV. Using PV would request a version the registry does not have.
        """
        variables = self._run_and_capture_vars(
            tmp_path, image, 'pkg', '1.0.0-beta.1', 'pkg', '1.0.0_beta1')

        url, _, rename = variables['SRC_URI'].partition(' -> ')
        # The fetched URL must use the upstream spelling; requesting
        # 1.0.0_beta1 would 404.
        assert url.endswith('/pkg-1.0.0-beta.1.tgz'), url
        assert '1.0.0_beta1' not in url
        # The local filename is ${P}, so it correctly carries the PMS version.
        assert rename == 'pkg-1.0.0_beta1.tgz'
        assert variables['SLOT'] == '1.0.0_beta1'

    def test_slot_is_the_version(self, tmp_path, image):
        """SLOT=${PV} is what makes versions co-installable."""
        variables = self._run_and_capture_vars(
            tmp_path, image, 'chalk', '4.1.2', 'chalk', '4.1.2')
        assert variables['SLOT'] == '4.1.2'

    def test_s_points_at_the_tarball_subdirectory(self, tmp_path, image):
        variables = self._run_and_capture_vars(
            tmp_path, image, 'chalk', '4.1.2', 'chalk', '4.1.2')
        assert variables['S'].endswith('/package')

    def test_node_is_a_runtime_dependency(self, tmp_path, image):
        variables = self._run_and_capture_vars(
            tmp_path, image, 'chalk', '4.1.2', 'chalk', '4.1.2')
        assert 'net-libs/nodejs' in variables['RDEPEND']

    def test_rejects_unsupported_eapi(self, tmp_path):
        result = subprocess.run(
            ['bash', '-c',
             'die() { echo "$*" >&2; exit 1; }; EAPI=7; source %s' % ECLASS],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert 'not supported' in result.stderr
