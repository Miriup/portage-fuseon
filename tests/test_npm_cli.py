"""
Tests for the npm CLI and the shared mount helpers.

Every test runs offline: emerge and the unmount tools are patched, and metadata
comes from a stub provider. The point is the translation from npm's vocabulary
into portage's, not whether emerge works.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import doctest
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from portage_pip_fuse import mount_helpers
from portage_pip_fuse.ecosystems.npm import cli as npm_cli


class StubProvider:
    VERSIONS = {
        'chalk': ['5.6.2', '4.1.2', '4.1.0', '5.0.0-next.1'],
        '@vue/cli-service': ['5.0.8', '5.0.7'],
    }

    def __init__(self, *args, **kwargs):
        pass

    def get_package_versions(self, name):
        return list(self.VERSIONS.get(name, []))

    def get_versions_metadata(self, name):
        return {v: {'name': name, 'version': v}
                for v in self.VERSIONS.get(name, [])}

    def get_version_info(self, name, version):
        return self.get_versions_metadata(name).get(version)

    def get_full_version_info(self, name, version):
        return self.get_version_info(name, version)

    def get_dist_tags(self, name):
        versions = self.VERSIONS.get(name)
        return {'latest': versions[0]} if versions else {}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Keep every test off the network and away from emerge."""
    monkeypatch.setattr(npm_cli, '_provider', lambda args: StubProvider())
    monkeypatch.setenv('NODE_VERSIONS', '22.22.2')


def run(argv, expect=0):
    """Invoke a subcommand as the CLI would, returning its exit code."""
    handlers = {
        'mount': npm_cli.mount_command,
        'unmount': npm_cli.unmount_command,
        'install': npm_cli.install_command,
        'npm': npm_cli.npm_command,
        'npx': npm_cli.npx_command,
        'debug': npm_cli.debug_command,
    }
    with patch.object(sys, 'argv', ['portage-npm-fuse'] + argv):
        code = handlers[argv[0]]()
    if expect is not None:
        assert code == expect, 'expected exit %r, got %r' % (expect, code)
    return code


class TestArgvHandling:

    def test_only_the_first_subcommand_token_is_dropped(self):
        """
        'npm install npm' must keep the package. The shared CLI filters every
        matching token, which would silently eat it.
        """
        with patch.object(sys, 'argv', ['prog', 'npm', 'install', 'npm']):
            assert npm_cli._argv_without('npm') == ['install', 'npm']

    def test_absent_subcommand_is_tolerated(self):
        with patch.object(sys, 'argv', ['prog', '--help']):
            assert npm_cli._argv_without('mount') == ['--help']


class TestSpecSplitting:

    @pytest.mark.parametrize('spec,expected', [
        ('chalk', ('chalk', None)),
        ('chalk@4.1.2', ('chalk', '4.1.2')),
        ('chalk@^4.0.0', ('chalk', '^4.0.0')),
        ('@vue/cli-service', ('@vue/cli-service', None)),
        ('@vue/cli-service@5.0.8', ('@vue/cli-service', '5.0.8')),
        ('@vue/cli-service@^5.0.0', ('@vue/cli-service', '^5.0.0')),
        ('@types/node', ('@types/node', None)),
    ])
    def test_splits_on_the_last_at(self, spec, expected):
        """A scoped name begins with '@', so a naive split would break it."""
        assert npm_cli._split_spec(spec) == expected


class TestAtoms:

    @pytest.mark.parametrize('name,version,expected', [
        ('chalk', None, 'dev-nodejs/chalk'),
        ('chalk', '4.1.2', '~dev-nodejs/chalk-4.1.2'),
        ('@vue/cli-service', '5.0.8', '~dev-nodejs/vue+cli-service-5.0.8'),
        ('socket.io', None, 'dev-nodejs/socket_io'),
        ('chalk', '1.0.0-beta.1', '~dev-nodejs/chalk-1.0.0_beta1'),
    ])
    def test_builds_atoms(self, name, version, expected):
        assert npm_cli._atom_for(name, version) == expected

    @pytest.mark.parametrize('name,version', [
        ('has space', None), ('', None), ('chalk', '1.0.0-next.5'),
        ('chalk', 'not-a-version'),
    ])
    def test_refuses_unexpressible(self, name, version):
        assert npm_cli._atom_for(name, version) is None


class TestNpmInstall:

    def test_bare_name(self, capsys):
        run(['npm', 'install', 'chalk', '--dry-run'])
        assert 'emerge --ask dev-nodejs/chalk' in capsys.readouterr().out

    def test_exact_version(self, capsys):
        run(['npm', 'install', 'chalk@4.1.2', '--dry-run'])
        assert '~dev-nodejs/chalk-4.1.2' in capsys.readouterr().out

    def test_range_is_resolved_against_the_registry(self, capsys):
        """A range must become a concrete version, or the atom names nothing."""
        run(['npm', 'install', 'chalk@^4.0.0', '--dry-run'])
        assert '~dev-nodejs/chalk-4.1.2' in capsys.readouterr().out

    def test_scoped_package(self, capsys):
        run(['npm', 'install', '@vue/cli-service@5.0.8', '--dry-run'])
        assert '~dev-nodejs/vue+cli-service-5.0.8' in capsys.readouterr().out

    def test_dist_tag_is_reported_not_guessed(self, capsys):
        run(['npm', 'install', 'chalk@latest', '--dry-run'], expect=1)
        captured = capsys.readouterr()
        assert 'not a version or range' in captured.err
        assert 'nothing to install' in captured.err

    def test_unsatisfiable_range_is_reported(self, capsys):
        run(['npm', 'install', 'chalk@^99.0.0', '--dry-run'], expect=1)
        assert 'satisfies' in capsys.readouterr().err

    def test_unknown_package_is_reported(self, capsys):
        run(['npm', 'install', 'no-such-package@^1.0.0', '--dry-run'], expect=1)
        assert 'no published versions' in capsys.readouterr().err

    def test_one_bad_spec_does_not_lose_the_others(self, capsys):
        run(['npm', 'install', 'chalk', 'chalk@latest', '--dry-run'])
        captured = capsys.readouterr()
        assert 'dev-nodejs/chalk' in captured.out
        assert 'not a version or range' in captured.err

    def test_pretend_replaces_ask(self, capsys):
        run(['npm', 'install', 'chalk', '--dry-run', '--pretend'])
        output = capsys.readouterr().out
        assert '--pretend' in output
        assert '--ask' not in output

    def test_no_ask(self, capsys):
        run(['npm', 'install', 'chalk', '--dry-run', '--no-ask'])
        assert '--ask' not in capsys.readouterr().out

    def test_global_flag_is_accepted_and_ignored(self, capsys):
        """portage installs system-wide, so -g is meaningless but harmless."""
        run(['npm', 'install', '-g', 'chalk', '--dry-run'])
        assert 'dev-nodejs/chalk' in capsys.readouterr().out

    @pytest.mark.parametrize('action', ['uninstall', 'remove', 'update', 'ci'])
    def test_other_subcommands_are_refused_with_advice(self, capsys, action):
        run(['npm', action, 'chalk'], expect=1)
        captured = capsys.readouterr()
        assert 'only "npm install" is supported' in captured.err
        assert 'emerge' in captured.err

    def test_no_arguments_shows_help(self, capsys):
        run(['npm'], expect=1)

    def test_emerge_is_invoked_when_not_a_dry_run(self):
        with patch('subprocess.run') as runner:
            runner.return_value = subprocess.CompletedProcess([], 0)
            run(['npm', 'install', 'chalk'])
            assert runner.call_args[0][0] == ['emerge', '--ask',
                                              'dev-nodejs/chalk']

    def test_missing_emerge_is_reported(self, capsys):
        with patch('subprocess.run', side_effect=FileNotFoundError):
            run(['npm', 'install', 'chalk'], expect=1)
        assert 'emerge not found' in capsys.readouterr().err


class TestLockfileParsing:

    def _write(self, tmp_path, data):
        path = tmp_path / 'package-lock.json'
        path.write_text(json.dumps(data))
        return path

    def test_lockfile_v3_direct_dependencies_only(self, tmp_path):
        """
        Only direct dependencies belong in the set. Each generated ebuild pins
        its own, so declaring the closure would duplicate portage's work.
        """
        path = self._write(tmp_path, {
            'name': 'app', 'lockfileVersion': 3,
            'packages': {
                '': {'dependencies': {'chalk': '^4.0.0'}},
                'node_modules/chalk': {'version': '4.1.2'},
                'node_modules/ansi-styles': {'version': '4.3.0'},
                'node_modules/supports-color': {'version': '7.2.0'},
            },
        })
        dependencies, problems = npm_cli.parse_lockfile(path)
        assert dependencies == {'chalk': '4.1.2'}
        assert problems == []

    def test_nested_transitive_copies_are_ignored(self, tmp_path):
        path = self._write(tmp_path, {
            'lockfileVersion': 3,
            'packages': {
                '': {'dependencies': {'a': '^1.0.0'}},
                'node_modules/a': {'version': '1.0.0'},
                'node_modules/a/node_modules/b': {'version': '2.0.0'},
            },
        })
        dependencies, _ = npm_cli.parse_lockfile(path)
        assert dependencies == {'a': '1.0.0'}

    def test_scoped_dependency(self, tmp_path):
        path = self._write(tmp_path, {
            'lockfileVersion': 3,
            'packages': {
                '': {'dependencies': {'@vue/cli-service': '^5.0.0'}},
                'node_modules/@vue/cli-service': {'version': '5.0.8'},
            },
        })
        dependencies, _ = npm_cli.parse_lockfile(path)
        assert dependencies == {'@vue/cli-service': '5.0.8'}

    def test_lockfile_v1_nested_form(self, tmp_path):
        path = self._write(tmp_path, {
            'lockfileVersion': 1,
            'dependencies': {'chalk': {'version': '4.1.2'}},
        })
        dependencies, _ = npm_cli.parse_lockfile(path)
        assert dependencies == {'chalk': '4.1.2'}

    def test_malformed_json_raises_with_the_path(self, tmp_path):
        path = tmp_path / 'package-lock.json'
        path.write_text('{ not json')
        with pytest.raises(ValueError, match='not valid JSON'):
            npm_cli.parse_lockfile(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match='cannot read'):
            npm_cli.parse_lockfile(tmp_path / 'absent.json')

    def test_unrecognised_shape_reports_a_problem(self, tmp_path):
        path = self._write(tmp_path, {'nothing': 'useful'})
        dependencies, problems = npm_cli.parse_lockfile(path)
        assert dependencies == {}
        assert problems


class TestSetNameDerivation:

    def test_from_the_lockfile_name(self, tmp_path):
        path = tmp_path / 'package-lock.json'
        path.write_text(json.dumps({'name': 'my-app'}))
        assert npm_cli._derive_set_name(path) == 'my-app-npm'

    def test_sanitises_characters_portage_rejects(self, tmp_path):
        path = tmp_path / 'package-lock.json'
        path.write_text(json.dumps({'name': '@acme/Web_App.v2'}))
        assert npm_cli._derive_set_name(path) == 'acme-web-app-v2-npm'

    def test_falls_back_to_the_directory(self, tmp_path):
        directory = tmp_path / 'My_Project'
        directory.mkdir()
        path = directory / 'package-lock.json'
        path.write_text('{}')
        assert npm_cli._derive_set_name(path) == 'my-project-npm'

    def test_unreadable_lockfile_still_yields_a_name(self, tmp_path):
        directory = tmp_path / 'proj'
        directory.mkdir()
        path = directory / 'package-lock.json'
        path.write_text('{ broken')
        assert npm_cli._derive_set_name(path) == 'proj-npm'


class TestInstallFromLockfile:

    def test_writes_a_set_and_emerges_it(self, tmp_path, capsys, monkeypatch):
        project = tmp_path / 'app'
        project.mkdir()
        (project / 'package-lock.json').write_text(json.dumps({
            'name': 'app', 'lockfileVersion': 3,
            'packages': {
                '': {'dependencies': {'chalk': '^4.0.0'}},
                'node_modules/chalk': {'version': '4.1.2'},
            },
        }))
        monkeypatch.chdir(project)

        run(['npm', 'install', '--dry-run', '--set-dir', str(tmp_path / 'sets')])
        output = capsys.readouterr().out
        assert 'app-npm' in output
        assert '~dev-nodejs/chalk-4.1.2' in output
        assert 'emerge @app-npm' in output

    def test_set_file_is_actually_written(self, tmp_path, monkeypatch):
        project = tmp_path / 'app'
        project.mkdir()
        (project / 'package-lock.json').write_text(json.dumps({
            'name': 'app', 'lockfileVersion': 3,
            'packages': {
                '': {'dependencies': {'chalk': '^4.0.0'}},
                'node_modules/chalk': {'version': '4.1.2'},
            },
        }))
        monkeypatch.chdir(project)
        set_dir = tmp_path / 'sets'

        with patch('subprocess.run') as runner:
            runner.return_value = subprocess.CompletedProcess([], 0)
            run(['npm', 'install', '--set-dir', str(set_dir)])

        content = (set_dir / 'app-npm').read_text()
        assert '~dev-nodejs/chalk-4.1.2' in content
        assert content.startswith('# Generated by portage-npm-fuse')

    def test_absent_lockfile_is_reported(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        run(['npm', 'install', '--dry-run'], expect=1)
        assert 'no package-lock.json' in capsys.readouterr().err


class TestInstallCommand:

    def test_dry_run_shows_the_conf(self, capsys):
        run(['install', '--dry-run'])
        output = capsys.readouterr().out
        assert '[portage-npm-fuse]' in output
        assert 'location = /var/db/repos/npm' in output
        assert 'auto-sync = no' in output

    def test_negative_priority_keeps_gentoo_winning(self, capsys):
        run(['install', '--dry-run'])
        assert 'priority = -50' in capsys.readouterr().out

    def test_writes_the_file(self, tmp_path, capsys):
        run(['install', '--repos-conf', str(tmp_path)])
        content = (tmp_path / 'portage-npm-fuse.conf').read_text()
        assert 'location = /var/db/repos/npm' in content

    def test_permission_error_is_reported(self, capsys):
        with patch('pathlib.Path.write_text', side_effect=PermissionError):
            run(['install', '--repos-conf', '/nonexistent-root'], expect=1)
        assert 'sudo' in capsys.readouterr().err


class TestUnmountCommand:

    def test_success(self, capsys, tmp_path):
        with patch('subprocess.run') as runner:
            runner.return_value = subprocess.CompletedProcess([], 0)
            run(['unmount', str(tmp_path)])
        assert 'Unmounted' in capsys.readouterr().out

    @pytest.mark.parametrize('message', [
        'umount: /mnt: not mounted.',
        'fusermount: entry for /mnt not found in /etc/mtab',
        'umount: /mnt: no such file or directory',
    ])
    def test_already_unmounted_is_success(self, capsys, tmp_path, message):
        """
        Unmounting something already unmounted reaches the desired state, so it
        must not be an error -- scripts and retries depend on that.
        """
        with patch('subprocess.run') as runner:
            runner.return_value = subprocess.CompletedProcess(
                [], 1, stderr=message)
            run(['unmount', str(tmp_path)])
        assert 'not mounted' in capsys.readouterr().out

    def test_genuine_failure_is_an_error(self, capsys, tmp_path):
        with patch('subprocess.run') as runner:
            runner.return_value = subprocess.CompletedProcess(
                [], 1, stderr='umount: /mnt: target is busy')
            run(['unmount', str(tmp_path)], expect=1)
        assert 'busy' in capsys.readouterr().err

    def test_force_tries_lazy_unmount_first(self, tmp_path):
        with patch('subprocess.run') as runner:
            runner.return_value = subprocess.CompletedProcess([], 0)
            run(['unmount', str(tmp_path), '--force'])
            assert '-z' in runner.call_args_list[0][0][0]


class TestDebugCommand:

    def test_node(self, capsys):
        run(['debug', 'node'])
        assert '22.22.2' in capsys.readouterr().out

    def test_node_json(self, capsys):
        run(['debug', 'node', '--json'])
        assert json.loads(capsys.readouterr().out) == ['22.22.2']

    def test_translate_scoped(self, capsys):
        run(['debug', 'translate', '@vue/cli-service'])
        output = capsys.readouterr().out
        assert 'vue+cli-service' in output
        assert '@vue/cli-service' in output

    def test_translate_reports_a_collision(self, capsys):
        run(['debug', 'translate', 'socket.io'])
        assert 'Collides with: socket_io' in capsys.readouterr().out

    def test_translate_json(self, capsys):
        run(['debug', 'translate', 'chalk', '--json'])
        payload = json.loads(capsys.readouterr().out)
        assert payload['gentoo'] == 'chalk'
        assert payload['atom'] == 'dev-nodejs/chalk'

    def test_translate_invalid_name(self, capsys):
        run(['debug', 'translate', 'has space'], expect=1)

    def test_versions_shows_both_spellings(self, capsys):
        run(['debug', 'versions', 'chalk'])
        output = capsys.readouterr().out
        assert '4.1.2' in output
        assert 'untranslatable' in output, 'the next.1 prerelease should say so'

    def test_versions_unknown_package(self, capsys):
        run(['debug', 'versions', 'no-such-package'], expect=1)

    def test_info(self, capsys):
        run(['debug', 'info', 'chalk', '--version', '4.1.2'])
        assert 'chalk' in capsys.readouterr().out

    def test_filter_reports_what_was_dropped(self, capsys):
        run(['debug', 'filter', 'chalk'])
        output = capsys.readouterr().out
        assert 'published' in output
        assert 'after filters' in output
        assert '5.0.0-next.1' in output, 'should name the dropped version'

    def test_deps(self, capsys):
        run(['debug', 'deps', 'chalk', '--version', '4.1.2'])
        assert 'requirement' in capsys.readouterr().out

    def test_name_required(self, capsys):
        run(['debug', 'versions'], expect=1)
        assert 'needs a package name' in capsys.readouterr().err


class TestNpxCommand:

    def test_explains_the_portage_equivalent(self, capsys):
        run(['npx', 'tsc', '--version'])
        output = capsys.readouterr().out
        assert 'emerge --ask dev-nodejs/tsc' in output
        assert 'tsc --version' in output

    def test_no_arguments_shows_help(self, capsys):
        run(['npx'], expect=1)


class TestMountArgumentValidation:

    def test_unknown_filter_is_rejected_before_mounting(self, capsys):
        run(['mount', '/tmp/nowhere', '--filter', 'nonsense'], expect=1)
        captured = capsys.readouterr()
        assert 'unknown filter' in captured.err
        assert 'gentoo-version' in captured.err, 'should list what is available'

    def test_unknown_no_filter_is_rejected(self, capsys):
        run(['mount', '/tmp/nowhere', '--no-filter', 'nonsense'], expect=1)
        assert 'unknown filter' in capsys.readouterr().err

    def test_missing_fuse_is_reported(self, capsys, tmp_path):
        with patch.object(mount_helpers, 'check_fuse_available',
                          return_value='FUSE is unavailable: test'):
            with patch.object(npm_cli, 'check_fuse_available',
                              return_value='FUSE is unavailable: test'):
                run(['mount', str(tmp_path)], expect=1)
        assert 'FUSE is unavailable' in capsys.readouterr().err


class TestMountHelpers:

    def test_validate_creates_the_mountpoint(self, tmp_path):
        target = tmp_path / 'a' / 'b'
        assert mount_helpers.validate_mountpoint(str(target)) == target.resolve()
        assert target.is_dir()

    def test_validate_rejects_a_file(self, tmp_path):
        path = tmp_path / 'file'
        path.write_text('x')
        with pytest.raises(ValueError, match='not a directory'):
            mount_helpers.validate_mountpoint(str(path))

    def test_validate_can_refuse_to_create(self, tmp_path):
        with pytest.raises(ValueError, match='does not exist'):
            mount_helpers.validate_mountpoint(str(tmp_path / 'absent'),
                                              create=False)

    def test_pid_file_round_trip(self, tmp_path):
        path = tmp_path / 'run' / 'npm.pid'
        with mount_helpers.PidFile(path):
            assert path.read_text() == str(os.getpid())
        assert not path.exists()

    def test_pid_file_removed_even_on_exception(self, tmp_path):
        path = tmp_path / 'npm.pid'
        with pytest.raises(RuntimeError):
            with mount_helpers.PidFile(path):
                raise RuntimeError('boom')
        assert not path.exists(), 'a stale PID file misleads the unmount command'

    def test_pid_file_none_is_inert(self):
        with mount_helpers.PidFile(None) as pid_file:
            pid_file.write()
            pid_file.remove()

    def test_remove_tolerates_an_already_deleted_file(self, tmp_path):
        path = tmp_path / 'npm.pid'
        pid_file = mount_helpers.PidFile(path)
        pid_file.write()
        path.unlink()
        pid_file.remove()

    def test_configure_logging_to_a_file(self, tmp_path):
        import logging
        logfile = tmp_path / 'logs' / 'npm.log'
        saved = logging.getLogger().handlers[:]
        try:
            mount_helpers.configure_logging(logfile=str(logfile))
            logging.getLogger('test').info('hello')
            assert 'hello' in logfile.read_text()
        finally:
            logging.getLogger().handlers = saved

    def test_configure_logging_rejects_an_unwritable_path(self):
        with patch('logging.FileHandler', side_effect=PermissionError):
            with pytest.raises(ValueError, match='Cannot write log file'):
                mount_helpers.configure_logging(logfile='/x/y.log')

    def test_check_fuse_returns_a_reason_or_none(self):
        result = mount_helpers.check_fuse_available()
        assert result is None or 'FUSE' in result or '/dev/fuse' in result


class TestEntryPointWiring:

    def test_main_npm_dispatches(self, capsys):
        from portage_pip_fuse.cli import main_npm
        with patch.object(sys, 'argv', ['portage-npm-fuse', '--version']):
            assert main_npm() == 0
        assert npm_cli.VERSION in capsys.readouterr().out

    def test_main_npm_help(self, capsys):
        from portage_pip_fuse.cli import main_npm
        with patch.object(sys, 'argv', ['portage-npm-fuse']):
            assert main_npm() == 0
        assert 'Subcommands:' in capsys.readouterr().out

    def test_main_npm_rejects_unknown_subcommand(self, capsys):
        from portage_pip_fuse.cli import main_npm
        with patch.object(sys, 'argv', ['portage-npm-fuse', 'bogus']):
            assert main_npm() == 1
        assert 'Unknown subcommand' in capsys.readouterr().err

    def test_declared_as_a_console_script(self):
        import pathlib
        content = (pathlib.Path(__file__).parent.parent
                   / 'pyproject.toml').read_text()
        assert 'portage-npm-fuse = "portage_pip_fuse.cli:main_npm"' in content

    def test_launcher_script_exists_and_is_executable(self):
        import pathlib
        launcher = pathlib.Path(__file__).parent.parent / 'bin' / 'portage-npm-fuse'
        assert launcher.is_file()
        assert os.access(launcher, os.X_OK)


def test_cli_doctests():
    results = doctest.testmod(npm_cli, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed


def test_mount_helpers_doctests():
    results = doctest.testmod(mount_helpers, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed
