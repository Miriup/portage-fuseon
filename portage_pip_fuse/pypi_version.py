"""
PEP 440 to Gentoo PMS version translation.

PyPI versions do not use the dot-separated suffix dialect that
:mod:`portage_pip_fuse.pms_version` handles natively — PEP 440 attaches
markers directly to the base (``2.0a0``, ``1.0.post1``) — so they get their own
translator, layered on the canonical PMS validity check rather than duplicating
it.

This module consolidates three previously independent copies of the same regex
chain, in ``pip_metadata.py``, ``filesystem.py`` and ``cli.py``. Only the
``filesystem.py`` copy validated its output; the other two fed unvalidated
versions straight into ebuild filenames and dependency atoms.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import re
from typing import Optional

from portage_pip_fuse import pms_version

__all__ = [
    'translate_pep440',
    'can_translate_pep440',
]


#: Ordered rewrite rules taking PEP 440 markers to PMS suffixes. Order matters:
#: the long spellings must be consumed before the single-letter ones, and ``rc``
#: before a bare ``c``, or the shorter pattern eats part of the longer one.
_REWRITES = (
    (re.compile(r'\.?alpha(\d+)'), r'_alpha\1'),
    (re.compile(r'(?<![a-z])\.?a(\d+)'), r'_alpha\1'),
    (re.compile(r'\.?beta(\d+)'), r'_beta\1'),
    (re.compile(r'(?<![a-z])\.?b(\d+)'), r'_beta\1'),
    (re.compile(r'\.?rc(\d+)'), r'_rc\1'),
    (re.compile(r'(?<!r)\.?c(\d+)'), r'_rc\1'),
    (re.compile(r'\.post(\d+)'), r'_p\1'),
    (re.compile(r'\.dev(\d+)'), r'_pre\1'),
)


def translate_pep440(version: str, validate: bool = True) -> Optional[str]:
    """
    Translate a PEP 440 version string into Gentoo PMS form.

    Converts PEP 440 pre-release and post-release markers:

    - ``a`` / ``alpha`` -> ``_alpha``
    - ``b`` / ``beta`` -> ``_beta``
    - ``rc`` / ``c`` -> ``_rc``
    - ``.post`` -> ``_p``
    - ``.dev`` -> ``_pre``

    Args:
        version: Version string in PyPI/PEP 440 format
        validate: When True (the default) the result is checked against the PMS
            grammar and None is returned if it does not conform. Pass False
            only to inspect the raw rewrite, never to emit a version.

    Returns:
        PMS version string, or None when the input cannot be represented

    Examples:
        >>> translate_pep440('1.2.3')
        '1.2.3'
        >>> translate_pep440('2.0a0')
        '2.0_alpha0'
        >>> translate_pep440('1.0b1')
        '1.0_beta1'
        >>> translate_pep440('3.0rc1')
        '3.0_rc1'
        >>> translate_pep440('1.0c1')
        '1.0_rc1'
        >>> translate_pep440('1.0.post1')
        '1.0_p1'
        >>> translate_pep440('1.0.dev1')
        '1.0_pre1'

        Versions PEP 440 permits but PMS cannot express are rejected rather
        than passed through to become unparseable atoms:

        >>> translate_pep440('1.0+local.build') is None
        True
        >>> translate_pep440('2024.rubbish') is None
        True
        >>> translate_pep440('') is None
        True

        The unvalidated rewrite is available for diagnostics:

        >>> translate_pep440('1.0+local.build', validate=False)
        '1.0+local.build'
    """
    if not version:
        return None

    result = version
    for pattern, replacement in _REWRITES:
        result = pattern.sub(replacement, result)

    if validate and not pms_version.is_valid(result):
        return None

    return result


def can_translate_pep440(version: str) -> bool:
    """
    Check whether a PEP 440 version can be represented in PMS form.

    Derived from :func:`translate_pep440` so the two cannot disagree.

    Args:
        version: Version string in PyPI/PEP 440 format

    Returns:
        True if the version translates to a valid PMS version

    Examples:
        >>> can_translate_pep440('1.2.3')
        True
        >>> can_translate_pep440('2.0a0')
        True
        >>> can_translate_pep440('1.0+local.build')
        False
    """
    return translate_pep440(version) is not None
