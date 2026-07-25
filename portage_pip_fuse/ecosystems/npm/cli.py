"""
CLI commands for the npm ecosystem.

Provides ``portage-npm-fuse``: mount, unmount, install, npm, npx and debug.

Unlike the RubyGems plugin, whose mount/unmount/install commands live in the
shared ``cli.py``, everything npm-specific is kept here. The shared module is
already 2700 lines carrying two ecosystems; ``main_npm`` there is a thin
dispatcher into this file.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import argparse
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from portage_pip_fuse.constants import find_cache_dir
from portage_pip_fuse.mount_helpers import (
    PidFile,
    check_fuse_available,
    configure_logging,
    install_signal_handlers,
    validate_mountpoint,
)

from . import filters as npm_filters
from . import name_translator, node_targets, semver, version_translator

logger = logging.getLogger(__name__)

__all__ = [
    'mount_command',
    'unmount_command',
    'install_command',
    'npm_command',
    'npx_command',
    'debug_command',
]

VERSION = '0.1.0'
PROG = 'portage-npm-fuse'


def _plugin():
    """Get the npm plugin, imported lazily to keep --help fast."""
    from .plugin import NpmPlugin
    return NpmPlugin()


def _argv_without(subcommand: str) -> List[str]:
    """
    Return argv with the subcommand token removed.

    Only the first occurrence is dropped, so ``npm install npm`` keeps the
    package name. The shared CLI filters every match, which would eat a package
    that happens to share the subcommand's name.

    Examples:
        >>> import sys
        >>> saved = sys.argv
        >>> sys.argv = ['prog', 'npm', 'install', 'npm']
        >>> _argv_without('npm')
        ['install', 'npm']
        >>> sys.argv = ['prog', 'mount', '/mnt']
        >>> _argv_without('mount')
        ['/mnt']
        >>> sys.argv = saved
    """
    remaining = list(sys.argv[1:])
    if subcommand in remaining:
        remaining.remove(subcommand)
    return remaining


# ---------------------------------------------------------------------------
# mount
# ---------------------------------------------------------------------------

def mount_command() -> int:
    """Mount the npm overlay."""
    plugin = _plugin()
    available = sorted(npm_filters.NpmVersionFilterRegistry.get_all_filters())

    parser = argparse.ArgumentParser(
        prog='%s mount' % PROG,
        description='Mount the npm FUSE overlay',
    )
    parser.add_argument('mountpoint', nargs='?',
                        default=plugin.default_repo_location,
                        help='Where to mount (default: %(default)s)')
    parser.add_argument('-f', '--foreground', action='store_true',
                        help='Stay in the foreground')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='Enable debug logging and FUSE tracing')
    parser.add_argument('--cache-dir', help='Metadata cache directory')
    parser.add_argument('--cache-ttl', type=int, default=3600,
                        help='Metadata cache lifetime in seconds '
                             '(default: %(default)s)')
    parser.add_argument('--registry',
                        help='Registry base URL, for a mirror or private registry')
    parser.add_argument('--node-versions',
                        help='Override detected Node versions, space separated')
    parser.add_argument('--max-versions', type=int, default=0,
                        help='Ebuilds per package, newest first; 0 for all. '
                             'Some packages publish thousands of versions.')
    parser.add_argument('--filter', action='append', metavar='NAME',
                        help='Enable an optional filter (available: %s)'
                             % ', '.join(available))
    parser.add_argument('--no-filter', action='append', metavar='NAME',
                        help='Disable a default filter')
    parser.add_argument('--logfile',
                        help='Log to this file; needed when daemonised, which '
                             'has no usable stderr')
    parser.add_argument('--patch-file',
                        help='Where dependency-pin locks are stored '
                             '(default: the shared patches.json)')
    parser.add_argument('--no-locks', action='store_true',
                        help='Resolve dependencies afresh rather than reusing '
                             'locked pins. Ebuilds then change whenever a '
                             'dependency publishes a new version.')
    parser.add_argument('--pid-file', help='Write the mount process ID here')
    parser.add_argument('--no-allow-other', action='store_true',
                        help='Do not let other users read the mount. Portage '
                             'usually runs as root, so the default allows it.')

    args = parser.parse_args(_argv_without('mount'))

    unknown = set(args.filter or ()) | set(args.no_filter or ())
    unknown -= set(available)
    if unknown:
        print('Error: unknown filter(s): %s' % ', '.join(sorted(unknown)),
              file=sys.stderr)
        print('Available: %s' % ', '.join(available), file=sys.stderr)
        return 1

    problem = check_fuse_available()
    if problem:
        print('Error: %s' % problem, file=sys.stderr)
        return 1

    try:
        mountpoint = validate_mountpoint(args.mountpoint)
        configure_logging(debug=args.debug, logfile=args.logfile)
    except ValueError as exc:
        print('Error: %s' % exc, file=sys.stderr)
        return 1

    cache_dir = args.cache_dir or str(find_cache_dir(None))
    node_versions = args.node_versions.split() if args.node_versions else None

    filter_config = {
        'enabled_filters': args.filter or [],
        'disabled_filters': args.no_filter or [],
    }

    print('Mounting npm overlay at %s' % mountpoint)
    print('Cache directory: %s' % cache_dir)
    print('Node versions: %s' % ', '.join(
        node_versions or node_targets.get_node_versions()))
    if args.max_versions:
        print('Versions per package: %d (newest first)' % args.max_versions)
    if args.filter:
        print('Enabled filters: %s' % ', '.join(sorted(args.filter)))
    if args.no_filter:
        print('Disabled filters: %s' % ', '.join(sorted(args.no_filter)))
    print('Dependency pins: %s' % ('resolved afresh each time'
                                   if args.no_locks else 'locked once resolved'))
    print()
    print('Add the overlay to portage with: %s install' % PROG)

    pid_file = PidFile(Path(args.pid_file) if args.pid_file else None)
    install_signal_handlers(pid_file)

    from .filesystem import mount_npm_filesystem

    try:
        with pid_file:
            mount_npm_filesystem(
                str(mountpoint),
                foreground=args.foreground,
                debug=args.debug,
                cache_ttl=args.cache_ttl,
                cache_dir=cache_dir,
                filter_config=filter_config,
                node_versions=node_versions,
                registry=args.registry,
                max_versions=args.max_versions,
                patch_file=args.patch_file,
                no_locks=args.no_locks,
                allow_other=not args.no_allow_other,
            )
    except RuntimeError as exc:
        print('Error: mount failed: %s' % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    return 0


# ---------------------------------------------------------------------------
# unmount
# ---------------------------------------------------------------------------

def unmount_command() -> int:
    """Unmount the npm overlay."""
    plugin = _plugin()

    parser = argparse.ArgumentParser(
        prog='%s unmount' % PROG,
        description='Unmount the npm FUSE overlay',
    )
    parser.add_argument('mountpoint', nargs='?',
                        default=plugin.default_repo_location,
                        help='Mount to release (default: %(default)s)')
    parser.add_argument('-f', '--force', action='store_true',
                        help='Force unmount even with open files')

    args = parser.parse_args(_argv_without('unmount'))
    mountpoint = str(Path(args.mountpoint).expanduser().resolve())

    # fusermount is the unprivileged path; umount is the fallback for a mount
    # made by root or when fuse's userspace tools are absent.
    attempts = [['fusermount', '-u', mountpoint]]
    if args.force:
        attempts.insert(0, ['fusermount', '-z', '-u', mountpoint])
    attempts.append(['umount', mountpoint])
    if args.force:
        attempts.append(['umount', '-l', mountpoint])

    last_error = None
    for command in attempts:
        try:
            result = subprocess.run(command, capture_output=True, text=True)
        except FileNotFoundError:
            continue
        if result.returncode == 0:
            print('Unmounted %s' % mountpoint)
            return 0
        last_error = result.stderr.strip() or result.stdout.strip()

    # Unmounting something already unmounted is the desired end state, so it is
    # reported as success. umount says 'not mounted'; fusermount says 'not found'
    # in mtab; a missing mountpoint says 'no such file'.
    if last_error and any(phrase in last_error.lower() for phrase in
                          ('not mounted', 'not found', 'no such file')):
        print('%s is not mounted' % mountpoint)
        return 0

    print('Error: could not unmount %s: %s' % (mountpoint, last_error or
                                               'no unmount tool available'),
          file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

def install_command() -> int:
    """Write a repos.conf entry for the overlay."""
    plugin = _plugin()

    parser = argparse.ArgumentParser(
        prog='%s install' % PROG,
        description='Create the portage repos.conf entry for the npm overlay',
    )
    parser.add_argument('--location', default=plugin.default_repo_location,
                        help='Overlay location (default: %(default)s)')
    parser.add_argument('--repos-conf', default='/etc/portage/repos.conf',
                        help='repos.conf directory (default: %(default)s)')
    parser.add_argument('--priority', type=int, default=-50,
                        help='Repository priority; negative keeps ::gentoo '
                             'winning for packages both provide '
                             '(default: %(default)s)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print what would be written')

    args = parser.parse_args(_argv_without('install'))

    content = (
        '# Generated by %s\n'
        '[%s]\n'
        'location = %s\n'
        'priority = %d\n'
        'auto-sync = no\n'
        'sync-type =\n'
        'sync-uri =\n'
    ) % (PROG, plugin.repo_name, args.location, args.priority)

    target = Path(args.repos_conf) / ('%s.conf' % plugin.repo_name)

    if args.dry_run:
        print('Would write %s:' % target)
        print()
        print(content)
        return 0

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    except PermissionError:
        print('Error: cannot write %s: permission denied. Try with sudo.'
              % target, file=sys.stderr)
        return 1
    except OSError as exc:
        print('Error: cannot write %s: %s' % (target, exc), file=sys.stderr)
        return 1

    print('Wrote %s' % target)
    print()
    print('Next: %s mount' % PROG)
    return 0


# ---------------------------------------------------------------------------
# npm / npx
# ---------------------------------------------------------------------------

def _atom_for(npm_name: str, version: Optional[str] = None,
              category: str = 'dev-nodejs') -> Optional[str]:
    """
    Build a portage atom for a package, optionally pinned to a version.

    Returns None when the name or version has no Gentoo equivalent, so the
    caller can report it rather than emit an atom portage cannot parse.

    Examples:
        >>> _atom_for('chalk')
        'dev-nodejs/chalk'
        >>> _atom_for('chalk', '4.1.2')
        '~dev-nodejs/chalk-4.1.2'
        >>> _atom_for('@vue/cli-service', '5.0.8')
        '~dev-nodejs/vue+cli-service-5.0.8'
        >>> _atom_for('chalk', '1.0.0-beta.1')
        '~dev-nodejs/chalk-1.0.0_beta1'
        >>> _atom_for('has space') is None
        True
        >>> _atom_for('chalk', '1.0.0-next.5') is None
        True
    """
    gentoo_name = name_translator.npm_to_gentoo(npm_name)
    if gentoo_name is None:
        return None

    if version is None:
        return '%s/%s' % (category, gentoo_name)

    pms_version = version_translator.translate_version(version)
    if pms_version is None:
        return None

    # '~' matches any revision of the version.
    return '~%s/%s-%s' % (category, gentoo_name, pms_version)


def _split_spec(spec: str) -> Tuple[str, Optional[str]]:
    """
    Split an ``npm install`` argument into a name and a version request.

    Splits on the last ``@`` so a scoped name, which begins with one, survives.

    Examples:
        >>> _split_spec('chalk')
        ('chalk', None)
        >>> _split_spec('chalk@4.1.2')
        ('chalk', '4.1.2')
        >>> _split_spec('@vue/cli-service')
        ('@vue/cli-service', None)
        >>> _split_spec('@vue/cli-service@5.0.8')
        ('@vue/cli-service', '5.0.8')
        >>> _split_spec('chalk@^4.0.0')
        ('chalk', '^4.0.0')
    """
    if spec.startswith('@'):
        scope, _, rest = spec[1:].partition('/')
        if '@' in rest:
            name, _, version = rest.rpartition('@')
            return '@%s/%s' % (scope, name), version
        return spec, None

    if '@' in spec:
        name, _, version = spec.rpartition('@')
        return name, version

    return spec, None


def _resolve_request(npm_name: str, request: Optional[str],
                     provider) -> Tuple[Optional[str], Optional[str]]:
    """
    Turn a version request into a concrete version.

    Returns ``(version, error)``. An exact version is used as given; a range is
    resolved against the registry so the atom names something that exists.
    """
    if request is None:
        return None, None

    if semver.parse_version(request) is not None:
        return request, None

    if not semver.is_range(request):
        return None, ('%r is not a version or range; dist-tags and URL '
                      'specifiers are not supported' % request)

    versions = provider.get_package_versions(npm_name)
    if not versions:
        return None, 'no published versions found for %s' % npm_name

    chosen = semver.max_satisfying(versions, request)
    if chosen is None:
        return None, 'no published version of %s satisfies %r' % (npm_name,
                                                                  request)
    return chosen, None


def npm_command() -> int:
    """Translate ``npm install`` into an emerge invocation."""
    parser = argparse.ArgumentParser(
        prog='%s npm' % PROG,
        description='Translate npm install commands into emerge',
        usage='%s npm install [options] [packages...]' % PROG,
    )
    parser.add_argument('args', nargs='*',
                        help="npm subcommand and packages, e.g. 'install chalk'")
    parser.add_argument('-g', '--global', dest='global_install',
                        action='store_true',
                        help='Accepted and ignored; portage installs system-wide')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print the emerge command instead of running it')
    parser.add_argument('--pretend', action='store_true',
                        help='Pass --pretend to emerge')
    parser.add_argument('--no-ask', action='store_true',
                        help='Do not pass --ask to emerge')
    parser.add_argument('--cache-dir', help='Metadata cache directory')
    parser.add_argument('--set-dir', default='/etc/portage/sets',
                        help='Where to write portage sets (default: %(default)s)')

    # argparse cannot interleave optionals with a nargs='*' positional, so
    # 'npm install -g chalk' -- the most common npm invocation there is -- leaves
    # 'chalk' in the unparsed remainder. Recover the positionals, while still
    # rejecting flags that really are unknown.
    args, remainder = parser.parse_known_args(_argv_without('npm'))
    for token in remainder:
        if token.startswith('-'):
            parser.error('unrecognized argument: %s' % token)
        args.args.append(token)

    if not args.args:
        parser.print_help()
        return 1

    action, packages = args.args[0], args.args[1:]

    if action in ('install', 'i', 'add'):
        if not packages:
            # 'npm install' with no arguments installs from the lockfile.
            return _install_from_lockfile(args)
        return _install_packages(args, packages)

    print('Error: only "npm install" is supported, got %r' % action,
          file=sys.stderr)
    print('Portage manages uninstall and update itself: use '
          '"emerge --deselect" or "emerge -u".', file=sys.stderr)
    return 1


def _provider(args):
    """Build a metadata provider for a CLI command."""
    from .plugin import NpmMetadataProvider
    return NpmMetadataProvider(cache_dir=args.cache_dir)


def _install_packages(args, specs: List[str]) -> int:
    """Translate named packages into an emerge invocation."""
    provider = _provider(args)
    atoms: List[str] = []
    problems: List[str] = []

    for spec in specs:
        npm_name, request = _split_spec(spec)

        version, error = _resolve_request(npm_name, request, provider)
        if error:
            problems.append('%s: %s' % (spec, error))
            continue

        atom = _atom_for(npm_name, version)
        if atom is None:
            problems.append('%s: no Gentoo equivalent for that name or version'
                            % spec)
            continue
        atoms.append(atom)

    for problem in problems:
        print('Warning: %s' % problem, file=sys.stderr)

    if not atoms:
        print('Error: nothing to install', file=sys.stderr)
        return 1

    return _run_emerge(args, atoms)


def _derive_set_name(lockfile: Path) -> str:
    """
    Derive a portage set name from a lockfile's project.

    Uses the lockfile's own ``name`` when present, else the directory name, and
    sanitises the result. The RubyGems bundle command skips sanitisation, so a
    directory containing a dot or underscore yields a set name portage will not
    accept; this follows the PyPI helper instead.

    Examples:
        >>> import tempfile, json, pathlib
        >>> directory = pathlib.Path(tempfile.mkdtemp()) / 'My_Project.v2'
        >>> directory.mkdir()
        >>> path = directory / 'package-lock.json'
        >>> _ = path.write_text(json.dumps({'name': 'My_App.v2'}))
        >>> _derive_set_name(path)
        'my-app-v2-npm'

        Falling back to the directory name when the lockfile is anonymous, with
        the same sanitisation applied:

        >>> _ = path.write_text('{}')
        >>> _derive_set_name(path)
        'my-project-v2-npm'

        A scoped lockfile name loses characters portage would reject:

        >>> _ = path.write_text(json.dumps({'name': '@acme/web-app'}))
        >>> _derive_set_name(path)
        'acme-web-app-npm'
    """
    name = ''
    try:
        with lockfile.open() as handle:
            name = (json.load(handle) or {}).get('name') or ''
    except (OSError, ValueError):
        name = ''

    if not name:
        name = lockfile.resolve().parent.name

    name = re.sub(r'[^a-zA-Z0-9-]', '-', name.lower())
    name = re.sub(r'-+', '-', name).strip('-')

    return '%s-npm' % (name or 'project')


def parse_lockfile(path: Path) -> Tuple[Dict[str, str], List[str]]:
    """
    Read direct dependencies and their resolved versions from a lockfile.

    Only *direct* dependencies are returned. The transitive closure is portage's
    job: each generated ebuild pins its own dependencies, so re-declaring the
    whole tree would duplicate work and could conflict with it.

    Supports lockfile versions 2 and 3, which key ``packages`` by install path,
    and version 1, which nests ``dependencies``.

    Args:
        path: Path to ``package-lock.json``

    Returns:
        ``(dependencies, problems)`` mapping npm name to resolved version

    Raises:
        ValueError: if the file cannot be read or parsed
    """
    try:
        with path.open() as handle:
            data = json.load(handle)
    except OSError as exc:
        raise ValueError('cannot read %s: %s' % (path, exc))
    except ValueError as exc:
        raise ValueError('%s is not valid JSON: %s' % (path, exc))

    problems: List[str] = []
    direct: Dict[str, str] = {}

    packages = data.get('packages')
    if isinstance(packages, dict):
        # The root entry names the direct dependencies; the rest of 'packages'
        # is the resolved closure, keyed by install path.
        root = packages.get('') or {}
        wanted = set()
        for field in ('dependencies', 'optionalDependencies'):
            wanted.update((root.get(field) or {}).keys())

        for key, entry in packages.items():
            if not key.startswith('node_modules/'):
                continue
            name = key[len('node_modules/'):]
            # A nested path means a transitive copy, not a top-level install.
            if 'node_modules/' in name:
                continue
            if wanted and name not in wanted:
                continue
            version = (entry or {}).get('version')
            if version:
                direct[name] = version

        if direct or wanted:
            return direct, problems

    legacy = data.get('dependencies')
    if isinstance(legacy, dict):
        for name, entry in legacy.items():
            version = (entry or {}).get('version')
            if version:
                direct[name] = version
        return direct, problems

    problems.append('no dependencies found; is this a package-lock.json?')
    return direct, problems


def _install_from_lockfile(args) -> int:
    """Write a portage set from package-lock.json and emerge it."""
    lockfile = Path.cwd() / 'package-lock.json'
    if not lockfile.is_file():
        print('Error: no package-lock.json in %s' % Path.cwd(), file=sys.stderr)
        print('Run "npm install" first to create one, or name packages '
              'explicitly.', file=sys.stderr)
        return 1

    try:
        dependencies, problems = parse_lockfile(lockfile)
    except ValueError as exc:
        print('Error: %s' % exc, file=sys.stderr)
        return 1

    for problem in problems:
        print('Warning: %s' % problem, file=sys.stderr)

    if not dependencies:
        print('Error: no direct dependencies found in %s' % lockfile,
              file=sys.stderr)
        return 1

    atoms: List[str] = []
    for npm_name, version in sorted(dependencies.items()):
        atom = _atom_for(npm_name, version)
        if atom is None:
            print('Warning: skipping %s@%s: no Gentoo equivalent'
                  % (npm_name, version), file=sys.stderr)
            continue
        atoms.append(atom)

    if not atoms:
        print('Error: no dependency could be expressed as a portage atom',
              file=sys.stderr)
        return 1

    set_name = _derive_set_name(lockfile)
    set_path = Path(args.set_dir) / set_name
    content = ''.join(
        ['# Generated by %s from %s\n' % (PROG, lockfile)]
        + ['%s\n' % atom for atom in atoms]
    )

    if args.dry_run:
        print('Would write %s:' % set_path)
        print()
        print(content)
        print('Would run: emerge @%s' % set_name)
        return 0

    try:
        set_path.parent.mkdir(parents=True, exist_ok=True)
        set_path.write_text(content)
    except PermissionError:
        print('Error: cannot write %s: permission denied. Try with sudo.'
              % set_path, file=sys.stderr)
        return 1
    except OSError as exc:
        print('Error: cannot write %s: %s' % (set_path, exc), file=sys.stderr)
        return 1

    print('Wrote %s with %d package(s)' % (set_path, len(atoms)))
    return _run_emerge(args, ['@%s' % set_name])


def _run_emerge(args, targets: List[str]) -> int:
    """Run emerge against the given targets."""
    command = ['emerge']
    if not args.no_ask and not args.pretend:
        command.append('--ask')
    if args.pretend:
        command.append('--pretend')
    command.extend(targets)

    if args.dry_run:
        print('Would run: %s' % ' '.join(command))
        return 0

    print('Running: %s' % ' '.join(command))
    try:
        return subprocess.run(command).returncode
    except FileNotFoundError:
        print('Error: emerge not found; this command needs a Gentoo system',
              file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def npx_command() -> int:
    """Translate ``npx <tool>`` into installing the tool that provides it."""
    parser = argparse.ArgumentParser(
        prog='%s npx' % PROG,
        description='Install the package providing a command, then run it',
    )
    parser.add_argument('args', nargs=argparse.REMAINDER,
                        help='Command and its arguments')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print what would happen')
    parser.add_argument('--cache-dir', help='Metadata cache directory')

    args = parser.parse_args(_argv_without('npx'))

    if not args.args:
        parser.print_help()
        return 1

    tool = args.args[0]
    atom = _atom_for(tool)
    if atom is None:
        print('Error: %r is not a usable package name' % tool, file=sys.stderr)
        return 1

    print('npx runs a command from a package; portage installs it instead.')
    print()
    print('  emerge --ask %s' % atom)
    print('  %s' % ' '.join(args.args))
    print()
    print('The package providing %r may be named differently; check with '
          '"%s debug info %s".' % (tool, PROG, tool))
    return 0


# ---------------------------------------------------------------------------
# debug
# ---------------------------------------------------------------------------

def debug_command() -> int:
    """Inspect npm metadata and translation decisions."""
    parser = argparse.ArgumentParser(
        prog='%s debug' % PROG,
        description='Inspect npm metadata and how it maps onto Gentoo',
    )
    parser.add_argument('action',
                        choices=['versions', 'info', 'translate', 'filter',
                                 'deps', 'node', 'locks', 'unlock'],
                        help='What to inspect')
    parser.add_argument('name', nargs='?', help='Package name')
    parser.add_argument('--version', help='Specific version')
    parser.add_argument('--cache-dir', help='Metadata cache directory')
    parser.add_argument('--patch-file', help='Where pin locks are stored')
    parser.add_argument('--mountpoint',
                        help='Limit to one mount point; locks are namespaced by '
                             'mount, and all are shown by default')
    parser.add_argument('--json', action='store_true', help='Emit JSON')

    args = parser.parse_args(_argv_without('debug'))

    if args.action == 'locks':
        return _debug_locks(args)

    if args.action == 'unlock':
        return _debug_unlock(args)

    if args.action == 'node':
        versions = node_targets.get_node_versions()
        print(json.dumps(versions) if args.json
              else 'Detected Node versions: %s' % ', '.join(versions))
        return 0

    if not args.name:
        print('Error: %s needs a package name' % args.action, file=sys.stderr)
        return 1

    if args.action == 'translate':
        return _debug_translate(args)

    provider = _provider(args)

    if args.action == 'versions':
        return _debug_versions(args, provider)
    if args.action == 'info':
        return _debug_info(args, provider)
    if args.action == 'filter':
        return _debug_filter(args, provider)
    if args.action == 'deps':
        return _debug_deps(args, provider)

    return 1


def _lock_path(args) -> str:
    """Resolve the lock file a debug command should read."""
    from portage_pip_fuse.constants import DEFAULT_PATCH_FILE
    return args.patch_file or str(DEFAULT_PATCH_FILE)


def _lock_stores(args):
    """
    Open one lock store per mount-point namespace present in the file.

    Locks are namespaced by mount point, so inspecting only the default
    namespace hides everything an overlay mounted at a custom path recorded.

    Returns:
        List of ``(mount_point_key, store)`` pairs
    """
    from .resolution_lock import ResolutionLockStore

    path = _lock_path(args)
    wanted = getattr(args, 'mountpoint', None)

    if wanted:
        return [(wanted, ResolutionLockStore(storage_path=path,
                                             mount_point=wanted))]

    keys = ResolutionLockStore.list_mount_points(path)
    if not keys:
        return [('_default', ResolutionLockStore(storage_path=path))]

    stores = []
    for key in keys:
        # '_default' is the key used when no mount point was recorded, so it
        # must be opened without one rather than as a literal path.
        store = ResolutionLockStore(
            storage_path=path,
            mount_point=None if key == '_default' else key)
        stores.append((key, store))
    return stores


def _debug_locks(args) -> int:
    """Show the recorded dependency pins, across every mount point."""
    gentoo = name_translator.npm_to_gentoo(args.name) if args.name else None

    payload = []
    for mount_key, store in _lock_stores(args):
        for category, package, version, pins in store.list_all_locks():
            if gentoo is not None and package != gentoo:
                continue
            payload.append({'mount_point': mount_key, 'category': category,
                            'package': package, 'version': version,
                            'pins': pins})

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    if not payload:
        print('No dependency pins recorded.')
        print('Pins are recorded as ebuilds are generated, so mount the '
              'overlay and read a package first.')
        return 0

    current = None
    for entry in payload:
        if entry['mount_point'] != current:
            current = entry['mount_point']
            print('mount: %s' % current)
        print('  %s/%s-%s (%d pin(s))' % (entry['category'], entry['package'],
                                          entry['version'], len(entry['pins'])))
        for name in sorted(entry['pins']):
            print('      %-38s %s' % (name, entry['pins'][name]))
    return 0


def _debug_unlock(args) -> int:
    """Clear recorded pins so they resolve afresh."""
    gentoo = None
    if args.name:
        gentoo = name_translator.npm_to_gentoo(args.name)
        if gentoo is None:
            print('Error: %r is not a usable package name' % args.name,
                  file=sys.stderr)
            return 1

    removed = 0
    for _mount_key, store in _lock_stores(args):
        if gentoo is None:
            removed += store.clear()
        elif args.version:
            pms = version_translator.translate_version(args.version) or args.version
            removed += 1 if store.remove('dev-nodejs', gentoo, pms) else 0
        else:
            removed += store.remove_package('dev-nodejs', gentoo)

        if store.is_dirty and not store.save():
            print('Error: could not save the lock file', file=sys.stderr)
            return 1

    if not removed:
        print('Nothing to unlock.')
        return 0

    print('Unlocked %d package version(s); they will resolve afresh on the '
          'next generation.' % removed)
    return 0


def _debug_translate(args) -> int:
    """Show how a name maps between npm and Gentoo."""
    name = args.name
    gentoo = name_translator.npm_to_gentoo(name)
    collision = name_translator.find_collision(name)

    result = {
        'npm': name,
        'gentoo': gentoo,
        'atom': _atom_for(name),
        'reverse': name_translator.gentoo_to_npm(gentoo) if gentoo else None,
        'collides_with': collision,
    }
    if args.version:
        result['npm_version'] = args.version
        result['gentoo_version'] = version_translator.translate_version(args.version)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    if gentoo is None:
        print('%s is not a valid npm package name' % name)
        return 1

    print('npm name     : %s' % name)
    print('Gentoo name  : %s' % gentoo)
    print('Atom         : %s' % result['atom'])
    print('Reverse      : %s' % result['reverse'])
    if collision:
        print('Collides with: %s (resolve via .sys/name-translation)' % collision)
    if args.version:
        print('Version      : %s -> %s' % (args.version,
                                           result['gentoo_version'] or 'untranslatable'))
    return 0


def _debug_versions(args, provider) -> int:
    """List published versions and their Gentoo equivalents."""
    versions = provider.get_package_versions(args.name)
    if not versions:
        print('Error: %s not found on the registry' % args.name, file=sys.stderr)
        return 1

    rows = [(v, version_translator.translate_version(v)) for v in versions]

    if args.json:
        print(json.dumps([{'npm': npm, 'gentoo': gentoo}
                          for npm, gentoo in rows], indent=2))
        return 0

    print('%s: %d published version(s), newest first' % (args.name, len(rows)))
    for npm, gentoo in rows:
        print('  %-28s -> %s' % (npm, gentoo or '(untranslatable)'))
    return 0


def _debug_info(args, provider) -> int:
    """Show a version's manifest highlights."""
    version = args.version
    if version is None:
        tags = provider.get_dist_tags(args.name)
        version = tags.get('latest')
        if version is None:
            versions = provider.get_package_versions(args.name)
            version = versions[0] if versions else None

    if version is None:
        print('Error: %s not found on the registry' % args.name, file=sys.stderr)
        return 1

    manifest = provider.get_full_version_info(args.name, version)
    if not manifest:
        print('Error: %s@%s not found' % (args.name, version), file=sys.stderr)
        return 1

    info = {
        'name': manifest.get('name'),
        'version': manifest.get('version'),
        'description': manifest.get('description'),
        'license': manifest.get('license'),
        'homepage': manifest.get('homepage'),
        'engines': manifest.get('engines'),
        'os': manifest.get('os'),
        'cpu': manifest.get('cpu'),
        'bin': manifest.get('bin'),
        'deprecated': manifest.get('deprecated'),
        'dependencies': len(manifest.get('dependencies') or {}),
        'gentoo_version': version_translator.translate_version(version),
        'keywords': npm_filters.os_cpu_to_keywords(manifest.get('os'),
                                                   manifest.get('cpu')),
    }

    if args.json:
        print(json.dumps(info, indent=2))
        return 0

    for key in ('name', 'version', 'gentoo_version', 'description', 'license',
                'homepage', 'engines', 'os', 'cpu', 'keywords', 'bin',
                'deprecated', 'dependencies'):
        value = info[key]
        if value not in (None, {}, [], ''):
            print('%-15s: %s' % (key, value))
    return 0


def _debug_filter(args, provider) -> int:
    """Show which versions survive the filters, and why the rest do not."""
    raw = provider.get_versions_metadata(args.name)
    if not raw:
        print('Error: %s not found on the registry' % args.name, file=sys.stderr)
        return 1

    chain = npm_filters.create_filter_chain()
    kept = chain.filter_versions(args.name, raw)
    ordered = version_translator.select_order_preserving(list(kept))

    result = {
        'published': len(raw),
        'after_filters': len(kept),
        'after_order_check': len(ordered),
        'visible': sorted(ordered),
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print('%s' % args.name)
    print('  published        : %d' % result['published'])
    print('  after filters    : %d' % result['after_filters'])
    print('  after order check: %d' % result['after_order_check'])
    print('  filters          : %s' % chain.get_description())
    dropped = sorted(set(raw) - set(ordered))
    if dropped:
        print('  dropped (%d), first 10:' % len(dropped))
        for version in dropped[:10]:
            reason = ('untranslatable'
                      if not version_translator.can_translate_version(version)
                      else 'filtered or order-inverting')
            print('    %-28s %s' % (version, reason))
    return 0


def _debug_deps(args, provider) -> int:
    """Show a version's dependencies and the atoms they become."""
    from .plugin import NpmEbuildGenerator

    version = args.version
    if version is None:
        version = provider.get_dist_tags(args.name).get('latest')
    if version is None:
        print('Error: %s not found on the registry' % args.name, file=sys.stderr)
        return 1

    manifest = provider.get_version_info(args.name, version)
    if not manifest:
        print('Error: %s@%s not found' % (args.name, version), file=sys.stderr)
        return 1

    generator = NpmEbuildGenerator(metadata_provider=provider)
    requirements = generator.collect_requirements(manifest)
    pins, unresolved = generator.resolve_dependencies(manifest)

    result = {
        'package': args.name,
        'version': version,
        'requirements': requirements,
        'pins': [{'name': n, 'version': v, 'atom': _atom_for(n, v)}
                 for n, v in pins],
        'unresolved': [{'name': n, 'spec': s} for n, s in unresolved],
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print('%s@%s: %d requirement(s)' % (args.name, version, len(requirements)))
    for pin in result['pins']:
        print('  %-32s %-14s %s' % (pin['name'], pin['version'], pin['atom']))
    if unresolved:
        print('  unresolved (portage cannot express these):')
        for item in result['unresolved']:
            print('    %-32s %s' % (item['name'], item['spec']))
    return 0
