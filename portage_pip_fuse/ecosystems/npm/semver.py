"""
npm semver range parsing and version selection.

This is the one genuinely npm-specific algorithm in the ecosystem plugin, and
the riskiest: every generated ebuild's RDEPEND pins a concrete version chosen
here, so disagreeing with npm's own resolver produces ebuilds that reference
versions nothing else would have picked.

Only *one level* of resolution is needed. Each ebuild pins its own direct
dependencies and portage walks the graph transitively, so this module never has
to solve the whole closure -- it only answers "given this range and this list of
published versions, which version would npm choose?". That is a comparison
problem, not a SAT problem.

Scope: the full range grammar npm accepts -- exact versions, comparators,
caret, tilde, x-ranges, hyphen ranges, whitespace-separated conjunction and
``||`` disjunction -- plus semver precedence including prerelease ordering and
npm's rule restricting when prerelease versions may satisfy a range.

Deliberately excluded: ``latest`` and other dist-tags, ``file:``/``git:``/URL
specifiers, and ``workspace:`` protocols. Those are not version ranges and the
caller must recognise them before asking this module anything.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import re
from typing import Any, List, Optional, Sequence, Tuple, Union

__all__ = [
    'Version',
    'parse_version',
    'compare',
    'parse_range',
    'satisfies',
    'max_satisfying',
    'is_range',
]


# Numeric identifier: no leading zeros, matching node-semver's non-loose mode.
_NUM = r'0|[1-9]\d*'
# A prerelease identifier is numeric, or alphanumeric containing a letter or '-'.
_PRE_ID = r'(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)'
_BUILD_ID = r'[0-9a-zA-Z-]+'

_VERSION_RE = re.compile(
    r'^v?(%s)\.(%s)\.(%s)'
    r'(?:-(%s(?:\.%s)*))?'
    r'(?:\+(%s(?:\.%s)*))?$'
    % (_NUM, _NUM, _NUM, _PRE_ID, _PRE_ID, _BUILD_ID, _BUILD_ID)
)

# A partial version, as it may appear inside a range: trailing components may be
# absent or a wildcard.
_XR = r'[xX*]|0|[1-9]\d*'
_PARTIAL_RE = re.compile(
    r'^v?(%s)(?:\.(%s)(?:\.(%s)'
    r'(?:-(%s(?:\.%s)*))?'
    r'(?:\+(%s(?:\.%s)*))?'
    r')?)?$'
    % (_XR, _XR, _XR, _PRE_ID, _PRE_ID, _BUILD_ID, _BUILD_ID)
)

_HYPHEN_RE = re.compile(r'\s+-\s+')
_OPERATOR_RE = re.compile(r'^(>=|<=|>|<|=|\^|~>|~)\s*(.*)$')

# npm allows whitespace between an operator and its version ('>= 1.2.3'). The
# space has to go before the range is split on whitespace, or the operator and
# the version become two independent atoms and the range silently changes
# meaning: '>= 1.2.3' would parse as 'anything AND exactly 1.2.3'.
_OPERATOR_SPACE_RE = re.compile(r'(>=|<=|>|<|=|\^|~>|~)\s+')


def _split_prerelease(text: Optional[str]) -> Tuple[Union[int, str], ...]:
    """Split a prerelease string into comparable identifiers."""
    if not text:
        return ()
    parts: List[Union[int, str]] = []
    for chunk in text.split('.'):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            parts.append(chunk)
    return tuple(parts)


class Version:
    """
    A parsed semantic version.

    Build metadata is retained for display but ignored in comparisons, as the
    specification requires.

    Examples:
        >>> v = Version(1, 2, 3)
        >>> (v.major, v.minor, v.patch)
        (1, 2, 3)
        >>> str(Version(1, 2, 3, ('beta', 1)))
        '1.2.3-beta.1'
        >>> Version(1, 2, 3) > Version(1, 2, 3, ('beta', 1))
        True
    """

    __slots__ = ('major', 'minor', 'patch', 'prerelease', 'build')

    def __init__(
        self,
        major: int,
        minor: int,
        patch: int,
        prerelease: Tuple[Union[int, str], ...] = (),
        build: str = '',
    ):
        self.major = major
        self.minor = minor
        self.patch = patch
        self.prerelease = prerelease
        self.build = build

    def __str__(self) -> str:
        text = '%d.%d.%d' % (self.major, self.minor, self.patch)
        if self.prerelease:
            text += '-' + '.'.join(str(part) for part in self.prerelease)
        if self.build:
            text += '+' + self.build
        return text

    def __repr__(self) -> str:
        return 'Version(%r)' % str(self)

    @property
    def tuple(self) -> Tuple[int, int, int]:
        """The (major, minor, patch) triple, which prerelease rules key on."""
        return (self.major, self.minor, self.patch)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return compare(self, other) == 0

    def __hash__(self) -> int:
        return hash((self.tuple, self.prerelease))

    def __lt__(self, other: 'Version') -> bool:
        return compare(self, other) < 0

    def __le__(self, other: 'Version') -> bool:
        return compare(self, other) <= 0

    def __gt__(self, other: 'Version') -> bool:
        return compare(self, other) > 0

    def __ge__(self, other: 'Version') -> bool:
        return compare(self, other) >= 0


def parse_version(text: str) -> Optional[Version]:
    """
    Parse a semver string.

    Args:
        text: Version string, optionally prefixed with ``v`` or ``=``

    Returns:
        A :class:`Version`, or None if the string is not valid semver

    Examples:
        >>> str(parse_version('1.2.3'))
        '1.2.3'
        >>> str(parse_version('v1.2.3'))
        '1.2.3'
        >>> str(parse_version('1.2.3-beta.1'))
        '1.2.3-beta.1'
        >>> str(parse_version('1.2.3+build.5'))
        '1.2.3+build.5'
        >>> parse_version('1.2') is None
        True
        >>> parse_version('1.02.3') is None
        True
        >>> parse_version('not-a-version') is None
        True
    """
    if not text:
        return None

    match = _VERSION_RE.match(text.strip().lstrip('='))
    if not match:
        return None

    major, minor, patch, prerelease, build = match.groups()
    return Version(
        int(major), int(minor), int(patch),
        _split_prerelease(prerelease),
        build or '',
    )


def _compare_prerelease(
    left: Tuple[Union[int, str], ...],
    right: Tuple[Union[int, str], ...],
) -> int:
    """Compare prerelease identifier lists per semver precedence rules."""
    # A version without a prerelease outranks one with it.
    if not left and not right:
        return 0
    if not left:
        return 1
    if not right:
        return -1

    for a, b in zip(left, right):
        if a == b:
            continue
        a_num, b_num = isinstance(a, int), isinstance(b, int)
        if a_num and b_num:
            return -1 if a < b else 1
        # Numeric identifiers always sort below alphanumeric ones.
        if a_num:
            return -1
        if b_num:
            return 1
        return -1 if str(a) < str(b) else 1

    # All shared identifiers equal: the shorter list sorts lower.
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def compare(left: Version, right: Version) -> int:
    """
    Compare two versions, returning -1, 0 or 1.

    Build metadata is ignored, as the specification requires.

    Examples:
        >>> compare(parse_version('1.2.3'), parse_version('1.2.4'))
        -1
        >>> compare(parse_version('1.2.3'), parse_version('1.2.3'))
        0
        >>> compare(parse_version('2.0.0'), parse_version('1.9.9'))
        1
        >>> compare(parse_version('1.2.3'), parse_version('1.2.3+build'))
        0
        >>> compare(parse_version('1.2.3-alpha'), parse_version('1.2.3'))
        -1
        >>> compare(parse_version('1.2.3-alpha.1'), parse_version('1.2.3-alpha.2'))
        -1
        >>> compare(parse_version('1.2.3-alpha'), parse_version('1.2.3-alpha.1'))
        -1
        >>> compare(parse_version('1.2.3-1'), parse_version('1.2.3-alpha'))
        -1
    """
    if left.tuple != right.tuple:
        return -1 if left.tuple < right.tuple else 1
    return _compare_prerelease(left.prerelease, right.prerelease)


class _Comparator:
    """A single ``<operator><version>`` test within a comparator set."""

    __slots__ = ('operator', 'version')

    #: A comparator that matches everything, used for '*' and empty ranges.
    ANY = '*'

    def __init__(self, operator: str, version: Optional[Version]):
        self.operator = operator
        self.version = version

    def __repr__(self) -> str:
        if self.operator == self.ANY:
            return '<Comparator *>'
        return '<Comparator %s%s>' % (self.operator, self.version)

    def test(self, version: Version) -> bool:
        if self.operator == self.ANY:
            return True

        result = compare(version, self.version)
        if self.operator == '>':
            return result > 0
        if self.operator == '>=':
            return result >= 0
        if self.operator == '<':
            return result < 0
        if self.operator == '<=':
            return result <= 0
        return result == 0


def _is_wildcard(part: Optional[str]) -> bool:
    return part is None or part in ('x', 'X', '*')


def _parse_partial(text: str):
    """
    Parse a possibly-partial version from inside a range.

    Returns a (major, minor, patch, prerelease) tuple where absent or wildcard
    components are None, or None if the text is not a partial version.
    """
    if text in ('', '*', 'x', 'X'):
        return (None, None, None, ())

    match = _PARTIAL_RE.match(text.strip())
    if not match:
        return None

    major, minor, patch, prerelease, _build = match.groups()
    return (
        None if _is_wildcard(major) else int(major),
        None if _is_wildcard(minor) else int(minor),
        None if _is_wildcard(patch) else int(patch),
        _split_prerelease(prerelease),
    )


#: Sentinel prerelease used to widen synthesized bounds under
#: include_prerelease. npm appends '-0' to the bounds it derives from partial
#: versions, rather than switching off the prerelease restriction: '^1.0.0'
#: becomes '>=1.0.0-0 <2.0.0-0'. The distinction matters at the upper bound,
#: where '<2.0.0' would admit 2.0.0-beta.1 but '<2.0.0-0' does not, since a
#: numeric prerelease identifier sorts below an alphanumeric one.
_PRERELEASE_SENTINEL: Tuple[Union[int, str], ...] = (0,)


def _lower(
    major: int,
    minor: int,
    patch: int,
    prerelease: Tuple[Union[int, str], ...],
    sentinel: Tuple[Union[int, str], ...],
) -> Version:
    """Build a synthesized lower bound, applying the sentinel if requested."""
    return Version(major, minor, patch, prerelease or sentinel)


def _upper(major: int, minor: int, patch: int) -> Version:
    """
    Build a synthesized upper bound.

    The sentinel is unconditional here: npm expands '^1.2.3' to
    '>=1.2.3 <2.0.0-0' even with includePrerelease off, so that no 2.0.0
    prerelease slips past the bound. Verified against
    ``new semver.Range(r).range``.
    """
    return Version(major, minor, patch, _PRERELEASE_SENTINEL)


def _expand(
    text: str,
    sentinel: Tuple[Union[int, str], ...] = (),
) -> Optional[List[_Comparator]]:
    """
    Expand one range atom into an equivalent list of comparators.

    Args:
        text: A single range atom, e.g. '^1.2.3' or '>=1.2'
        sentinel: Prerelease to apply to synthesized bounds; the caller passes
            :data:`_PRERELEASE_SENTINEL` when include_prerelease is in effect

    Returns:
        Equivalent comparators, or None if the atom is not a valid range
    """
    text = text.strip()
    if not text:
        return [_Comparator(_Comparator.ANY, None)]

    match = _OPERATOR_RE.match(text)
    operator, rest = (match.group(1), match.group(2)) if match else ('', text)
    # '~>' is an alias for '~' that npm tolerates.
    if operator == '~>':
        operator = '~'

    parsed = _parse_partial(rest)
    if parsed is None:
        return None

    major, minor, patch, prerelease = parsed

    if major is None:
        # A bare wildcard matches anything, whatever operator precedes it.
        return [_Comparator(_Comparator.ANY, None)]

    if operator == '^':
        # The lower-bound sentinel reaches only the major==0 branches of npm's
        # caret expansion, plus any partial version. '^1.2.3' stays '>=1.2.3'
        # under includePrerelease while '^0.2.3' becomes '>=0.2.3-0'. That is an
        # artifact of how node-semver's branches are written rather than a
        # principled rule, but it is the observable behaviour.
        caret_partial = minor is None or patch is None
        caret_sentinel = sentinel if (caret_partial or major == 0) else ()
        lower = _lower(major, minor or 0, patch or 0, prerelease, caret_sentinel)
        # The caret pins the leftmost non-zero component, so where that
        # component sits determines the upper bound.
        if major != 0:
            upper = _upper(major + 1, 0, 0)
        elif minor is None:
            upper = _upper(1, 0, 0)
        elif minor != 0:
            upper = _upper(0, minor + 1, 0)
        elif patch is None:
            upper = _upper(0, minor + 1, 0)
        else:
            upper = _upper(0, 0, patch + 1)
        return [_Comparator('>=', lower), _Comparator('<', upper)]

    if operator == '~':
        # Tilde never widens its lower bound, not even for a partial version.
        lower = _lower(major, minor or 0, patch or 0, prerelease, ())
        if minor is None:
            upper = _upper(major + 1, 0, 0)
        else:
            upper = _upper(major, minor + 1, 0)
        return [_Comparator('>=', lower), _Comparator('<', upper)]

    if operator in ('', '='):
        if minor is None:
            return [_Comparator('>=', _lower(major, 0, 0, (), sentinel)),
                    _Comparator('<', _upper(major + 1, 0, 0))]
        if patch is None:
            return [_Comparator('>=', _lower(major, minor, 0, (), sentinel)),
                    _Comparator('<', _upper(major, minor + 1, 0))]
        # A fully-specified version is an exact comparator, never widened.
        return [_Comparator('=', Version(major, minor, patch, prerelease))]

    # Inequalities against a partial version widen to the implied bound.
    if operator == '>':
        if minor is None:
            return [_Comparator('>=', _lower(major + 1, 0, 0, (), sentinel))]
        if patch is None:
            return [_Comparator('>=', _lower(major, minor + 1, 0, (), sentinel))]
        return [_Comparator('>', Version(major, minor, patch, prerelease))]

    if operator == '>=':
        if minor is None or patch is None:
            return [_Comparator(
                '>=', _lower(major, minor or 0, patch or 0, prerelease, sentinel))]
        return [_Comparator('>=', Version(major, minor, patch, prerelease))]

    if operator == '<':
        if minor is None or patch is None:
            return [_Comparator(
                '<', _lower(major, minor or 0, patch or 0, prerelease, sentinel))]
        return [_Comparator('<', Version(major, minor, patch, prerelease))]

    if operator == '<=':
        if minor is None:
            return [_Comparator('<', _upper(major + 1, 0, 0))]
        if patch is None:
            return [_Comparator('<', _upper(major, minor + 1, 0))]
        return [_Comparator('<=', Version(major, minor, patch, prerelease))]

    return None


def _expand_hyphen(
    lower_text: str,
    upper_text: str,
    sentinel: Tuple[Union[int, str], ...] = (),
) -> Optional[List[_Comparator]]:
    """Expand ``a - b`` into an inclusive comparator pair."""
    lower = _parse_partial(lower_text)
    upper = _parse_partial(upper_text)
    if lower is None or upper is None:
        return None

    comparators: List[_Comparator] = []

    l_major, l_minor, l_patch, l_pre = lower
    if l_major is None:
        comparators.append(_Comparator('>=', _lower(0, 0, 0, (), sentinel)))
    else:
        comparators.append(_Comparator(
            '>=', _lower(l_major, l_minor or 0, l_patch or 0, l_pre, sentinel)))

    u_major, u_minor, u_patch, u_pre = upper
    if u_major is None:
        comparators.append(_Comparator(_Comparator.ANY, None))
    elif u_minor is None:
        # '1.2.3 - 2' means everything below 3.0.0.
        comparators.append(_Comparator('<', _upper(u_major + 1, 0, 0)))
    elif u_patch is None:
        comparators.append(
            _Comparator('<', _upper(u_major, u_minor + 1, 0)))
    else:
        comparators.append(
            _Comparator('<=', Version(u_major, u_minor, u_patch, u_pre)))

    return comparators


def parse_range(
    text: str,
    include_prerelease: bool = False,
) -> Optional[List[List[_Comparator]]]:
    """
    Parse an npm range into a disjunction of comparator sets.

    The result is a list of alternatives, each a list of comparators that must
    all hold: ``[[a, b], [c]]`` means ``(a AND b) OR c``.

    Args:
        text: npm range expression
        include_prerelease: Widen synthesized bounds with the '-0' prerelease
            sentinel, matching npm's includePrerelease option

    Returns:
        Parsed range, or None if the expression is not a valid range

    Examples:
        >>> len(parse_range('^1.0.0'))
        1
        >>> len(parse_range('^1.0.0 || ^2.0.0'))
        2
        >>> parse_range('not a range') is None
        True
    """
    if text is None:
        return None

    sentinel = _PRERELEASE_SENTINEL if include_prerelease else ()
    alternatives: List[List[_Comparator]] = []

    for part in text.split('||'):
        part = part.strip()

        if not part:
            alternatives.append([_Comparator(_Comparator.ANY, None)])
            continue

        # Hyphen ranges are detected before operator-space trimming, because
        # the ' - ' separator is itself whitespace-delimited.
        hyphen = _HYPHEN_RE.split(part)
        if len(hyphen) == 2:
            expanded = _expand_hyphen(hyphen[0], hyphen[1], sentinel)
            if expanded is None:
                return None
            alternatives.append(expanded)
            continue

        # Bind each operator to its version before splitting on whitespace.
        part = _OPERATOR_SPACE_RE.sub(r'\1', part)

        comparators: List[_Comparator] = []
        for atom in part.split():
            expanded = _expand(atom, sentinel)
            if expanded is None:
                return None
            comparators.extend(expanded)

        alternatives.append(comparators or [_Comparator(_Comparator.ANY, None)])

    return alternatives or None


def is_range(text: str) -> bool:
    """
    Report whether a specifier is a version range this module understands.

    Callers use this to filter out dist-tags, URLs, git specifiers and
    ``workspace:`` protocols, none of which are ranges.

    Examples:
        >>> is_range('^1.0.0')
        True
        >>> is_range('*')
        True
        >>> is_range('latest')
        False
        >>> is_range('git+https://github.com/u/r.git')
        False
        >>> is_range('file:../local')
        False
        >>> is_range('workspace:*')
        False
    """
    if text is None:
        return False
    return parse_range(text) is not None


def _satisfies_set(
    version: Version,
    comparators: Sequence[_Comparator],
    include_prerelease: bool,
) -> bool:
    """Test one comparator set, applying npm's prerelease restriction."""
    for comparator in comparators:
        if not comparator.test(version):
            return False

    if not version.prerelease or include_prerelease:
        return True

    # npm's rule: a prerelease version may only satisfy a comparator set if some
    # comparator in that set pins the same major.minor.patch *and* itself names a
    # prerelease. Without this, '^1.0.0' would match '2.0.0-beta.1', pulling
    # unreleased majors into ordinary ranges.
    for comparator in comparators:
        if comparator.version is None:
            continue
        if comparator.version.prerelease and \
                comparator.version.tuple == version.tuple:
            return True

    return False


def satisfies(
    version: Union[str, Version],
    range_text: str,
    include_prerelease: bool = False,
) -> bool:
    """
    Report whether a version satisfies an npm range.

    Args:
        version: Version string or parsed :class:`Version`
        range_text: npm range expression
        include_prerelease: Allow prerelease versions to satisfy ranges that do
            not explicitly mention a prerelease

    Returns:
        True if the version satisfies the range

    Examples:
        >>> satisfies('1.2.3', '^1.0.0')
        True
        >>> satisfies('2.0.0', '^1.0.0')
        False
        >>> satisfies('1.2.3', '~1.2.0')
        True
        >>> satisfies('1.3.0', '~1.2.0')
        False
        >>> satisfies('1.2.3', '>=1.0.0 <2.0.0')
        True
        >>> satisfies('2.5.0', '^1.0.0 || ^2.0.0')
        True
        >>> satisfies('1.5.0', '1.2.3 - 2.3.4')
        True
        >>> satisfies('1.2.3', '*')
        True
        >>> satisfies('1.2.7', '1.2.x')
        True

        A prerelease only satisfies a range that names one at the same version:

        >>> satisfies('2.0.0-beta.1', '^1.0.0')
        False
        >>> satisfies('1.2.3-beta.2', '^1.2.3-beta.1')
        True
        >>> satisfies('1.2.4-beta.1', '^1.2.3-beta.1')
        False
        >>> satisfies('1.2.4-beta.1', '^1.2.3-beta.1', include_prerelease=True)
        True
    """
    parsed_version = version if isinstance(version, Version) \
        else parse_version(version)
    if parsed_version is None:
        return False

    alternatives = parse_range(range_text, include_prerelease)
    if alternatives is None:
        return False

    return any(
        _satisfies_set(parsed_version, comparators, include_prerelease)
        for comparators in alternatives
    )


def max_satisfying(
    versions: Sequence[str],
    range_text: str,
    include_prerelease: bool = False,
) -> Optional[str]:
    """
    Return the highest version from a list that satisfies a range.

    This is the function ebuild generation depends on: it answers which version
    npm would resolve a dependency range to, given the versions the registry
    publishes.

    Args:
        versions: Published version strings; unparseable entries are ignored
        range_text: npm range expression
        include_prerelease: Allow prerelease versions to satisfy ranges that do
            not explicitly mention a prerelease

    Returns:
        The matching version string as given, or None if nothing matches

    Examples:
        >>> max_satisfying(['1.0.0', '1.2.3', '2.0.0'], '^1.0.0')
        '1.2.3'
        >>> max_satisfying(['1.0.0', '1.2.3', '2.0.0'], '^2.0.0')
        '2.0.0'
        >>> max_satisfying(['1.0.0', '1.2.3'], '^3.0.0') is None
        True
        >>> max_satisfying(['1.0.0', '2.0.0-beta.1'], '*')
        '1.0.0'

        The original spelling is returned, not a normalised one:

        >>> max_satisfying(['v1.0.0'], '^1.0.0')
        'v1.0.0'
    """
    alternatives = parse_range(range_text, include_prerelease)
    if alternatives is None:
        return None

    best_text: Optional[str] = None
    best_version: Optional[Version] = None

    for text in versions:
        parsed = parse_version(text)
        if parsed is None:
            continue
        if not any(_satisfies_set(parsed, comparators, include_prerelease)
                   for comparators in alternatives):
            continue
        if best_version is None or compare(parsed, best_version) > 0:
            best_version = parsed
            best_text = text

    return best_text
