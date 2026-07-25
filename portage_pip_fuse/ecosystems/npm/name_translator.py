"""
npm package name to Gentoo package name translation.

PMS allows only ``[A-Za-z0-9+_-]`` in a package name. npm additionally permits
``.`` and, for scoped packages, a leading ``@`` and an internal ``/``, so some
rewriting is unavoidable.

The scope separator is translated to ``+`` rather than ``-``. That deserves
explanation, because ``-`` is the obvious choice and it is wrong: npm's scope
feature arrived after the flat names had already been taken, so the flattened
form of a scoped package almost always names a *different, real* package. Every
case sampled collided --- ``@babel/core`` against ``babel-core``,
``@vue/cli-service`` against ``vue-cli-service``, ``@types/node`` against
``types-node``, and so on. ``babel-core`` is literally the pre-scoping release
line of ``@babel/core``, with unrelated versions. Aliasing the two would let
portage install one believing it had the other.

``+`` avoids this completely: npm forbids it in package names, so the mapping is
injective and needs no lookup table to reverse. It is legal mid-name under PMS
--- ``sys-libs/libstdc++-v3`` is a real package, which also demonstrates that a
trailing ``-v3`` still parses --- and it matches the spelling ``npm.eclass``
already uses for the store path, ``@vue+cli-service``.

The remaining ambiguity is ``.`` becoming ``_``, which can collide with a real
underscored package: ``socket.io`` and ``socket_io`` both exist. PMS offers no
fourth separator to spend on this, so the collision is detectable via
:func:`find_collision` and resolvable through the shared
``NameTranslationPatchStore``, the same mechanism PyPI uses for
``torch`` -> ``sci-ml/pytorch``.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import re
from typing import Dict, List, Optional

__all__ = [
    'SCOPE_SEPARATOR',
    'npm_to_gentoo',
    'gentoo_to_npm',
    'is_valid_npm_name',
    'is_valid_gentoo_name',
    'find_collision',
    'NpmNameTranslator',
]

#: Character standing in for the ``@scope/`` separator. npm forbids it in
#: package names, which is exactly why it is safe to use.
SCOPE_SEPARATOR = '+'

#: npm package names: lowercase letters, digits, and '-', '_', '.', optionally
#: within an '@scope/' prefix. Historic packages may carry uppercase.
_NPM_NAME_RE = re.compile(
    r'^(?:@[A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*$'
)

#: PMS 3.1.2 package name.
_GENTOO_NAME_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9+_-]*$')

#: A trailing '-<digits>' makes a package name ambiguous with a version, so PMS
#: forbids it. Replacing the hyphen with an underscore keeps 'http-2' distinct
#: from the separate package 'http2'.
_TRAILING_DIGITS_RE = re.compile(r'^(.*)-(\d+)$')


def is_valid_npm_name(name: str) -> bool:
    """
    Check whether a string is a well-formed npm package name.

    Args:
        name: Candidate package name

    Returns:
        True if the name is valid for npm

    Examples:
        >>> is_valid_npm_name('chalk')
        True
        >>> is_valid_npm_name('@vue/cli-service')
        True
        >>> is_valid_npm_name('socket.io')
        True
        >>> is_valid_npm_name('lodash.merge')
        True
        >>> is_valid_npm_name('')
        False
        >>> is_valid_npm_name('@noslash')
        False
        >>> is_valid_npm_name('has space')
        False
    """
    if not name or len(name) > 214:
        return False
    return bool(_NPM_NAME_RE.match(name))


def is_valid_gentoo_name(name: str) -> bool:
    """
    Check whether a string is a valid Gentoo package name per PMS 3.1.2.

    Args:
        name: Candidate package name

    Returns:
        True if the name is valid for Gentoo

    Examples:
        >>> is_valid_gentoo_name('chalk')
        True
        >>> is_valid_gentoo_name('vue+cli-service')
        True
        >>> is_valid_gentoo_name('socket_io')
        True
        >>> is_valid_gentoo_name('http_2')
        True
        >>> is_valid_gentoo_name('socket.io')
        False
        >>> is_valid_gentoo_name('@vue/cli')
        False
        >>> is_valid_gentoo_name('-leading-hyphen')
        False
        >>> is_valid_gentoo_name('')
        False

        A trailing '-<digits>' is invalid because it cannot be told apart from
        a version:

        >>> is_valid_gentoo_name('http-2')
        False
    """
    if not name:
        return False
    if not _GENTOO_NAME_RE.match(name):
        return False
    return _TRAILING_DIGITS_RE.match(name) is None


def npm_to_gentoo(name: str) -> Optional[str]:
    """
    Translate an npm package name to a Gentoo package name.

    Args:
        name: npm package name, possibly scoped

    Returns:
        Gentoo package name, or None if the input is not a valid npm name

    Examples:
        >>> npm_to_gentoo('chalk')
        'chalk'
        >>> npm_to_gentoo('vue-cli-service')
        'vue-cli-service'

        Scoped names keep the scope, joined with '+':

        >>> npm_to_gentoo('@vue/cli-service')
        'vue+cli-service'
        >>> npm_to_gentoo('@babel/core')
        'babel+core'
        >>> npm_to_gentoo('@types/node')
        'types+node'

        which keeps them distinct from the unrelated flat packages that really
        exist under those names:

        >>> npm_to_gentoo('@babel/core') != npm_to_gentoo('babel-core')
        True

        Dots become underscores, since PMS forbids them:

        >>> npm_to_gentoo('socket.io')
        'socket_io'
        >>> npm_to_gentoo('lodash.merge')
        'lodash_merge'

        A trailing '-<digits>' would be read as a version, so the hyphen
        becomes an underscore. This keeps 'http-2' distinct from 'http2':

        >>> npm_to_gentoo('http-2')
        'http_2'
        >>> npm_to_gentoo('http2')
        'http2'

        Every result is a valid Gentoo package name:

        >>> all(is_valid_gentoo_name(npm_to_gentoo(n)) for n in
        ...     ['chalk', '@vue/cli-service', 'socket.io', 'http-2', 'lodash.merge'])
        True

        Invalid input is rejected rather than coerced:

        >>> npm_to_gentoo('') is None
        True
        >>> npm_to_gentoo('has space') is None
        True
    """
    if not is_valid_npm_name(name):
        return None

    translated = name.strip()

    if translated.startswith('@'):
        scope, _, remainder = translated[1:].partition('/')
        translated = scope + SCOPE_SEPARATOR + remainder

    translated = translated.replace('.', '_')

    match = _TRAILING_DIGITS_RE.match(translated)
    if match is not None:
        translated = '%s_%s' % (match.group(1), match.group(2))

    return translated if is_valid_gentoo_name(translated) else None


def gentoo_to_npm(name: str) -> Optional[str]:
    """
    Translate a Gentoo package name back to its npm name.

    The scope half is exact, because ``+`` cannot occur in an npm name. The
    underscore half is a best guess: an ``_`` may be original, or may stand for
    a ``.`` or for a trailing ``-<digits>``. This returns the most common
    reading -- underscores left alone -- so callers that need certainty should
    check the result against the registry, or consult
    :class:`NpmNameTranslator`, which remembers the mappings it has performed.

    Args:
        name: Gentoo package name

    Returns:
        npm package name, or None if the input is not a valid Gentoo name

    Examples:
        >>> gentoo_to_npm('chalk')
        'chalk'
        >>> gentoo_to_npm('vue+cli-service')
        '@vue/cli-service'
        >>> gentoo_to_npm('babel+core')
        '@babel/core'
        >>> gentoo_to_npm('socket_io')
        'socket_io'
        >>> gentoo_to_npm('') is None
        True

        The scope round-trip is exact:

        >>> scoped = ['@vue/cli-service', '@babel/core', '@types/node']
        >>> [gentoo_to_npm(npm_to_gentoo(n)) for n in scoped] == scoped
        True
    """
    if not is_valid_gentoo_name(name):
        return None

    if SCOPE_SEPARATOR in name:
        scope, _, remainder = name.partition(SCOPE_SEPARATOR)
        return '@%s/%s' % (scope, remainder)

    return name


def find_collision(name: str) -> Optional[str]:
    """
    Return another npm name that would translate to the same Gentoo name.

    Only the dot-to-underscore rewrite can collide; the scope rewrite cannot.
    Callers use this to warn, or to look up an override in the shared
    ``NameTranslationPatchStore``. Whether the returned name is actually
    published is not checked here -- that requires the registry.

    Args:
        name: npm package name

    Returns:
        The colliding npm name, or None if this name cannot collide

    Examples:
        >>> find_collision('socket.io')
        'socket_io'
        >>> find_collision('socket_io')
        'socket.io'
        >>> find_collision('chalk') is None
        True
        >>> find_collision('@vue/cli-service') is None
        True
    """
    if not is_valid_npm_name(name):
        return None

    # A scoped name's '+' is unambiguous, so only the bare name can collide.
    bare = name.rpartition('/')[2] if name.startswith('@') else name

    if '.' in bare:
        return name.replace('.', '_')
    if '_' in bare:
        return name.replace('_', '.')
    return None


class NpmNameTranslator:
    """
    Name translator that remembers the rewrites it has performed.

    Mirrors ``RubyGemsNameTranslator``: translating forward registers the
    reverse mapping, so a name whose underscores came from dots can be reversed
    exactly rather than guessed.

    Examples:
        >>> translator = NpmNameTranslator()
        >>> translator.npm_to_gentoo('socket.io')
        'socket_io'
        >>> translator.gentoo_to_npm('socket_io')
        'socket.io'

        Without a registered mapping the underscore is assumed original:

        >>> NpmNameTranslator().gentoo_to_npm('socket_io')
        'socket_io'

        Scopes need no registration:

        >>> NpmNameTranslator().gentoo_to_npm('vue+cli-service')
        '@vue/cli-service'
    """

    def __init__(self, overrides: Optional[Dict[str, str]] = None):
        """
        Args:
            overrides: Explicit npm-name to Gentoo-name mappings, as supplied by
                the ``NameTranslationPatchStore``
        """
        self._npm_to_gentoo: Dict[str, str] = {}
        self._gentoo_to_npm: Dict[str, str] = {}

        for npm_name, gentoo_name in (overrides or {}).items():
            self.register(npm_name, gentoo_name)

    def register(self, npm_name: str, gentoo_name: str) -> None:
        """Record a mapping in both directions."""
        self._npm_to_gentoo[npm_name] = gentoo_name
        self._gentoo_to_npm[gentoo_name] = npm_name

    def npm_to_gentoo(self, name: str) -> Optional[str]:
        """Translate forward, registering the reverse mapping when it differs."""
        if name in self._npm_to_gentoo:
            return self._npm_to_gentoo[name]

        translated = npm_to_gentoo(name)
        if translated is not None and translated != name:
            self.register(name, translated)
        return translated

    def gentoo_to_npm(self, name: str) -> Optional[str]:
        """Translate back, preferring a remembered mapping over the guess."""
        if name in self._gentoo_to_npm:
            return self._gentoo_to_npm[name]
        return gentoo_to_npm(name)

    def known_mappings(self) -> List[str]:
        """List the npm names with a registered rewrite, for diagnostics."""
        return sorted(self._npm_to_gentoo)
