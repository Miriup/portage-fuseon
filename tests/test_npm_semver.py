"""
Tests for npm semver range parsing and version selection.

This is the highest-risk module in the npm ecosystem plugin: every generated
ebuild pins its dependencies to versions chosen by ``max_satisfying``, so any
disagreement with npm's own resolver produces ebuilds referencing versions
nothing else would pick. The tests are therefore built around a differential
fixture generated from npm's own bundled semver rather than around hand-written
expectations, which would only encode my understanding of the spec.

``tests/data/npm_semver_golden.json`` holds:

- a synthetic matrix of 30 versions x 57 ranges x both includePrerelease values,
  plus every pairwise version comparison; and
- one real registry case per distinct range syntax observed across the
  dependency declarations of 18 popular packages, with real published version
  lists.

Every expectation in it was produced by semver itself, never by the code under
test. A live differential test also runs when node and npm's semver are present,
so drift in npm's behaviour is detectable rather than silently baked in.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import doctest
import json
import os
import pathlib
import subprocess

import pytest

from portage_pip_fuse.ecosystems.npm import semver as sv

GOLDEN_PATH = pathlib.Path(__file__).parent / 'data' / 'npm_semver_golden.json'


def _golden():
    with GOLDEN_PATH.open() as handle:
        return json.load(handle)


GOLDEN = _golden()


def _key(*parts):
    """Reproduce the JS JSON.stringify key format used in the fixture."""
    return json.dumps(list(parts), separators=(',', ':'))


class TestParseVersion:

    @pytest.mark.parametrize('text,expected', [
        ('1.2.3', '1.2.3'),
        ('v1.2.3', '1.2.3'),
        ('=1.2.3', '1.2.3'),
        (' 1.2.3 ', '1.2.3'),
        ('1.2.3-beta.1', '1.2.3-beta.1'),
        ('1.2.3+build.5', '1.2.3+build.5'),
        ('0.0.0', '0.0.0'),
        ('10.20.30', '10.20.30'),
    ])
    def test_parses(self, text, expected):
        assert str(sv.parse_version(text)) == expected

    @pytest.mark.parametrize('text', [
        '', '1.2', '1', 'x.y.z', '1.02.3', 'not-a-version', '1.2.3.4', None,
    ])
    def test_rejects(self, text):
        assert sv.parse_version(text) is None

    def test_build_metadata_is_ignored_in_comparison(self):
        assert sv.parse_version('1.2.3+a') == sv.parse_version('1.2.3+b')


class TestCompare:

    @pytest.mark.parametrize('left,right,expected', [
        ('1.2.3', '1.2.4', -1),
        ('1.2.3', '1.2.3', 0),
        ('2.0.0', '1.9.9', 1),
        ('1.2.3', '1.2.3+build', 0),
        ('1.2.3-alpha', '1.2.3', -1),
        ('1.2.3-alpha.1', '1.2.3-alpha.2', -1),
        ('1.2.3-alpha', '1.2.3-alpha.1', -1),
        ('1.2.3-1', '1.2.3-alpha', -1),
        ('10.0.0', '9.0.0', 1),
    ])
    def test_orders(self, left, right, expected):
        assert sv.compare(sv.parse_version(left), sv.parse_version(right)) == expected

    def test_numeric_prerelease_sorts_below_alphanumeric(self):
        """Spec rule that is easy to get wrong with a naive string compare."""
        assert sv.parse_version('1.0.0-1') < sv.parse_version('1.0.0-alpha')

    def test_rich_comparison_operators(self):
        a, b = sv.parse_version('1.0.0'), sv.parse_version('2.0.0')
        assert a < b and a <= b and b > a and b >= a and a != b


class TestRangeExpansion:
    """Range shapes, asserted directly for readability."""

    @pytest.mark.parametrize('version,range_text,expected', [
        # caret keeps the leftmost non-zero component
        ('1.9.9', '^1.2.3', True), ('2.0.0', '^1.2.3', False),
        ('0.2.9', '^0.2.3', True), ('0.3.0', '^0.2.3', False),
        ('0.0.3', '^0.0.3', True), ('0.0.4', '^0.0.3', False),
        # tilde allows patch-level changes
        ('1.2.9', '~1.2.3', True), ('1.3.0', '~1.2.3', False),
        ('1.2.0', '~1.2', True), ('1.3.0', '~1.2', False),
        ('1.9.9', '~1', True), ('2.0.0', '~1', False),
        # x-ranges
        ('1.2.7', '1.2.x', True), ('1.3.0', '1.2.x', False),
        ('1.9.9', '1', True), ('2.0.0', '1', False),
        ('5.0.0', '*', True),
        # explicit comparators, conjunction and disjunction
        ('1.5.0', '>=1.0.0 <2.0.0', True), ('2.0.0', '>=1.0.0 <2.0.0', False),
        ('2.5.0', '^1.0.0 || ^2.0.0', True), ('3.0.0', '^1.0.0 || ^2.0.0', False),
        # hyphen ranges
        ('1.5.0', '1.2.3 - 2.3.4', True), ('2.3.5', '1.2.3 - 2.3.4', False),
        ('2.3.9', '1.2.3 - 2.3', True), ('2.4.0', '1.2.3 - 2.3', False),
        # whitespace between operator and version
        ('1.5.0', '>= 1.2.3', True), ('1.0.0', '>= 1.2.3', False),
        ('1.5.0', '  ^1.0.0  ', True),
        # ~> alias
        ('1.2.9', '~>1.2.3', True), ('1.3.0', '~>1.2.3', False),
    ])
    def test_satisfies(self, version, range_text, expected):
        assert sv.satisfies(version, range_text) is expected

    def test_operator_space_does_not_split_into_two_atoms(self):
        """
        Regression: splitting on whitespace before binding operators turned
        '>= 1.2.3' into 'anything AND exactly 1.2.3', so only 1.2.3 matched.
        """
        assert sv.max_satisfying(['1.2.3', '2.0.0', '10.0.0'], '>= 1.2.3') == '10.0.0'


class TestPrereleaseRestriction:
    """
    npm's rule: a prerelease may only satisfy a comparator set when some
    comparator pins the same major.minor.patch and itself names a prerelease.
    """

    @pytest.mark.parametrize('version,range_text,expected', [
        ('2.0.0-beta.1', '^1.0.0', False),
        ('1.2.3-beta.1', '^1.2.3', False),
        ('1.2.3-beta.2', '^1.2.3-beta.1', True),
        ('1.2.4-beta.1', '^1.2.3-beta.1', False),
        ('1.2.3-alpha', '>=1.2.3-alpha', True),
    ])
    def test_restriction(self, version, range_text, expected):
        assert sv.satisfies(version, range_text) is expected

    def test_upper_bound_excludes_next_major_prereleases(self):
        """
        npm expands '^1.2.3' to '>=1.2.3 <2.0.0-0', not '<2.0.0'. Without the
        sentinel a bare '<2.0.0' would admit 2.0.0-beta.1.
        """
        assert not sv.satisfies('2.0.0-beta.1', '^1.2.3', include_prerelease=True)

    def test_include_prerelease_widens_within_the_range(self):
        assert sv.satisfies('1.2.4-beta.1', '^1.2.3-beta.1', include_prerelease=True)

    def test_max_satisfying_skips_prereleases_by_default(self):
        versions = ['1.0.0', '1.1.0', '2.0.0-beta.1']
        assert sv.max_satisfying(versions, '*') == '1.1.0'


class TestIsRange:

    @pytest.mark.parametrize('text', ['^1.0.0', '~1.2', '*', '', '1.2.3',
                                      '>=1.0.0 <2.0.0', '1 - 2', 'x'])
    def test_accepts_ranges(self, text):
        assert sv.is_range(text)

    @pytest.mark.parametrize('text', [
        'latest', 'next', 'git+https://github.com/u/r.git', 'file:../local',
        'workspace:*', 'npm:other@^1.0.0', 'https://example.com/p.tgz', None,
    ])
    def test_rejects_non_ranges(self, text):
        """These are specifiers, not ranges; the caller must handle them."""
        assert not sv.is_range(text)


class TestMaxSatisfying:

    def test_picks_the_highest_match(self):
        versions = ['1.0.0', '1.2.3', '1.9.9', '2.0.0']
        assert sv.max_satisfying(versions, '^1.0.0') == '1.9.9'

    def test_returns_none_when_nothing_matches(self):
        assert sv.max_satisfying(['1.0.0', '1.2.3'], '^3.0.0') is None

    def test_ignores_unparseable_versions(self):
        assert sv.max_satisfying(['1.0.0', 'garbage', '1.2.0'], '^1.0.0') == '1.2.0'

    def test_preserves_the_original_spelling(self):
        assert sv.max_satisfying(['v1.0.0'], '^1.0.0') == 'v1.0.0'

    def test_empty_input(self):
        assert sv.max_satisfying([], '^1.0.0') is None

    def test_invalid_range(self):
        assert sv.max_satisfying(['1.0.0'], 'latest') is None


class TestGoldenSynthetic:
    """
    Differential comparison against npm's semver over the synthetic matrix.

    These are the assertions that actually establish parity; the readable tests
    above document intent.
    """

    def test_valid(self):
        golden = GOLDEN['synthetic']['valid']
        for version, expected in golden.items():
            assert (sv.parse_version(version) is not None) is expected, version

    def test_valid_range(self):
        golden = GOLDEN['synthetic']['validRange']
        for range_text, expected in golden.items():
            assert sv.is_range(range_text) is expected, range_text

    def test_compare(self):
        golden = GOLDEN['synthetic']['compare']
        checked = 0
        for versions in GOLDEN['synthetic']['versions']:
            for other in GOLDEN['synthetic']['versions']:
                expected = golden.get(_key(versions, other))
                if expected is None:
                    continue
                left, right = sv.parse_version(versions), sv.parse_version(other)
                if left is None or right is None:
                    continue
                assert sv.compare(left, right) == expected, (versions, other)
                checked += 1
        assert checked > 800, 'fixture did not cover the comparison matrix'

    def test_satisfies(self):
        golden = GOLDEN['synthetic']['satisfies']
        checked = 0
        for range_text in GOLDEN['synthetic']['ranges']:
            for include_prerelease in (False, True):
                for version in GOLDEN['synthetic']['versions']:
                    expected = golden.get(
                        _key(version, range_text, include_prerelease))
                    if expected is None:
                        continue
                    actual = sv.satisfies(
                        version, range_text,
                        include_prerelease=include_prerelease)
                    assert actual is expected, \
                        '%r vs %r (includePrerelease=%s)' % (
                            version, range_text, include_prerelease)
                    checked += 1
        assert checked > 3000, 'fixture did not cover the satisfies matrix'

    def test_max_satisfying(self):
        golden = GOLDEN['synthetic']['maxSatisfying']
        versions = GOLDEN['synthetic']['versions']
        for range_text in GOLDEN['synthetic']['ranges']:
            for include_prerelease in (False, True):
                expected = golden.get(_key(range_text, include_prerelease))
                actual = sv.max_satisfying(
                    versions, range_text, include_prerelease=include_prerelease)
                assert actual == expected, \
                    '%r (includePrerelease=%s)' % (range_text, include_prerelease)


class TestGoldenRealRegistryData:
    """
    Differential comparison over real dependency ranges and real published
    version lists, which exercise syntaxes a hand-written corpus would miss.
    """

    def test_range_validity(self):
        for case in GOLDEN['real']:
            assert sv.is_range(case['range']) is case['valid'], case['range']

    def test_max_satisfying(self):
        for case in GOLDEN['real']:
            if not case['valid']:
                continue
            actual = sv.max_satisfying(case['versions'], case['range'])
            assert actual == case['expected'], \
                '%s %r over %d versions' % (
                    case['dep'], case['range'], len(case['versions']))

    def test_fixture_is_substantial(self):
        """Guard against the fixture being trimmed into uselessness."""
        cases = GOLDEN['real']
        assert len(cases) >= 100
        assert len({c['range'] for c in cases}) >= 100
        with_prerelease = [c for c in cases
                           if any('-' in v for v in c['versions'])]
        assert len(with_prerelease) >= 20, \
            'fixture should exercise prerelease-bearing version lists'


class TestLiveDifferentialAgainstNpm:
    """
    Re-run the comparison against npm's semver when it is installed, so that a
    change in npm's behaviour surfaces instead of being masked by the fixture.
    """

    @staticmethod
    def _semver_dir():
        try:
            root = subprocess.run(['npm', 'root', '-g'], capture_output=True,
                                  text=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if root.returncode != 0:
            return None
        candidate = os.path.join(root.stdout.strip(), 'npm', 'node_modules', 'semver')
        return candidate if os.path.isdir(candidate) else None

    def test_matches_installed_npm_semver(self, tmp_path):
        semver_dir = self._semver_dir()
        if semver_dir is None:
            pytest.skip("npm's bundled semver not available")

        cases = [(c['range'], c['versions']) for c in GOLDEN['real'][:60]]
        script = tmp_path / 'check.js'
        script.write_text(
            'const semver = require(%s);\n'
            'const cases = JSON.parse(process.argv[2]);\n'
            'const out = cases.map(([r, vs]) => {\n'
            '  try { return semver.validRange(r) === null ? null'
            ' : semver.maxSatisfying(vs, r); } catch (e) { return null; }\n'
            '});\n'
            'process.stdout.write(JSON.stringify(out));\n'
            % json.dumps(semver_dir)
        )

        result = subprocess.run(
            ['node', str(script), json.dumps(cases)],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr

        reference = json.loads(result.stdout)
        for (range_text, versions), expected in zip(cases, reference):
            actual = sv.max_satisfying(versions, range_text)
            assert actual == expected, \
                'live divergence on %r: npm=%r ours=%r' % (
                    range_text, expected, actual)


def test_doctests():
    results = doctest.testmod(sv, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed
