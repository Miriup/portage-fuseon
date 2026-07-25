"""
Canonical upstream-to-Gentoo license translation.

Which license names Gentoo accepts is a property of Gentoo's ``licenses/``
directory, not of any package registry, so every ecosystem needs the same
mapping. Before this module there were three separate tables — an instance
attribute in ``pip_metadata.EbuildDataExtractor``, a function-local dict in its
``_translate_spdx_expression``, and another function-local dict in the RubyGems
ebuild generator — which disagreed with each other and were each incomplete.

Consolidating them fixes three defects:

- **LGPL was reported as GPL.** The PyPI heuristics tested ``'gpl' in text``
  before ``'lgpl' in text``, and ``'lgpl'`` contains ``'gpl'``, so anything not
  matched exactly by the table (``LGPL-2.1+``, ``LGPL-3.0+``, ``LGPL v3``) fell
  into the GPL branch and was emitted with the wrong license.
- **RubyGems passed unknown licenses through verbatim**, so a name Gentoo does
  not recognise landed in ``LICENSE=`` and failed at build time.
- **The two ecosystems used different sentinels** for "no license information",
  ``all-rights-reserved`` versus ``unknown``. Only the former is a real Gentoo
  license; the latter is not.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

from typing import List, Optional, Sequence

__all__ = [
    'UNKNOWN_LICENSE',
    'SPDX_TO_GENTOO',
    'PROSE_TO_GENTOO',
    'translate_expression',
    'translate',
    'translate_list',
]


#: Gentoo's placeholder when no usable license information exists. Gentoo policy
#: is to assume all rights reserved rather than guess.
UNKNOWN_LICENSE = 'all-rights-reserved'

#: SPDX identifiers to Gentoo license names.
SPDX_TO_GENTOO = {
    'MIT': 'MIT',
    'Apache-2.0': 'Apache-2.0',
    'BSD-2-Clause': 'BSD-2',
    'BSD-3-Clause': 'BSD',
    'GPL-2.0': 'GPL-2',
    'GPL-2.0-only': 'GPL-2',
    'GPL-2.0-or-later': 'GPL-2+',
    'GPL-2.0+': 'GPL-2+',
    'GPL-3.0': 'GPL-3',
    'GPL-3.0-only': 'GPL-3',
    'GPL-3.0-or-later': 'GPL-3+',
    'GPL-3.0+': 'GPL-3+',
    'LGPL-2.1': 'LGPL-2.1',
    'LGPL-2.1-only': 'LGPL-2.1',
    'LGPL-2.1-or-later': 'LGPL-2.1+',
    'LGPL-2.1+': 'LGPL-2.1+',
    'LGPL-3.0': 'LGPL-3',
    'LGPL-3.0-only': 'LGPL-3',
    'LGPL-3.0-or-later': 'LGPL-3+',
    'LGPL-3.0+': 'LGPL-3+',
    'ISC': 'ISC',
    'MPL-2.0': 'MPL-2.0',
    'CC0-1.0': 'CC0-1.0',
    'Unlicense': 'Unlicense',
    'Python-2.0': 'PSF-2',
    'PSF-2.0': 'PSF-2',
    'Ruby': 'Ruby',
    'Artistic-2.0': 'Artistic-2',
    'Zlib': 'ZLIB',
}

#: Human-written license names, as they appear in legacy PyPI ``license`` fields
#: and gemspec ``licenses`` entries.
PROSE_TO_GENTOO = {
    'MIT License': 'MIT',
    'Apache 2.0': 'Apache-2.0',
    'Apache License 2.0': 'Apache-2.0',
    'Apache Software License': 'Apache-2.0',
    'BSD': 'BSD',
    'BSD License': 'BSD',
    'GNU General Public License v2': 'GPL-2+',
    'GNU General Public License v3': 'GPL-3+',
    'GNU Lesser General Public License v2.1': 'LGPL-2.1+',
    'GNU Lesser General Public License v3': 'LGPL-3+',
    'Python Software Foundation License': 'PSF-2',
}


def _lookup(name: str) -> Optional[str]:
    """Resolve an exact license name through both tables."""
    name = name.strip()
    if name in SPDX_TO_GENTOO:
        return SPDX_TO_GENTOO[name]
    return PROSE_TO_GENTOO.get(name)


def _guess(text: str) -> Optional[str]:
    """
    Best-effort match for a free-text license string.

    LGPL is tested before GPL: ``'lgpl'`` contains ``'gpl'``, so checking GPL
    first silently mislabels every LGPL package.
    """
    lowered = text.lower()

    if 'lgpl' in lowered or 'lesser general public' in lowered:
        later = '+' in lowered or 'later' in lowered
        if '2.1' in lowered:
            return 'LGPL-2.1+' if later else 'LGPL-2.1'
        if '3' in lowered:
            return 'LGPL-3+' if later else 'LGPL-3'
        return 'LGPL-2.1+' if later else 'LGPL-2.1'

    if 'gpl' in lowered or 'general public' in lowered:
        later = '+' in lowered or 'later' in lowered
        if '3' in lowered:
            return 'GPL-3+' if later else 'GPL-3'
        if '2' in lowered:
            return 'GPL-2+' if later else 'GPL-2'
        return 'GPL-3+'

    if 'mit' in lowered:
        return 'MIT'
    if 'apache' in lowered and '2' in lowered:
        return 'Apache-2.0'
    if 'bsd' in lowered:
        return 'BSD-2' if '2' in lowered else 'BSD'
    if 'python' in lowered or 'psf' in lowered:
        return 'PSF-2'
    if 'isc' in lowered:
        return 'ISC'
    if 'mozilla' in lowered or 'mpl' in lowered:
        return 'MPL-2.0'
    if 'unlicense' in lowered:
        return 'Unlicense'
    if 'cc0' in lowered:
        return 'CC0-1.0'
    if 'ruby' in lowered:
        return 'Ruby'

    return None


def translate_expression(expression: str) -> Optional[str]:
    """
    Translate an SPDX license expression into Gentoo ``LICENSE`` syntax.

    ``OR`` becomes Gentoo's any-of group; ``AND`` becomes a space-separated
    list. Translation is all-or-nothing: if any operand is unrecognised the
    whole expression fails, because a partial license statement is worse than
    an explicit fallback.

    Args:
        expression: SPDX expression, e.g. ``"Apache-2.0 OR BSD-2-Clause"``

    Returns:
        Gentoo LICENSE string, or None if any operand is unrecognised

    Examples:
        >>> translate_expression('MIT')
        'MIT'
        >>> translate_expression('Apache-2.0 OR BSD-2-Clause')
        '|| ( Apache-2.0 BSD-2 )'
        >>> translate_expression('MIT AND Apache-2.0')
        'MIT Apache-2.0'
        >>> translate_expression('BSD-3-Clause')
        'BSD'
        >>> translate_expression('MIT OR NotARealLicense') is None
        True
        >>> translate_expression('') is None
        True
    """
    if not expression or not expression.strip():
        return None

    if ' OR ' in expression:
        parts = [_lookup(part) for part in expression.split(' OR ')]
        if any(part is None for part in parts):
            return None
        return '|| ( %s )' % ' '.join(parts)

    if ' AND ' in expression:
        parts = [_lookup(part) for part in expression.split(' AND ')]
        if any(part is None for part in parts):
            return None
        return ' '.join(parts)

    return _lookup(expression)


def translate(license_name: str, expression: str = '') -> str:
    """
    Translate license metadata into a Gentoo ``LICENSE`` value.

    Resolution order: an SPDX expression if one is supplied and translatable,
    then an exact table match, then free-text heuristics, then the
    all-rights-reserved fallback.

    Args:
        license_name: License string from package metadata
        expression: SPDX expression, where the ecosystem provides one
            separately (PEP 639's ``license_expression``)

    Returns:
        Gentoo LICENSE value, never empty

    Examples:
        >>> translate('MIT')
        'MIT'
        >>> translate('Apache-2.0')
        'Apache-2.0'
        >>> translate('', 'Apache-2.0')
        'Apache-2.0'
        >>> translate('', 'Apache-2.0 OR BSD-2-Clause')
        '|| ( Apache-2.0 BSD-2 )'
        >>> translate('BSD-3-Clause')
        'BSD'
        >>> translate('GNU General Public License v3')
        'GPL-3+'
        >>> translate('GPL v2 or later')
        'GPL-2+'
        >>> translate('some weird mit license')
        'MIT'

        LGPL resolves to LGPL, not GPL:

        >>> translate('LGPL-2.1+')
        'LGPL-2.1+'
        >>> translate('LGPL-3.0+')
        'LGPL-3+'
        >>> translate('LGPL v3')
        'LGPL-3'
        >>> translate('GNU Lesser General Public License v3 or later')
        'LGPL-3+'

        Missing or unrecognisable information falls back rather than guessing:

        >>> translate('')
        'all-rights-reserved'
        >>> translate('Unknown License')
        'all-rights-reserved'
    """
    if expression:
        translated = translate_expression(expression)
        if translated:
            return translated

    if not license_name or not license_name.strip():
        return UNKNOWN_LICENSE

    exact = _lookup(license_name)
    if exact:
        return exact

    guessed = _guess(license_name)
    if guessed:
        return guessed

    return UNKNOWN_LICENSE


def translate_list(licenses: Sequence[str]) -> str:
    """
    Translate a list of license names into a single Gentoo ``LICENSE`` value.

    Ecosystems that expose licenses as a list, such as a gemspec's ``licenses``
    array, mean all of them apply, which is Gentoo's space-separated AND form.

    Unrecognised entries are dropped rather than passed through: an unknown name
    in ``LICENSE=`` fails the build, whereas dropping it leaves the remaining
    accurate licenses in place. If nothing survives, the fallback applies.

    Args:
        licenses: License names from package metadata

    Returns:
        Gentoo LICENSE value, never empty

    Examples:
        >>> translate_list(['MIT'])
        'MIT'
        >>> translate_list(['MIT', 'Apache-2.0'])
        'MIT Apache-2.0'
        >>> translate_list(['Ruby', 'BSD-2-Clause'])
        'Ruby BSD-2'
        >>> translate_list([])
        'all-rights-reserved'
        >>> translate_list(['NotARealLicense'])
        'all-rights-reserved'
        >>> translate_list(['MIT', 'NotARealLicense'])
        'MIT'
    """
    if not licenses:
        return UNKNOWN_LICENSE

    translated: List[str] = []
    for name in licenses:
        resolved = _lookup(name) or _guess(name)
        if resolved and resolved not in translated:
            translated.append(resolved)

    if not translated:
        return UNKNOWN_LICENSE

    return ' '.join(translated)
