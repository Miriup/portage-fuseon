"""
Tests for npm dependency-pin locking.

The behaviour under test is temporal: what an ebuild says today must still be
what it says tomorrow, after upstream publishes. Several tests therefore
simulate a second mount against a registry that has gained a version.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import doctest
import json

import pytest

from portage_pip_fuse.ecosystems.npm import resolution_lock as lock_module
from portage_pip_fuse.ecosystems.npm.plugin import NpmEbuildGenerator
from portage_pip_fuse.ecosystems.npm.resolution_lock import (
    STORAGE_KEY,
    ResolutionLockStore,
)

CATEGORY = 'dev-nodejs'


@pytest.fixture
def store():
    return ResolutionLockStore()


@pytest.fixture
def patch_file(tmp_path):
    return str(tmp_path / 'patches.json')


class FakeProvider:
    """A registry whose published versions can change between mounts."""

    def __init__(self, versions=('4.1.0', '4.3.0')):
        self.versions = list(versions)

    def get_versions_metadata(self, name):
        if name != 'ansi-styles':
            return {}
        return {v: {'name': name, 'version': v} for v in self.versions}

    def get_package_versions(self, name):
        return list(self.get_versions_metadata(name))


MANIFEST = {
    'name': 'chalk',
    'version': '4.1.2',
    'dependencies': {'ansi-styles': '^4.1.0'},
}


class TestRecording:

    def test_first_write_wins(self, store):
        assert store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0') == '1.0.0'
        assert store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '2.0.0') == '1.0.0'

    def test_get_pin(self, store):
        store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
        assert store.get_pin(CATEGORY, 'chalk', '4.1.2', 'dep') == '1.0.0'
        assert store.get_pin(CATEGORY, 'chalk', '4.1.2', 'other') is None
        assert store.get_pin(CATEGORY, 'chalk', '9.9.9', 'dep') is None

    def test_locks_are_per_package_version(self, store):
        store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
        assert store.record(CATEGORY, 'chalk', '5.0.0', 'dep', '2.0.0') == '2.0.0'
        assert store.get_pin(CATEGORY, 'chalk', '4.1.2', 'dep') == '1.0.0'

    def test_locks_are_per_package(self, store):
        store.record(CATEGORY, 'chalk', '1.0.0', 'dep', '1.0.0')
        assert store.record(CATEGORY, 'other', '1.0.0', 'dep', '2.0.0') == '2.0.0'

    def test_has_lock(self, store):
        assert not store.has_lock(CATEGORY, 'chalk', '4.1.2')
        store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
        assert store.has_lock(CATEGORY, 'chalk', '4.1.2')

    def test_get_returns_a_copy(self, store):
        store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
        pins = store.get(CATEGORY, 'chalk', '4.1.2')
        pins['dep'] = 'tampered'
        assert store.get_pin(CATEGORY, 'chalk', '4.1.2', 'dep') == '1.0.0'

    def test_dirty_tracking(self, store):
        assert not store.is_dirty
        store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
        assert store.is_dirty
        # Re-recording an existing pin changes nothing, so nothing to save.
        store._dirty = False
        store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '2.0.0')
        assert not store.is_dirty


class TestSetAndRemove:

    def test_set_replaces(self, store):
        store.set(CATEGORY, 'chalk', '4.1.2', {'a': '1.0.0', 'b': '2.0.0'})
        assert store.get(CATEGORY, 'chalk', '4.1.2') == {'a': '1.0.0', 'b': '2.0.0'}
        store.set(CATEGORY, 'chalk', '4.1.2', {'c': '3.0.0'})
        assert store.get(CATEGORY, 'chalk', '4.1.2') == {'c': '3.0.0'}

    def test_set_empty_clears(self, store):
        store.set(CATEGORY, 'chalk', '4.1.2', {'a': '1.0.0'})
        store.set(CATEGORY, 'chalk', '4.1.2', {})
        assert store.get(CATEGORY, 'chalk', '4.1.2') is None

    def test_set_validates(self, store):
        with pytest.raises(ValueError, match='string names'):
            store.set(CATEGORY, 'chalk', '4.1.2', {'a': 1})

    def test_remove(self, store):
        store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
        assert store.remove(CATEGORY, 'chalk', '4.1.2')
        assert not store.remove(CATEGORY, 'chalk', '4.1.2')
        assert store.get(CATEGORY, 'chalk', '4.1.2') is None

    def test_remove_package_clears_every_version(self, store):
        store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
        store.record(CATEGORY, 'chalk', '5.0.0', 'dep', '2.0.0')
        store.record(CATEGORY, 'other', '1.0.0', 'dep', '3.0.0')
        assert store.remove_package(CATEGORY, 'chalk') == 2
        assert store.get_pin(CATEGORY, 'other', '1.0.0', 'dep') == '3.0.0'

    def test_clear(self, store):
        store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
        store.record(CATEGORY, 'other', '1.0.0', 'dep', '2.0.0')
        assert store.clear() == 2
        assert store.list_all_locks() == []


class TestListing:

    @pytest.fixture
    def populated(self, store):
        store.record(CATEGORY, 'chalk', '4.1.2', 'a', '1.0.0')
        store.record(CATEGORY, 'chalk', '5.0.0', 'a', '2.0.0')
        store.record(CATEGORY, 'other', '1.0.0', 'b', '3.0.0')
        return store

    def test_categories(self, populated):
        assert populated.list_categories() == {CATEGORY}

    def test_packages(self, populated):
        assert populated.list_packages(CATEGORY) == {'chalk', 'other'}

    def test_versions(self, populated):
        assert populated.list_versions(CATEGORY, 'chalk') == {'4.1.2', '5.0.0'}

    def test_all_locks_sorted(self, populated):
        locks = populated.list_all_locks()
        assert [(c, p, v) for c, p, v, _ in locks] == [
            (CATEGORY, 'chalk', '4.1.2'),
            (CATEGORY, 'chalk', '5.0.0'),
            (CATEGORY, 'other', '1.0.0'),
        ]


class TestPersistence:

    def test_round_trip(self, patch_file):
        store = ResolutionLockStore(storage_path=patch_file)
        store.record(CATEGORY, 'chalk', '4.1.2', 'ansi-styles', '4.3.0')
        assert store.save()

        reloaded = ResolutionLockStore(storage_path=patch_file)
        assert reloaded.get_pin(CATEGORY, 'chalk', '4.1.2', 'ansi-styles') == '4.3.0'
        assert not reloaded.is_dirty

    def test_memory_only_save_is_a_no_op(self, store):
        store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
        assert store.save()

    def test_other_sections_are_preserved(self, patch_file):
        """The patch file is shared with every other patch store."""
        with open(patch_file, 'w') as handle:
            json.dump({'version': 3, 'mount_points': {
                '_default': {'slot_overrides': {'dev-ruby/x/1.0': '2'}}}}, handle)

        store = ResolutionLockStore(storage_path=patch_file)
        store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
        store.save()

        with open(patch_file) as handle:
            data = json.load(handle)
        assert data['mount_points']['_default']['slot_overrides'] == {
            'dev-ruby/x/1.0': '2'}
        assert STORAGE_KEY in data['mount_points']['_default']

    def test_mount_points_are_namespaced(self, patch_file):
        first = ResolutionLockStore(storage_path=patch_file,
                                    mount_point='/var/db/repos/npm')
        first.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
        first.save()

        second = ResolutionLockStore(storage_path=patch_file,
                                     mount_point='/mnt/other')
        assert second.get_pin(CATEGORY, 'chalk', '4.1.2', 'dep') is None

        same = ResolutionLockStore(storage_path=patch_file,
                                   mount_point='/var/db/repos/npm')
        assert same.get_pin(CATEGORY, 'chalk', '4.1.2', 'dep') == '1.0.0'

    def test_corrupt_file_does_not_raise(self, patch_file):
        with open(patch_file, 'w') as handle:
            handle.write('{ not json')
        store = ResolutionLockStore(storage_path=patch_file)
        assert store.list_all_locks() == []


class TestListMountPoints:
    """
    Namespace discovery. A mount records under its own mount point, so a tool
    that assumes '_default' sees nothing at all.
    """

    def test_missing_file(self, tmp_path):
        assert ResolutionLockStore.list_mount_points(
            str(tmp_path / 'absent.json')) == []

    def test_corrupt_file(self, patch_file):
        with open(patch_file, 'w') as handle:
            handle.write('{ not json')
        assert ResolutionLockStore.list_mount_points(patch_file) == []

    def test_finds_the_namespace_a_mount_wrote(self, patch_file):
        store = ResolutionLockStore(storage_path=patch_file,
                                    mount_point='/mnt/npm')
        store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
        store.save()

        assert ResolutionLockStore.list_mount_points(patch_file) == ['/mnt/npm']

    def test_sorted_across_namespaces(self, patch_file):
        for mount in ('/mnt/b', '/mnt/a'):
            store = ResolutionLockStore(storage_path=patch_file,
                                        mount_point=mount)
            store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
            store.save()

        assert ResolutionLockStore.list_mount_points(patch_file) == [
            '/mnt/a', '/mnt/b']

    def test_ignores_namespaces_without_locks(self, patch_file):
        """Other patch stores share the file and must not be reported."""
        with open(patch_file, 'w') as handle:
            json.dump({'version': 3, 'mount_points': {
                '/mnt/slots-only': {'slot_overrides': {'dev-ruby/x/1.0': '2'}},
                '/mnt/npm': {STORAGE_KEY: {'dev-nodejs/chalk/4.1.2':
                                           {'dep': '1.0.0'}}},
            }}, handle)

        assert ResolutionLockStore.list_mount_points(patch_file) == ['/mnt/npm']

    def test_malformed_entries_are_ignored_not_trusted(self, patch_file):
        """A bad entry must not surface later as a bogus pin."""
        with open(patch_file, 'w') as handle:
            json.dump({'version': 3, 'mount_points': {'_default': {
                STORAGE_KEY: {
                    'dev-nodejs/good/1.0.0': {'dep': '1.0.0'},
                    'dev-nodejs/bad/1.0.0': 'not-a-dict',
                    'dev-nodejs/worse/1.0.0': {'dep': 42},
                }}}}, handle)

        store = ResolutionLockStore(storage_path=patch_file)
        assert store.get_pin(CATEGORY, 'good', '1.0.0', 'dep') == '1.0.0'
        assert store.get(CATEGORY, 'bad', '1.0.0') is None
        assert store.get(CATEGORY, 'worse', '1.0.0') is None

    def test_save_reports_failure(self, tmp_path):
        store = ResolutionLockStore(storage_path=str(tmp_path / 'x.json'))
        store.record(CATEGORY, 'chalk', '4.1.2', 'dep', '1.0.0')
        from unittest.mock import patch as mock_patch
        with mock_patch('pathlib.Path.open', side_effect=OSError('full')):
            assert store.save() is False


class TestTextFormat:

    def test_round_trip(self, store):
        pins = {'ansi-styles': '4.3.0', '@types/node': '20.1.0'}
        store.set(CATEGORY, 'chalk', '4.1.2', pins)
        text = store.generate_patch_content(CATEGORY, 'chalk', '4.1.2')
        assert ResolutionLockStore.parse_patch_content(text) == pins

    def test_content_is_documented(self, store):
        text = store.generate_patch_content(CATEGORY, 'chalk', '4.1.2')
        assert text.startswith('#')
        assert 'dev-nodejs/chalk-4.1.2' in text

    @pytest.mark.parametrize('text,expected', [
        ('chalk 4.1.2\n', {'chalk': '4.1.2'}),
        ('== chalk 4.1.2\n', {'chalk': '4.1.2'}),
        ('# comment\n\nchalk 4.1.2\n', {'chalk': '4.1.2'}),
        ('  chalk   4.1.2  \n', {'chalk': '4.1.2'}),
        ('# only comments\n', {}),
        ('', {}),
    ])
    def test_parses(self, text, expected):
        assert ResolutionLockStore.parse_patch_content(text) == expected

    @pytest.mark.parametrize('text', ['chalk\n', 'chalk 1.0.0 extra\n',
                                      'chalk 1.0.0\nbroken\n'])
    def test_malformed_rejects_the_whole_file(self, text):
        """
        Half-applying an edited lock file would leave the rest resolving
        freely, which is exactly the drift the store exists to prevent.
        """
        assert ResolutionLockStore.parse_patch_content(text) is None

    def test_scoped_names_survive(self, store):
        pins = {'@vue/cli-service': '5.0.8'}
        store.set(CATEGORY, 'x', '1.0.0', pins)
        text = store.generate_patch_content(CATEGORY, 'x', '1.0.0')
        assert ResolutionLockStore.parse_patch_content(text) == pins


class TestGeneratorIntegration:
    """
    The behaviour the store exists for: an ebuild's RDEPEND must not change
    because someone else published a version.
    """

    def test_pin_survives_a_new_upstream_release(self, patch_file):
        first = ResolutionLockStore(storage_path=patch_file)
        generator = NpmEbuildGenerator(metadata_provider=FakeProvider(),
                                       resolution_lock=first)
        pins, _ = generator.resolve_dependencies(MANIFEST)
        assert pins == [('ansi-styles', '4.3.0')]
        assert first.save()

        # A remount, after upstream publishes something newer.
        second = ResolutionLockStore(storage_path=patch_file)
        newer = NpmEbuildGenerator(
            metadata_provider=FakeProvider(['4.1.0', '4.3.0', '4.9.0']),
            resolution_lock=second)
        assert newer.resolve_dependencies(MANIFEST)[0] == [('ansi-styles', '4.3.0')]

    def test_without_a_lock_the_pin_drifts(self):
        """The failure mode, asserted so the fix cannot be mistaken for a no-op."""
        generator = NpmEbuildGenerator(
            metadata_provider=FakeProvider(['4.1.0', '4.3.0', '4.9.0']),
            resolution_lock=None)
        assert generator.resolve_dependencies(MANIFEST)[0] == \
            [('ansi-styles', '4.9.0')]

    def test_unlocking_lets_it_move_again(self, patch_file):
        store = ResolutionLockStore(storage_path=patch_file)
        generator = NpmEbuildGenerator(metadata_provider=FakeProvider(),
                                       resolution_lock=store)
        generator.resolve_dependencies(MANIFEST)
        store.remove_package(CATEGORY, 'chalk')

        newer = NpmEbuildGenerator(
            metadata_provider=FakeProvider(['4.1.0', '4.3.0', '4.9.0']),
            resolution_lock=store)
        assert newer.resolve_dependencies(MANIFEST)[0] == [('ansi-styles', '4.9.0')]

    def test_ebuild_text_reflects_the_locked_pin(self, patch_file):
        store = ResolutionLockStore(storage_path=patch_file)
        store.set(CATEGORY, 'chalk', '4.1.2', {'ansi-styles': '4.1.0'})
        generator = NpmEbuildGenerator(
            metadata_provider=FakeProvider(['4.1.0', '4.3.0']),
            resolution_lock=store)

        text = generator.generate_ebuild(MANIFEST, '4.1.2', 'chalk')
        assert 'ansi-styles@4.1.0' in text
        assert '~dev-nodejs/ansi-styles-4.1.0' in text
        assert '4.3.0' not in text

    def test_a_locked_pin_needs_no_registry_lookup(self, patch_file):
        """Locks also make regeneration cheap, not only stable."""
        store = ResolutionLockStore(storage_path=patch_file)
        store.set(CATEGORY, 'chalk', '4.1.2', {'ansi-styles': '4.3.0'})

        class ExplodingProvider:
            def get_versions_metadata(self, name):
                raise AssertionError('should not consult the registry')

            def get_package_versions(self, name):
                raise AssertionError('should not consult the registry')

        generator = NpmEbuildGenerator(metadata_provider=ExplodingProvider(),
                                       resolution_lock=store)
        assert generator.resolve_dependencies(MANIFEST)[0] == \
            [('ansi-styles', '4.3.0')]

    def test_unidentifiable_manifest_resolves_unlocked(self, patch_file):
        """
        A manifest without a translatable name and version has no key to file a
        lock under, so it resolves normally rather than being stored wrongly.
        """
        store = ResolutionLockStore(storage_path=patch_file)
        generator = NpmEbuildGenerator(metadata_provider=FakeProvider(),
                                       resolution_lock=store)
        anonymous = {'dependencies': {'ansi-styles': '^4.1.0'}}
        assert generator.resolve_dependencies(anonymous)[0] == \
            [('ansi-styles', '4.3.0')]
        assert store.list_all_locks() == []

    def test_prerelease_package_keys_use_pms_versions(self, patch_file):
        store = ResolutionLockStore(storage_path=patch_file)
        generator = NpmEbuildGenerator(metadata_provider=FakeProvider(),
                                       resolution_lock=store)
        manifest = {'name': 'chalk', 'version': '5.0.0-beta.1',
                    'dependencies': {'ansi-styles': '^4.1.0'}}
        generator.resolve_dependencies(manifest)
        assert store.list_versions(CATEGORY, 'chalk') == {'5.0.0_beta1'}


class TestFilesystemIntegration:

    def test_filesystem_creates_a_lock_store(self, tmp_path):
        pytest.importorskip('fuse')
        from portage_pip_fuse.ecosystems.npm.filesystem import PortageNpmFS

        filesystem = PortageNpmFS(
            cache_dir=str(tmp_path / 'cache'),
            patch_file=str(tmp_path / 'patches.json'),
            node_versions=['22.22.2'],
        )
        assert isinstance(filesystem.resolution_lock, ResolutionLockStore)
        assert filesystem.ebuild_generator.resolution_lock is \
            filesystem.resolution_lock

    def test_no_locks_disables_it(self, tmp_path):
        pytest.importorskip('fuse')
        from portage_pip_fuse.ecosystems.npm.filesystem import PortageNpmFS

        filesystem = PortageNpmFS(
            cache_dir=str(tmp_path / 'cache'),
            no_locks=True,
            node_versions=['22.22.2'],
        )
        assert filesystem.resolution_lock is None

    def test_destroy_persists_recorded_pins(self, tmp_path):
        """
        Pins are recorded lazily as ebuilds are read, so without saving on
        unmount the first mount's decisions are lost.
        """
        pytest.importorskip('fuse')
        from portage_pip_fuse.ecosystems.npm.filesystem import PortageNpmFS

        patch_path = tmp_path / 'patches.json'
        filesystem = PortageNpmFS(
            cache_dir=str(tmp_path / 'cache'),
            patch_file=str(patch_path),
            node_versions=['22.22.2'],
        )
        filesystem.resolution_lock.record(CATEGORY, 'chalk', '4.1.2',
                                          'ansi-styles', '4.3.0')
        filesystem.destroy('/')

        reloaded = ResolutionLockStore(storage_path=str(patch_path))
        assert reloaded.get_pin(CATEGORY, 'chalk', '4.1.2', 'ansi-styles') == \
            '4.3.0'


class TestCliIntegration:

    def _run(self, argv, monkeypatch, expect=0):
        import sys
        from unittest.mock import patch as mock_patch
        from portage_pip_fuse.ecosystems.npm import cli as npm_cli

        monkeypatch.setenv('NODE_VERSIONS', '22.22.2')
        with mock_patch.object(sys, 'argv', ['portage-npm-fuse'] + argv):
            code = npm_cli.debug_command()
        assert code == expect
        return code

    def test_locks_reports_nothing_when_empty(self, tmp_path, capsys, monkeypatch):
        self._run(['debug', 'locks', '--patch-file',
                   str(tmp_path / 'patches.json')], monkeypatch)
        assert 'No dependency pins recorded' in capsys.readouterr().out

    def test_locks_lists_recorded_pins(self, tmp_path, capsys, monkeypatch):
        patch_path = tmp_path / 'patches.json'
        store = ResolutionLockStore(storage_path=str(patch_path))
        store.set(CATEGORY, 'chalk', '4.1.2', {'ansi-styles': '4.3.0'})
        store.save()

        self._run(['debug', 'locks', '--patch-file', str(patch_path)], monkeypatch)
        output = capsys.readouterr().out
        assert 'dev-nodejs/chalk-4.1.2' in output
        assert 'ansi-styles' in output

    def test_locks_json(self, tmp_path, capsys, monkeypatch):
        patch_path = tmp_path / 'patches.json'
        store = ResolutionLockStore(storage_path=str(patch_path))
        store.set(CATEGORY, 'chalk', '4.1.2', {'ansi-styles': '4.3.0'})
        store.save()

        self._run(['debug', 'locks', '--json', '--patch-file', str(patch_path)],
                  monkeypatch)
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]['pins'] == {'ansi-styles': '4.3.0'}

    def test_unlock_clears_and_saves(self, tmp_path, capsys, monkeypatch):
        patch_path = tmp_path / 'patches.json'
        store = ResolutionLockStore(storage_path=str(patch_path))
        store.set(CATEGORY, 'chalk', '4.1.2', {'ansi-styles': '4.3.0'})
        store.save()

        self._run(['debug', 'unlock', 'chalk', '--patch-file', str(patch_path)],
                  monkeypatch)
        assert 'Unlocked 1' in capsys.readouterr().out
        assert ResolutionLockStore(
            storage_path=str(patch_path)).list_all_locks() == []

    def test_unlock_nothing_to_do(self, tmp_path, capsys, monkeypatch):
        self._run(['debug', 'unlock', 'chalk', '--patch-file',
                   str(tmp_path / 'patches.json')], monkeypatch)
        assert 'Nothing to unlock' in capsys.readouterr().out

    def _write_at(self, patch_path, mount, package='chalk'):
        store = ResolutionLockStore(storage_path=str(patch_path),
                                    mount_point=mount)
        store.set(CATEGORY, package, '4.1.2', {'ansi-styles': '4.3.0'})
        store.save()

    def test_locks_finds_pins_written_by_a_mount(self, tmp_path, capsys,
                                                 monkeypatch):
        """
        A mount namespaces its locks under its own mount point. Reading only
        '_default' reported 'No dependency pins recorded' for pins that were
        plainly present in the file.
        """
        patch_path = tmp_path / 'patches.json'
        self._write_at(patch_path, '/mnt/npm')

        self._run(['debug', 'locks', '--patch-file', str(patch_path)],
                  monkeypatch)
        output = capsys.readouterr().out
        assert 'mount: /mnt/npm' in output
        assert 'dev-nodejs/chalk-4.1.2' in output

    def test_locks_groups_by_mount_point(self, tmp_path, capsys, monkeypatch):
        patch_path = tmp_path / 'patches.json'
        self._write_at(patch_path, '/mnt/a')
        self._write_at(patch_path, '/mnt/b', package='debug')

        self._run(['debug', 'locks', '--patch-file', str(patch_path)],
                  monkeypatch)
        output = capsys.readouterr().out
        assert output.index('mount: /mnt/a') < output.index('dev-nodejs/chalk')
        assert output.index('dev-nodejs/chalk') < output.index('mount: /mnt/b')

    def test_locks_mountpoint_narrows(self, tmp_path, capsys, monkeypatch):
        patch_path = tmp_path / 'patches.json'
        self._write_at(patch_path, '/mnt/a')
        self._write_at(patch_path, '/mnt/b', package='debug')

        self._run(['debug', 'locks', '--patch-file', str(patch_path),
                   '--mountpoint', '/mnt/b'], monkeypatch)
        output = capsys.readouterr().out
        assert 'dev-nodejs/debug-4.1.2' in output
        assert 'chalk' not in output

    def test_locks_json_reports_the_mount_point(self, tmp_path, capsys,
                                                monkeypatch):
        patch_path = tmp_path / 'patches.json'
        self._write_at(patch_path, '/mnt/npm')

        self._run(['debug', 'locks', '--json', '--patch-file', str(patch_path)],
                  monkeypatch)
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]['mount_point'] == '/mnt/npm'

    def test_unlock_clears_every_namespace(self, tmp_path, capsys, monkeypatch):
        patch_path = tmp_path / 'patches.json'
        self._write_at(patch_path, '/mnt/a')
        self._write_at(patch_path, '/mnt/b')

        self._run(['debug', 'unlock', 'chalk', '--patch-file', str(patch_path)],
                  monkeypatch)
        assert 'Unlocked 2' in capsys.readouterr().out
        assert ResolutionLockStore.list_mount_points(str(patch_path)) == []

    def test_unlock_mountpoint_spares_the_others(self, tmp_path, capsys,
                                                 monkeypatch):
        patch_path = tmp_path / 'patches.json'
        self._write_at(patch_path, '/mnt/a')
        self._write_at(patch_path, '/mnt/b')

        self._run(['debug', 'unlock', 'chalk', '--patch-file', str(patch_path),
                   '--mountpoint', '/mnt/a'], monkeypatch)
        capsys.readouterr()
        assert ResolutionLockStore.list_mount_points(str(patch_path)) == [
            '/mnt/b']


def test_doctests():
    results = doctest.testmod(lock_module, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed
