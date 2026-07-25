"""
Canonical Gentoo PMS version handling.

This module is the single source of truth for what constitutes a valid Gentoo
package version, and for translating upstream version strings into that form.

Before this module existed, six independent translators were spread across the
codebase (PyPI in ``pip_metadata.py``, ``filesystem.py`` and ``cli.py``;
RubyGems in ``ecosystems/rubygems/{filesystem,plugin,cli}.py``), each with its
own suffix vocabulary and only some of them validating their output. That
divergence caused real defects: the RubyGems ``GentooVersionFilter`` accepted
versions that the RubyGems translator then refused to translate, and the PyPI
translators emitted unvalidated versions straight into dependency atoms.

The layering here is deliberate:

- :data:`PMS_VERSION_RE` and :func:`is_valid` describe *Gentoo*, and are
  ecosystem-independent.
- :func:`translate_dotted`, :func:`untranslate_dotted` and
  :func:`can_translate_dotted` implement the *dot-separated suffix* dialect
  (``1.0.0.beta1``), which RubyGems uses natively. Ecosystems whose versions
  are shaped differently normalize into this dialect first — npm's
  ``1.0.0-beta.1`` becomes ``1.0.0.beta.1`` — rather than growing a seventh
  translator.
- :func:`can_translate_dotted` is derived from :func:`translate_dotted`, so a
  filter built on it can never disagree with the translator that follows it.

PMS 3.2 defines the version syntax as::

    [0-9]+(\\.[0-9]+)*[a-z]?((_alpha|_beta|_pre|_rc|_p)[0-9]*)*(-r[0-9]+)?

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import re
from typing import Dict, Optional

__all__ = [
    'PMS_SUFFIXES',
    'PMS_VERSION_RE',
    'DEFAULT_SHORTHAND_MAP',
    'is_valid',
    'translate_dotted',
    'untranslate_dotted',
    'can_translate_dotted',
    'normalize_shortest',
    'normalize_longest',
    'equivalent_form',
]


#: PMS pre-release/post-release suffix names, in the order PMS orders them.
#: ``_p`` is a patchlevel and sorts *after* the plain version; the other four
#: sort before it.
PMS_SUFFIXES = ('alpha', 'beta', 'pre', 'rc', 'p')

#: Suffix names accepted as an explicit dotted component by
#: :func:`translate_dotted`. ``p`` is deliberately absent: in the dotted
#: dialect a bare numeric component already encodes a patchlevel, so accepting
#: a literal ``.p1`` as well would make the transform ambiguous and therefore
#: irreversible.
_DOTTED_SUFFIXES = frozenset(('alpha', 'beta', 'pre', 'rc'))

#: Single-letter upstream shorthands mapped to PMS suffix names. RubyGems uses
#: these (``5.a`` meaning ``5_alpha``); most ecosystems do not.
DEFAULT_SHORTHAND_MAP: Dict[str, str] = {'a': 'alpha', 'b': 'beta'}

#: Canonical PMS version regex, per PMS 3.2.
PMS_VERSION_RE = re.compile(
    r'^'
    r'\d+(\.\d+)*'                                   # base: 1.2.3
    r'([a-z])?'                                      # optional single letter
    r'(_alpha\d*|_beta\d*|_pre\d*|_rc\d*|_p\d*)*'    # PMS suffixes
    r'(-r\d+)?'                                      # optional revision
    r'$'
)

#: Splits a version into its numeric base and whatever trails it.
_BASE_SPLIT_RE = re.compile(r'^(\d+(?:\.\d+)*)(.*)$')

#: A suffix component that is a name immediately followed by digits.
_NAME_NUM_RE = re.compile(r'^([a-z]+)(\d+)$')


def is_valid(version: str) -> bool:
    """
    Check whether a version string is valid per the PMS version grammar.

    This is the gate every generated version must pass: an invalid version
    yields an ebuild filename portage cannot parse, or a dependency atom it
    silently mismatches.

    Args:
        version: Candidate version string, already in Gentoo form

    Returns:
        True if the version matches the PMS version grammar

    Examples:
        >>> is_valid('1.2.3')
        True
        >>> is_valid('2.0_alpha1')
        True
        >>> is_valid('1.0_beta2_p1')
        True
        >>> is_valid('1.2.3a')
        True
        >>> is_valid('1.2.3-r2')
        True
        >>> is_valid('1.0.0-beta.1')
        False
        >>> is_valid('1.0.0.RELEASE')
        False
        >>> is_valid('')
        False
    """
    if not version:
        return False
    return bool(PMS_VERSION_RE.match(version))


def translate_dotted(
    version: str,
    shorthand_map: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """
    Translate a dot-separated-suffix upstream version into PMS form.

    Handles the dialect where pre-release markers are dot-separated components
    trailing a numeric base, such as RubyGems' ``2.0.0.alpha1``. Standalone
    numeric components become ``_p`` patchlevels, which keeps the transform
    reversible by :func:`untranslate_dotted`.

    Returns None — rather than guessing — for any component that is not a
    recognised suffix. Failing closed matters: a guessed translation produces a
    plausible-looking but wrong ebuild version, whereas None lets the caller
    drop the version cleanly.

    Args:
        version: Upstream version string
        shorthand_map: Single-letter shorthands to expand, defaulting to
            :data:`DEFAULT_SHORTHAND_MAP`. Pass ``{}`` to disable shorthand
            handling for ecosystems that do not use it.

    Returns:
        PMS version string, or None if the version cannot be represented

    Examples:
        >>> translate_dotted('1.0.0')
        '1.0.0'
        >>> translate_dotted('2.0.0.alpha1')
        '2.0.0_alpha1'
        >>> translate_dotted('4.0.0.rc1')
        '4.0.0_rc1'
        >>> translate_dotted('5.0.0.pre')
        '5.0.0_pre'
        >>> translate_dotted('2.0.0.alpha.pre.4')
        '2.0.0_alpha_pre_p4'
        >>> translate_dotted('5.0.0.beta1.1')
        '5.0.0_beta1_p1'
        >>> translate_dotted('2.0.0.alpha.pre4')
        '2.0.0_alpha_pre4'
        >>> translate_dotted('5.a')
        '5_alpha'
        >>> translate_dotted('5.a1')
        '5_alpha1'

        Non-standard suffixes are rejected rather than mangled:

        >>> translate_dotted('5.0.0.racecar1') is None
        True
        >>> translate_dotted('1.0.0.RELEASE') is None
        True
        >>> translate_dotted('not-a-version') is None
        True

        Shorthand expansion can be turned off:

        >>> translate_dotted('5.a', shorthand_map={}) is None
        True

        Every successful translation satisfies :func:`is_valid`:

        >>> all(is_valid(translate_dotted(v)) for v in
        ...     ['1.0.0', '2.0.0.alpha1', '2.0.0.alpha.pre.4', '5.a1'])
        True
    """
    if shorthand_map is None:
        shorthand_map = DEFAULT_SHORTHAND_MAP

    match = _BASE_SPLIT_RE.match(version)
    if not match:
        return None

    base, suffix = match.groups()

    suffix = suffix.lstrip('.')
    if not suffix:
        return base

    shorthand_num_re = re.compile(r'^([%s])(\d+)$' % ''.join(shorthand_map)) \
        if shorthand_map else None

    translated = ''
    for component in suffix.split('.'):
        comp = component.lower()

        if comp in shorthand_map:
            translated += '_%s' % shorthand_map[comp]
        elif comp in _DOTTED_SUFFIXES:
            translated += '_%s' % comp
        elif comp.isdigit():
            # A bare number is a patchlevel.
            translated += '_p%s' % comp
        elif shorthand_num_re is not None and shorthand_num_re.match(comp):
            m = shorthand_num_re.match(comp)
            translated += '_%s%s' % (shorthand_map[m.group(1)], m.group(2))
        else:
            m = _NAME_NUM_RE.match(comp)
            if m is not None and m.group(1) in _DOTTED_SUFFIXES:
                translated += '_%s%s' % (m.group(1), m.group(2))
            else:
                return None

    return base + translated


def untranslate_dotted(version: str) -> str:
    """
    Convert a PMS version produced by :func:`translate_dotted` back upstream.

    Args:
        version: PMS version string

    Returns:
        Upstream version string in the dot-separated-suffix dialect

    Examples:
        >>> untranslate_dotted('1.0.0')
        '1.0.0'
        >>> untranslate_dotted('2.0.0_alpha1')
        '2.0.0.alpha1'
        >>> untranslate_dotted('4.0.0_rc1')
        '4.0.0.rc1'
        >>> untranslate_dotted('5.0.0_pre')
        '5.0.0.pre'
        >>> untranslate_dotted('2.0.0_alpha_pre_p4')
        '2.0.0.alpha.pre.4'
        >>> untranslate_dotted('5.0.0_beta1_p1')
        '5.0.0.beta1.1'

        The pair round-trips for versions in the dotted dialect:

        >>> originals = ['1.0.0', '2.0.0.alpha1', '2.0.0.alpha.pre.4',
        ...              '5.0.0.beta1.1', '2.0.0.alpha.pre4']
        >>> [untranslate_dotted(translate_dotted(v)) for v in originals] == originals
        True
    """
    # Patchlevels first: _p1 -> .1, so the suffix rules below cannot re-match.
    result = re.sub(r'_p(\d+)', r'.\1', version)

    for name in ('alpha', 'beta', 'pre', 'rc'):
        result = re.sub(r'_%s(\d*)' % name, r'.%s\1' % name, result)

    return result


def can_translate_dotted(
    version: str,
    shorthand_map: Optional[Dict[str, str]] = None,
) -> bool:
    """
    Check whether :func:`translate_dotted` can represent a version.

    Derived from :func:`translate_dotted` rather than reimplementing its rules,
    so a filter using this predicate and a generator using the translator
    cannot disagree about which versions are usable.

    Args:
        version: Upstream version string
        shorthand_map: Passed through to :func:`translate_dotted`

    Returns:
        True if the version translates to a valid PMS version

    Examples:
        >>> can_translate_dotted('1.0.0')
        True
        >>> can_translate_dotted('2.0.0.alpha1')
        True
        >>> can_translate_dotted('2.0.0.alpha.pre.4')
        True
        >>> can_translate_dotted('5.0.0.beta1.1')
        True
        >>> can_translate_dotted('5.a')
        True
        >>> can_translate_dotted('5.0.0.racecar1')
        False
        >>> can_translate_dotted('1.0.0.RELEASE')
        False
    """
    translated = translate_dotted(version, shorthand_map=shorthand_map)
    return translated is not None and is_valid(translated)


def normalize_shortest(version: str) -> str:
    """
    Strip redundant trailing ``.0`` segments from a version.

    Args:
        version: PMS version string

    Returns:
        Version with trailing ``.0`` segments removed, leaving at least two
        components. Versions carrying a PMS suffix are returned unchanged,
        since their ordering depends on the full base.

    Examples:
        >>> normalize_shortest('1.33.0')
        '1.33'
        >>> normalize_shortest('2.0.0')
        '2.0'
        >>> normalize_shortest('1.33')
        '1.33'
        >>> normalize_shortest('1.0.0_alpha1')
        '1.0.0_alpha1'
    """
    if '_' in version:
        return version
    while version.endswith('.0') and version.count('.') > 1:
        version = version[:-2]
    return version


def normalize_longest(version: str) -> str:
    """
    Extend a version with a trailing ``.0`` segment.

    Args:
        version: PMS version string

    Returns:
        Version with a trailing ``.0`` appended where meaningful. Versions
        carrying a PMS suffix are returned unchanged.

    Examples:
        >>> normalize_longest('1.33')
        '1.33.0'
        >>> normalize_longest('1.33.0')
        '1.33.0'
        >>> normalize_longest('1.0.0_alpha1')
        '1.0.0_alpha1'
    """
    if '_' in version:
        return version
    if not version.endswith('.0') or version.count('.') < 2:
        return version + '.0'
    return version


def equivalent_form(version: str) -> Optional[str]:
    """
    Return the alternate trailing-``.0`` spelling of a version.

    Several upstream version schemes treat ``1.33`` and ``1.33.0`` as the same
    release; Gentoo treats them as distinct versions. Callers matching an
    upstream version against a Gentoo tree need to try both spellings.

    Args:
        version: PMS version string

    Returns:
        The alternate spelling, or None when the concept does not apply
        (versions carrying a PMS suffix)

    Examples:
        >>> equivalent_form('1.33')
        '1.33.0'
        >>> equivalent_form('1.33.0')
        '1.33'
        >>> equivalent_form('2.0.0')
        '2.0'
        >>> equivalent_form('1.0_alpha1') is None
        True
    """
    if '_' in version:
        return None

    if version.endswith('.0') and version.count('.') > 1:
        return version[:-2]
    return version + '.0'
