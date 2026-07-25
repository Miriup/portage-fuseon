"""
Tests for npm name and version translation.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import doctest
import itertools

import pytest

from portage_pip_fuse import pms_version
from portage_pip_fuse.ecosystems.npm import name_translator as nt
from portage_pip_fuse.ecosystems.npm import semver as sv
from portage_pip_fuse.ecosystems.npm import version_translator as vt


class TestVersionTranslation:

    @pytest.mark.parametrize('npm,expected', [
        ('1.2.3', '1.2.3'),
        ('0.0.1', '0.0.1'),
        ('10.20.30', '10.20.30'),
        ('1.2.3-alpha', '1.2.3_alpha'),
        ('1.2.3-beta.1', '1.2.3_beta1'),
        ('1.2.3-beta1', '1.2.3_beta1'),
        ('2.0.0-rc.0', '2.0.0_rc0'),
        ('1.0.0-pre.5', '1.0.0_pre5'),
        ('1.2.3+build.5', '1.2.3'),
        ('1.2.3-beta.1+build', '1.2.3_beta1'),
        ('1.2.3-BETA.1', '1.2.3_beta1'),
    ])
    def test_translates(self, npm, expected):
        assert vt.translate_version(npm) == expected

    @pytest.mark.parametrize('npm', [
        '1.0.0-0', '1.0.0-1',            # bare numeric: no order-preserving form
        '1.2.3-next.5', '1.2.3-canary.3', '1.2.3-nightly.1',
        '1.2.3-security', '1.2.3-esm', '1.2.3-dev',
        '1.2.3-beta.1.2',                # three identifiers
        '12.1.1-alpha.2935e14d',         # commit-hash prerelease
        '23.0.0-alpha.3r',               # non-numeric trailer
        '3.0.0-alpha-1',                 # hyphen spelling orders differently
        '1.2', '1', 'not-a-version', '', None,
    ])
    def test_rejects(self, npm):
        assert vt.translate_version(npm) is None

    def test_every_translation_is_valid_pms(self):
        samples = ['1.2.3', '1.2.3-alpha', '1.2.3-beta.1', '2.0.0-rc.0',
                   '1.0.0-pre.5', '1.2.3-beta1', '0.0.1']
        for npm in samples:
            translated = vt.translate_version(npm)
            assert pms_version.is_valid(translated), (npm, translated)

    def test_bare_numeric_prerelease_would_invert_ordering(self):
        """
        '1.0.0-0' sorts below 1.0.0, but the only PMS suffix for a bare number
        is _p, which sorts above. Rejecting is the only order-safe option.
        """
        assert sv.parse_version('1.0.0-0') < sv.parse_version('1.0.0')
        assert vt.translate_version('1.0.0-0') is None

    def test_hyphen_spelling_rejected_because_npm_orders_it_lexically(self):
        """npm reads 'alpha-1' as one alphanumeric identifier, so it outranks
        'alpha.6'. Folding both to _alpha1/_alpha6 would invert that."""
        assert sv.parse_version('3.0.0-alpha-1') > sv.parse_version('3.0.0-alpha.6')
        assert vt.translate_version('3.0.0-alpha-1') is None

    def test_prerelease_sorts_below_release(self):
        assert vt.pms_sort_key('1.2.3_rc1') < vt.pms_sort_key('1.2.3')


class TestUntranslateVersion:

    @pytest.mark.parametrize('pms,expected', [
        ('1.2.3', '1.2.3'),
        ('1.2.3_alpha', '1.2.3-alpha'),
        ('1.2.3_beta1', '1.2.3-beta.1'),
        ('2.0.0_rc0', '2.0.0-rc.0'),
    ])
    def test_untranslates(self, pms, expected):
        assert vt.untranslate_version(pms) == expected

    @pytest.mark.parametrize('pms', ['not-a-version', '', '1.2', '1.2.3_foo'])
    def test_rejects(self, pms):
        assert vt.untranslate_version(pms) is None

    def test_round_trips_for_dot_separated_spelling(self):
        for npm in ['1.2.3', '1.2.3-alpha', '1.2.3-beta.1', '2.0.0-rc.0',
                    '1.0.0-pre.5']:
            assert vt.untranslate_version(vt.translate_version(npm)) == npm

    def test_is_not_injective_and_says_so(self):
        """Both npm spellings collapse to one PMS version, so the reverse is a
        hint the caller must confirm against the registry."""
        assert vt.translate_version('1.2.3-beta.1') == \
            vt.translate_version('1.2.3-beta1')
        assert vt.untranslate_version('1.2.3_beta1') == '1.2.3-beta.1'


class TestPmsSortKey:

    def test_orders_suffixes_per_pms(self):
        order = ['1.0.0_alpha', '1.0.0_beta', '1.0.0_pre', '1.0.0_rc',
                 '1.0.0', '1.0.0_p1']
        keys = [vt.pms_sort_key(v) for v in order]
        assert keys == sorted(keys), 'PMS suffix ranking is wrong'

    def test_numeric_suffix_ordering(self):
        assert vt.pms_sort_key('1.0.0_beta2') > vt.pms_sort_key('1.0.0_beta1')
        assert vt.pms_sort_key('1.0.0_beta10') > vt.pms_sort_key('1.0.0_beta9')

    def test_rejects_unrecognised(self):
        assert vt.pms_sort_key('nonsense') is None
        assert vt.pms_sort_key('') is None


class TestSelectOrderPreserving:
    """
    Ordering is a property of the version *set*, not of a single version, so it
    cannot be enforced by the per-version translator.
    """

    def test_plain_versions_are_all_kept_newest_first(self):
        assert vt.select_order_preserving(['1.0.0', '2.0.0', '1.1.0']) == \
            ['2.0.0', '1.1.0', '1.0.0']

    def test_untranslatable_versions_are_dropped(self):
        assert vt.select_order_preserving(['1.0.0', '1.1.0-next.5']) == ['1.0.0']

    @pytest.mark.parametrize('versions', [
        ['0.9.0-beta8', '0.9.0-beta25'],      # inline numbers compare lexically
        ['1.0.0-rc7', '1.0.0-rc11'],
        ['5.0.0-beta3', '5.0.0-beta.15'],     # mixed spellings
        ['0.10.0-beta5', '0.10.0-beta16'],
    ])
    def test_conflicting_spellings_are_trimmed(self, versions):
        kept = vt.select_order_preserving(versions)
        assert len(kept) == 1, 'one of the pair must be dropped'
        # The npm-newer one survives.
        newest = max(versions, key=lambda v: sv.parse_version(v))
        assert kept == [newest]

    def test_kept_sets_never_disagree_with_npm_ordering(self):
        """The invariant the whole helper exists to guarantee."""
        corpus = [
            ['1.0.0', '1.1.0', '2.0.0', '2.0.1'],
            ['0.9.0-beta8', '0.9.0-beta25', '0.9.0'],
            ['5.0.0-beta3', '5.0.0-beta.15', '5.0.0-rc.1', '5.0.0'],
            ['1.0.0-alpha', '1.0.0-beta.1', '1.0.0-rc.1', '1.0.0'],
            ['1.0.0-next.1', '1.0.0', '1.0.1'],
            ['1.0.0-0', '1.0.0'],
        ]
        for versions in corpus:
            kept = vt.select_order_preserving(versions)
            for left, right in itertools.combinations(kept, 2):
                npm_cmp = sv.compare(sv.parse_version(left), sv.parse_version(right))
                pms_cmp_keys = (vt.pms_sort_key(vt.translate_version(left)),
                                vt.pms_sort_key(vt.translate_version(right)))
                pms_cmp = (pms_cmp_keys[0] > pms_cmp_keys[1]) \
                    - (pms_cmp_keys[0] < pms_cmp_keys[1])
                assert npm_cmp == pms_cmp, \
                    'order inverted between %r and %r' % (left, right)

    def test_no_two_kept_versions_share_a_pms_filename(self):
        versions = ['1.0.0-beta.1', '1.0.0-beta1', '1.0.0-rc.1', '1.0.0-rc1',
                    '1.0.0']
        kept = vt.select_order_preserving(versions)
        translated = [vt.translate_version(v) for v in kept]
        assert len(translated) == len(set(translated))

    def test_empty_input(self):
        assert vt.select_order_preserving([]) == []


class TestNameTranslation:

    @pytest.mark.parametrize('npm,expected', [
        ('chalk', 'chalk'),
        ('vue-cli-service', 'vue-cli-service'),
        ('express', 'express'),
        ('socket.io', 'socket_io'),
        ('lodash.merge', 'lodash_merge'),
        ('http-2', 'http_2'),
        ('http2', 'http2'),
        ('@vue/cli-service', 'vue+cli-service'),
        ('@babel/core', 'babel+core'),
        ('@types/node', 'types+node'),
        ('@eslint-community/regexpp', 'eslint-community+regexpp'),
    ])
    def test_translates(self, npm, expected):
        assert nt.npm_to_gentoo(npm) == expected

    @pytest.mark.parametrize('npm', ['', 'has space', '@noslash', None, 'x' * 300])
    def test_rejects_invalid(self, npm):
        assert nt.npm_to_gentoo(npm) is None

    def test_every_result_is_a_valid_gentoo_name(self):
        samples = ['chalk', '@vue/cli-service', 'socket.io', 'http-2',
                   'lodash.merge', '@types/node', '3d-view', 'a']
        for npm in samples:
            translated = nt.npm_to_gentoo(npm)
            assert translated is not None, npm
            assert nt.is_valid_gentoo_name(translated), (npm, translated)


class TestScopeDoesNotAliasFlatPackages:
    """
    The reason the scope separator is '+' and not '-'.

    npm scoping postdates the flat namespace, so a scoped package's flattened
    name is almost always a different, really-published package. Every pair
    below exists on the registry, and '@babel/core' vs 'babel-core' are
    unrelated release lines.
    """

    @pytest.mark.parametrize('scoped,flat', [
        ('@vue/cli-service', 'vue-cli-service'),
        ('@babel/core', 'babel-core'),
        ('@types/node', 'types-node'),
        ('@angular/cli', 'angular-cli'),
        ('@eslint/js', 'eslint-js'),
        ('@jest/core', 'jest-core'),
    ])
    def test_scoped_and_flat_stay_distinct(self, scoped, flat):
        assert nt.npm_to_gentoo(scoped) != nt.npm_to_gentoo(flat)

    def test_scope_separator_cannot_occur_in_an_npm_name(self):
        """Which is what makes the mapping injective and reversible."""
        assert not nt.is_valid_npm_name('vue+cli-service')

    def test_scope_round_trip_is_exact(self):
        for npm in ['@vue/cli-service', '@babel/core', '@types/node',
                    '@eslint-community/eslint-utils']:
            assert nt.gentoo_to_npm(nt.npm_to_gentoo(npm)) == npm

    def test_separator_matches_the_eclass_store_spelling(self):
        """npm.eclass writes the store path as '@vue+cli-service'."""
        assert nt.SCOPE_SEPARATOR == '+'


class TestGentooToNpm:

    @pytest.mark.parametrize('gentoo,expected', [
        ('chalk', 'chalk'),
        ('vue+cli-service', '@vue/cli-service'),
        ('babel+core', '@babel/core'),
        ('socket_io', 'socket_io'),
    ])
    def test_untranslates(self, gentoo, expected):
        assert nt.gentoo_to_npm(gentoo) == expected

    @pytest.mark.parametrize('gentoo', ['', 'socket.io', '@vue/cli', 'http-2'])
    def test_rejects_invalid(self, gentoo):
        assert nt.gentoo_to_npm(gentoo) is None

    def test_underscore_is_a_guess_without_a_registry(self):
        """An '_' may be original or may stand for a '.'; bare reversal guesses."""
        assert nt.gentoo_to_npm('socket_io') == 'socket_io'
        translator = nt.NpmNameTranslator()
        translator.npm_to_gentoo('socket.io')
        assert translator.gentoo_to_npm('socket_io') == 'socket.io'


class TestValidation:

    @pytest.mark.parametrize('name', ['chalk', '@vue/cli-service', 'socket.io',
                                      'lodash.merge', 'a', '3d-view', 'http-2'])
    def test_valid_npm_names(self, name):
        assert nt.is_valid_npm_name(name)

    @pytest.mark.parametrize('name', ['', 'has space', '@noslash', '.leading',
                                      'x' * 300, None])
    def test_invalid_npm_names(self, name):
        assert not nt.is_valid_npm_name(name)

    @pytest.mark.parametrize('name', ['chalk', 'vue+cli-service', 'socket_io',
                                      'http_2', 'a', '3d-view'])
    def test_valid_gentoo_names(self, name):
        assert nt.is_valid_gentoo_name(name)

    @pytest.mark.parametrize('name', ['', 'socket.io', '@vue/cli',
                                      '-leading-hyphen', 'http-2', 'pkg-123'])
    def test_invalid_gentoo_names(self, name):
        assert not nt.is_valid_gentoo_name(name)

    def test_trailing_digits_are_rejected_because_they_look_like_versions(self):
        assert not nt.is_valid_gentoo_name('http-2')
        assert nt.npm_to_gentoo('http-2') == 'http_2'


class TestFindCollision:

    @pytest.mark.parametrize('name,expected', [
        ('socket.io', 'socket_io'),
        ('socket_io', 'socket.io'),
        ('lodash.merge', 'lodash_merge'),
    ])
    def test_reports_the_colliding_name(self, name, expected):
        assert nt.find_collision(name) == expected

    @pytest.mark.parametrize('name', ['chalk', 'express', '@vue/cli-service'])
    def test_reports_none_when_safe(self, name):
        assert nt.find_collision(name) is None

    def test_collision_pairs_really_translate_alike(self):
        """socket.io and socket_io both exist on the registry."""
        other = nt.find_collision('socket.io')
        assert nt.npm_to_gentoo('socket.io') == nt.npm_to_gentoo(other)


class TestNpmNameTranslator:

    def test_registers_reverse_mappings(self):
        translator = nt.NpmNameTranslator()
        assert translator.npm_to_gentoo('socket.io') == 'socket_io'
        assert translator.gentoo_to_npm('socket_io') == 'socket.io'

    def test_overrides_take_precedence(self):
        translator = nt.NpmNameTranslator(overrides={'chalk': 'my-chalk'})
        assert translator.npm_to_gentoo('chalk') == 'my-chalk'
        assert translator.gentoo_to_npm('my-chalk') == 'chalk'

    def test_untransformed_names_are_not_registered(self):
        translator = nt.NpmNameTranslator()
        translator.npm_to_gentoo('chalk')
        assert translator.known_mappings() == []

    def test_known_mappings_lists_rewrites(self):
        translator = nt.NpmNameTranslator()
        translator.npm_to_gentoo('socket.io')
        translator.npm_to_gentoo('@vue/cli-service')
        assert translator.known_mappings() == ['@vue/cli-service', 'socket.io']


class TestRealWorldNames:
    """Names taken from real dependency declarations of popular packages."""

    REAL = [
        ('@babel/helper-compilation-targets', 'babel+helper-compilation-targets'),
        ('@eslint-community/eslint-utils', 'eslint-community+eslint-utils'),
        ('@eslint/config-array', 'eslint+config-array'),
        ('@jridgewell/gen-mapping', 'jridgewell+gen-mapping'),
        ('supports-color', 'supports-color'),
        ('ansi-styles', 'ansi-styles'),
        ('is-glob', 'is-glob'),
        ('safer-buffer', 'safer-buffer'),
    ]

    @pytest.mark.parametrize('npm,expected', REAL)
    def test_translates(self, npm, expected):
        assert nt.npm_to_gentoo(npm) == expected

    def test_all_distinct(self):
        translated = [nt.npm_to_gentoo(npm) for npm, _ in self.REAL]
        assert len(set(translated)) == len(translated)


def test_version_translator_doctests():
    results = doctest.testmod(vt, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed


def test_name_translator_doctests():
    results = doctest.testmod(nt, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed
