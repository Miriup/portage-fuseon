"""
Interrupt handling utilities for FUSE filesystem operations.

This module provides a thread-safe mechanism for checking and handling
interrupt signals during long-running operations. This prevents the
FUSE filesystem from hanging on Ctrl+C or other interrupt signals.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import threading
import logging

logger = logging.getLogger(__name__)


class InterruptChecker:
    """
    Check for FUSE interrupt signals during long operations.

    This class provides a thread-safe way to signal interrupts across
    different parts of the FUSE filesystem. Call check() periodically
    in long-running loops to allow graceful interruption.

    Examples:
        >>> # In a long-running loop:
        >>> InterruptChecker.clear()  # Reset at start of operation
        >>> for item in large_list:
        ...     InterruptChecker.check()  # Raises InterruptedError if interrupted
        ...     process(item)

        >>> # To signal an interrupt from another thread:
        >>> InterruptChecker.set_interrupted()
    """

    _interrupted = threading.Event()
    _lock = threading.Lock()

    @classmethod
    def check(cls):
        """
        Check if an interrupt has been signaled.

        Call this method in loops over large datasets to allow
        graceful interruption of long-running operations.

        Raises:
            InterruptedError: If an interrupt has been signaled

        Examples:
            >>> InterruptChecker.clear()
            >>> InterruptChecker.check()  # No exception
            >>> InterruptChecker.set_interrupted()
            >>> try:
            ...     InterruptChecker.check()
            ... except InterruptedError:
            ...     print("Interrupted!")
            Interrupted!
        """
        if cls._interrupted.is_set():
            logger.debug("Interrupt detected, raising InterruptedError")
            raise InterruptedError("Operation cancelled")

    @classmethod
    def set_interrupted(cls):
        """
        Signal that an interrupt has occurred.

        Call this method when an interrupt signal is received
        (e.g., from a signal handler or FUSE interrupt callback).

        Examples:
            >>> InterruptChecker.clear()
            >>> InterruptChecker.is_interrupted()
            False
            >>> InterruptChecker.set_interrupted()
            >>> InterruptChecker.is_interrupted()
            True
        """
        with cls._lock:
            logger.debug("Setting interrupt flag")
            cls._interrupted.set()

    @classmethod
    def clear(cls):
        """
        Clear the interrupt flag.

        Call this method at the start of a new operation to reset
        the interrupt state.

        Examples:
            >>> InterruptChecker.set_interrupted()
            >>> InterruptChecker.is_interrupted()
            True
            >>> InterruptChecker.clear()
            >>> InterruptChecker.is_interrupted()
            False
        """
        with cls._lock:
            cls._interrupted.clear()

    @classmethod
    def is_interrupted(cls) -> bool:
        """
        Check if an interrupt has been signaled without raising.

        Returns:
            True if interrupted, False otherwise

        Examples:
            >>> InterruptChecker.clear()
            >>> InterruptChecker.is_interrupted()
            False
            >>> InterruptChecker.set_interrupted()
            >>> InterruptChecker.is_interrupted()
            True
        """
        return cls._interrupted.is_set()


def check_interrupt():
    """
    Convenience function to check for interrupts.

    This is a shorthand for InterruptChecker.check() that can be
    imported directly for cleaner code.

    Raises:
        InterruptedError: If an interrupt has been signaled

    Examples:
        >>> from portage_pip_fuse.interrupt import check_interrupt
        >>> InterruptChecker.clear()
        >>> check_interrupt()  # No exception
    """
    InterruptChecker.check()
