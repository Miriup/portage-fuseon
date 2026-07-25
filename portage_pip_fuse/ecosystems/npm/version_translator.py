"""
npm semver to Gentoo PMS version translation.

npm attaches prerelease markers with a hyphen and dot-separates their parts
(``1.2.3-beta.1``), which is neither PMS form (``1.2.3_beta1``) nor the
dot-separated dialect :mod:`portage_pip_fuse.pms_version` handles natively.
RubyGems' ``1.0.0.beta.1`` means "beta, then patchlevel 1" and translates to
``_beta_p1``; npm's ``1.0.0-beta.1`` means "beta number 1" and must become
``_beta1``. Reusing the dotted translator directly would therefore produce
wrong versions, so this module does its own join and defers only the PMS
validity check to the shared code.

Translation fails closed. A version whose prerelease cannot be represented in
PMS form yields None rather than a guess, because a guessed version produces a
plausible-looking ebuild filename that sorts wrongly against its siblings --
worse than the version simply not being offered.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import re
from typing import Optional

from portage_pip_fuse import pms_version
from portage_pip_fuse.ecosystems.npm import semver

__all__ = [
    'PMS_PRERELEASE_WORDS',
    'translate_version',
    'untranslate_version',
    'can_translate_version',
]

#: Prerelease words PMS can express that also sort below the plain release.
#: ``_p`` is excluded deliberately: it is a patchlevel and sorts *above* the
#: release, so using it for an npm prerelease would invert the ordering.
PMS_PRERELEASE_WORDS = ('alpha', 'beta', 'pre', 'rc')

#: A single prerelease identifier carrying its number inline, e.g. 'beta1'.
#: The hyphen spelling 'beta-1' is deliberately NOT accepted: npm treats it as
#: one opaque alphanumeric identifier, so it sorts *above* 'beta.6' rather than
#: below it. Folding it to _beta1 would silently invert that ordering.
_WORD_NUMBER_RE = re.compile(
    r'^(%s)(\d*)$' % '|'.join(PMS_PRERELEASE_WORDS)
)

#: The PMS suffix, for reversing a translation.
_PMS_SUFFIX_RE = re.compile(
    r'^(\d+(?:\.\d+)*)_(%s)(\d*)$' % '|'.join(PMS_PRERELEASE_WORDS)
)


def translate_version(version: str) -> Optional[str]:
    """
    Translate an npm semver version into Gentoo PMS form.

    Build metadata is dropped: PMS has no equivalent, and semver itself defines
    it as not participating in precedence, so discarding it loses no ordering
    information.

    Args:
        version: npm version string

    Returns:
        PMS version string, or None when the version cannot be represented

    Examples:
        >>> translate_version('1.2.3')
        '1.2.3'
        >>> translate_version('0.0.1')
        '0.0.1'
        >>> translate_version('10.20.30')
        '10.20.30'

        Prerelease markers become PMS suffixes, whether npm spelled the number
        as a separate identifier or inline:

        >>> translate_version('1.2.3-beta.1')
        '1.2.3_beta1'
        >>> translate_version('1.2.3-beta1')
        '1.2.3_beta1'
        >>> translate_version('1.2.3-alpha')
        '1.2.3_alpha'
        >>> translate_version('2.0.0-rc.0')
        '2.0.0_rc0'
        >>> translate_version('1.0.0-pre.5')
        '1.0.0_pre5'

        Build metadata is discarded:

        >>> translate_version('1.2.3+build.5')
        '1.2.3'
        >>> translate_version('1.2.3-beta.1+build')
        '1.2.3_beta1'

        Prereleases PMS cannot express are rejected rather than mangled. A bare
        numeric prerelease is the subtle one: '1.0.0-0' sorts *below* 1.0.0,
        but the only PMS suffix for a bare number is '_p', which sorts above:

        >>> translate_version('1.0.0-0') is None
        True
        >>> translate_version('1.2.3-next.5') is None
        True
        >>> translate_version('1.2.3-canary.3') is None
        True
        >>> translate_version('1.2.3-security') is None
        True
        >>> translate_version('1.2.3-beta.1.2') is None
        True

        The hyphen spelling is rejected because npm orders it differently: it is
        a single alphanumeric identifier, so '3.0.0-alpha-1' sorts *above*
        '3.0.0-alpha.6', and folding both to _alpha1/_alpha6 would invert that:

        >>> translate_version('3.0.0-alpha-1') is None
        True
        >>> translate_version('not-a-version') is None
        True

        Every successful translation is valid PMS:

        >>> all(pms_version.is_valid(translate_version(v)) for v in
        ...     ['1.2.3', '1.2.3-beta.1', '2.0.0-rc.0', '1.0.0-pre.5'])
        True
    """
    parsed = semver.parse_version(version)
    if parsed is None:
        return None

    base = '%d.%d.%d' % (parsed.major, parsed.minor, parsed.patch)

    if not parsed.prerelease:
        return base if pms_version.is_valid(base) else None

    suffix = _translate_prerelease(parsed.prerelease)
    if suffix is None:
        return None

    translated = base + suffix
    return translated if pms_version.is_valid(translated) else None


def _translate_prerelease(identifiers) -> Optional[str]:
    """
    Translate semver prerelease identifiers into a single PMS suffix.

    Accepts ``('beta', 1)``, ``('beta1',)`` and ``('beta',)``; anything else is
    rejected, including bare numbers and unknown words.
    """
    if len(identifiers) == 1:
        only = identifiers[0]
        if isinstance(only, int):
            # A bare numeric prerelease has no order-preserving PMS spelling.
            return None
        match = _WORD_NUMBER_RE.match(str(only).lower())
        if match is None:
            return None
        return '_%s%s' % (match.group(1), match.group(2))

    if len(identifiers) == 2:
        word, number = identifiers
        if isinstance(word, int) or not isinstance(number, int):
            return None
        if str(word).lower() not in PMS_PRERELEASE_WORDS:
            return None
        return '_%s%d' % (str(word).lower(), number)

    # Three or more identifiers cannot be folded into one PMS suffix.
    return None


def can_translate_version(version: str) -> bool:
    """
    Report whether a version can be represented in PMS form.

    Derived from :func:`translate_version` so a filter using this predicate
    cannot disagree with the translator that follows it.

    Args:
        version: npm version string

    Returns:
        True if the version translates

    Examples:
        >>> can_translate_version('1.2.3')
        True
        >>> can_translate_version('1.2.3-beta.1')
        True
        >>> can_translate_version('1.2.3-next.5')
        False
        >>> can_translate_version('1.0.0-0')
        False
    """
    return translate_version(version) is not None


def untranslate_version(version: str) -> Optional[str]:
    """
    Convert a PMS version back to npm form.

    Best-effort only, and callers must treat it as a hint. The translation is
    not injective: npm's ``1.2.3-beta.1`` and ``1.2.3-beta1`` both become
    ``1.2.3_beta1``, so this returns the dot-separated spelling and the caller
    should confirm it against the package's real published version list.

    Args:
        version: PMS version string

    Returns:
        npm version string, or None if the input is not a recognised PMS version

    Examples:
        >>> untranslate_version('1.2.3')
        '1.2.3'
        >>> untranslate_version('1.2.3_beta1')
        '1.2.3-beta.1'
        >>> untranslate_version('1.2.3_alpha')
        '1.2.3-alpha'
        >>> untranslate_version('2.0.0_rc0')
        '2.0.0-rc.0'
        >>> untranslate_version('not-a-version') is None
        True

        Round-trips for versions npm spelled with a dot-separated number:

        >>> originals = ['1.2.3', '1.2.3-beta.1', '2.0.0-rc.0', '1.0.0-pre.5']
        >>> [untranslate_version(translate_version(v)) for v in originals] == originals
        True
    """
    if not version or not pms_version.is_valid(version):
        return None

    match = _PMS_SUFFIX_RE.match(version)
    if match is None:
        # A plain numeric version needs no suffix handling, but must still have
        # the three components npm requires.
        return version if semver.parse_version(version) is not None else None

    base, word, number = match.groups()
    if semver.parse_version(base) is None:
        return None

    if number:
        return '%s-%s.%s' % (base, word, number)
    return '%s-%s' % (base, word)


#: PMS suffix ranking, per PMS 3.3: the four prerelease words sort below a plain
#: release, and ``_p`` above it.
_PMS_SUFFIX_RANK = {'alpha': 0, 'beta': 1, 'pre': 2, 'rc': 3, None: 4, 'p': 5}

_PMS_SORT_RE = re.compile(r'^(\d+(?:\.\d+)*)(?:_(alpha|beta|pre|rc|p)(\d*))?$')


def pms_sort_key(version: str):
    """
    Build a sort key ordering PMS versions the way portage does.

    Args:
        version: PMS version string

    Returns:
        Comparable tuple, or None if the version is not recognised

    Examples:
        >>> pms_sort_key('1.2.3') > pms_sort_key('1.2.3_rc1')
        True
        >>> pms_sort_key('1.0.0_alpha') < pms_sort_key('1.0.0_beta')
        True
        >>> pms_sort_key('1.0.0_beta2') > pms_sort_key('1.0.0_beta1')
        True
        >>> pms_sort_key('nonsense') is None
        True
    """
    match = _PMS_SORT_RE.match(version or '')
    if match is None:
        return None
    base, word, number = match.groups()
    return (
        tuple(int(part) for part in base.split('.')),
        _PMS_SUFFIX_RANK[word],
        int(number or 0),
    )


def select_order_preserving(versions):
    """
    Select the largest suffix-consistent subset of versions, newest first.

    Per-version translation cannot guarantee that a *set* of versions keeps its
    relative order, and npm makes this a real hazard: it compares an inline
    prerelease identifier such as ``beta25`` lexically, so ``beta25`` sorts
    *below* ``beta8``, whereas the PMS forms ``_beta25`` and ``_beta8`` compare
    numerically and swap. Any package mixing the ``beta.15`` and ``beta3``
    spellings has the same problem.

    Order mismatches are not cosmetic. If portage disagrees with npm about which
    version is newer, ``emerge -u`` selects the wrong one and the pinning done
    by :func:`semver.max_satisfying` is undermined.

    Versions are considered newest-first, so recent releases are retained in
    preference to old ones, and a version is kept only while it remains strictly
    ordered against everything already kept.

    Args:
        versions: npm version strings

    Returns:
        Kept version strings, ordered newest first

    Examples:
        >>> select_order_preserving(['1.0.0', '1.1.0', '2.0.0'])
        ['2.0.0', '1.1.0', '1.0.0']

        Untranslatable versions are dropped:

        >>> select_order_preserving(['1.0.0', '1.1.0-next.5'])
        ['1.0.0']

        Where two spellings would invert, the newer-by-npm one wins and the
        conflicting one is dropped rather than silently mis-ordered:

        >>> select_order_preserving(['0.9.0-beta8', '0.9.0-beta25'])
        ['0.9.0-beta8']
        >>> select_order_preserving(['5.0.0-beta3', '5.0.0-beta.15'])
        ['5.0.0-beta3']
    """
    candidates = []
    for version in versions:
        translated = translate_version(version)
        if translated is None:
            continue
        parsed = semver.parse_version(version)
        key = pms_sort_key(translated)
        if parsed is None or key is None:
            continue
        candidates.append((parsed, key, version))

    # Newest first by npm's ordering.
    candidates.sort(key=lambda item: item[0], reverse=True)

    kept = []
    last_key = None
    for _parsed, key, version in candidates:
        if last_key is not None and key >= last_key:
            # Keeping this would place it at or above a version npm considers
            # newer, inverting the order.
            continue
        kept.append(version)
        last_key = key

    return kept
