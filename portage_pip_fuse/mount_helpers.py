"""
Shared helpers for FUSE mount commands.

The PyPI and RubyGems mount commands each carry their own copy of this logic --
mountpoint validation, logging setup, signal handling and PID-file management --
interleaved with their argparse blocks. Rather than add a third copy for npm,
the reusable parts live here.

The existing two copies are deliberately left in place: they are working code
paths that need a real portage installation to exercise, so retrofitting them is
a separate change from adding a new ecosystem.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = [
    'check_fuse_available',
    'validate_mountpoint',
    'configure_logging',
    'PidFile',
    'install_signal_handlers',
]


def check_fuse_available() -> Optional[str]:
    """
    Check whether FUSE can be used.

    Returns:
        None when FUSE looks usable, otherwise a message explaining what is
        missing. Returning the reason rather than printing it keeps this usable
        from a library context.

    Examples:
        >>> result = check_fuse_available()
        >>> result is None or isinstance(result, str)
        True
    """
    try:
        import fuse  # noqa: F401
    except (ImportError, EnvironmentError) as exc:
        return ('FUSE is unavailable: %s\n'
                'Install fusepy and libfuse (Gentoo: sys-fs/fuse, '
                'dev-python/fusepy).' % exc)

    if not os.path.exists('/dev/fuse'):
        return ('/dev/fuse does not exist. Load the fuse module with '
                '"modprobe fuse".')

    return None


def validate_mountpoint(mountpoint: str, create: bool = True) -> Path:
    """
    Resolve and validate a mountpoint, optionally creating it.

    Args:
        mountpoint: Requested mount path
        create: Create the directory when missing

    Returns:
        The resolved path

    Raises:
        ValueError: if the path is unusable, with a message naming the reason

    Examples:
        >>> import tempfile, pathlib
        >>> target = pathlib.Path(tempfile.mkdtemp()) / 'mnt'
        >>> validate_mountpoint(str(target)).name
        'mnt'
        >>> target.is_dir()
        True
    """
    path = Path(mountpoint).expanduser().resolve()

    if path.exists():
        if not path.is_dir():
            raise ValueError('%s exists and is not a directory' % path)
        return path

    if not create:
        raise ValueError('%s does not exist' % path)

    try:
        path.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise ValueError(
            'Cannot create %s: permission denied. Create it first, or run as '
            'a user that can.' % path)
    except OSError as exc:
        raise ValueError('Cannot create %s: %s' % (path, exc))

    return path


def configure_logging(debug: bool = False, logfile: Optional[str] = None) -> None:
    """
    Configure logging for a mount command.

    Args:
        debug: Log at DEBUG rather than INFO
        logfile: Write to this file instead of stderr. A daemonised mount has no
            usable stderr, so this is the only way to see its logs.

    Raises:
        ValueError: if the log file cannot be opened
    """
    level = logging.DEBUG if debug else logging.INFO
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    if not logfile:
        logging.basicConfig(level=level, format=log_format)
        return

    path = Path(logfile).expanduser().resolve()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path)
    except (PermissionError, OSError) as exc:
        raise ValueError('Cannot write log file %s: %s' % (path, exc))

    handler.setFormatter(logging.Formatter(log_format))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


class PidFile:
    """
    Manage a PID file for a mount process.

    Used as a context manager so the file is removed on normal exit, and the
    signal handlers installed by :func:`install_signal_handlers` remove it on
    SIGINT and SIGTERM too. A stale PID file makes an unmount command think a
    mount is still running.

    Examples:
        >>> import tempfile, pathlib, os
        >>> path = pathlib.Path(tempfile.mkdtemp()) / 'run' / 'npm.pid'
        >>> with PidFile(path) as pid_file:
        ...     path.read_text() == str(os.getpid())
        True
        >>> path.exists()
        False

        A None path disables the whole mechanism:

        >>> with PidFile(None):
        ...     pass
    """

    def __init__(self, path: Optional[Path]):
        self.path = Path(path) if path else None

    def write(self) -> None:
        """Write the current process ID."""
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(str(os.getpid()))
        except OSError as exc:
            raise ValueError('Cannot write PID file %s: %s' % (self.path, exc))

    def remove(self) -> None:
        """Remove the PID file, ignoring a file that is already gone."""
        if self.path is None:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug('Could not remove PID file %s: %s', self.path, exc)

    def __enter__(self) -> 'PidFile':
        self.write()
        return self

    def __exit__(self, *exc_info) -> bool:
        self.remove()
        return False


def install_signal_handlers(pid_file: Optional[PidFile] = None) -> None:
    """
    Install SIGINT and SIGTERM handlers that clean up before exiting.

    Args:
        pid_file: Removed before exiting, if given
    """
    def handler(signum, _frame):
        logger.info('Received signal %d, unmounting', signum)
        if pid_file is not None:
            pid_file.remove()
        sys.exit(0)

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
