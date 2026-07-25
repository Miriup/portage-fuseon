"""
Tests for the canonical PMS version module.

The parity tests here are the safety net for the version-translator
consolidation: they assert that ``pms_version.translate_dotted`` reproduces the
behaviour of the RubyGems tokenizer it replaced, byte for byte, so the refactor
cannot silently change generated ebuild versions.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import doctest
import itertools
import json
import pathlib
import re

import pytest

from portage_pip_fuse import pms_version as pv


def _corpus():
    """Build a broad corpus of upstream-shaped version strings."""
    bases = ['1', '1.0', '1.2.3', '10.20.30', '0.0.1', '2.0.0.0']
    suffixes = [
        '', '.alpha', '.beta', '.pre', '.rc', '.alpha1', '.beta2', '.pre4',
        '.rc10', '.a', '.b', '.a1', '.b2', '.1', '.2.3', '.alpha.pre.4',
        '.beta1.1', '.alpha.pre4', '.racecar1', '.RELEASE', '.p1', '.p',
        '.ALPHA1', '.dev1', '.final', '.rc.1', '.1.alpha', '.alpha.beta.rc',
    ]
    extra = ['not-a-version', '', 'v1.0.0', '1.0.0-beta.1', '1.0.0+build',
             '1.0.0_p1']
    return [b + s for b, s in itertools.product(bases, suffixes)] + extra


CORPUS = _corpus()


def _canonical_upstream(version):
    """Apply the two canonicalizations translation performs, for round-trip use.

    Translation is exact except that it case-folds suffix components and
    expands single-letter shorthands, so ``1.ALPHA1`` and ``5.a`` come back as
    ``1.alpha1`` and ``5.alpha``. Expansion is component-wise so the ``a``
    inside ``alpha`` is left alone and ``a1`` becomes ``alpha1``.
    """
    parts = version.split('.')
    out = [parts[0]]
    for part in parts[1:]:
        part = part.lower()
        match = re.match(r'^([a-z])(\d*)$', part)
        if match and match.group(1) in pv.DEFAULT_SHORTHAND_MAP:
            out.append(pv.DEFAULT_SHORTHAND_MAP[match.group(1)] + match.group(2))
        else:
            out.append(part)
    return '.'.join(out)


class TestIsValid:
    """PMS grammar validation."""

    @pytest.mark.parametrize('version', [
        '1', '1.2', '1.2.3', '1.2.3a', '2.0_alpha', '2.0_alpha1',
        '1.0_beta2_p1', '1.0_pre', '1.0_rc3', '1.2.3-r2', '1.2.3a_p1-r1',
    ])
    def test_accepts_valid(self, version):
        assert pv.is_valid(version)

    @pytest.mark.parametrize('version', [
        '', 'abc', 'v1.0', '1.0.0-beta.1', '1.0.0.RELEASE', '1.0+build',
        '1.0_foo', '1.0-r', '.1.0', '1.0.',
    ])
    def test_rejects_invalid(self, version):
        assert not pv.is_valid(version)


class TestTranslateDotted:
    """Dotted-suffix dialect translation."""

    @pytest.mark.parametrize('upstream,expected', [
        ('1.0.0', '1.0.0'),
        ('2.0.0.alpha1', '2.0.0_alpha1'),
        ('3.0.0.beta2', '3.0.0_beta2'),
        ('4.0.0.rc1', '4.0.0_rc1'),
        ('5.0.0.pre', '5.0.0_pre'),
        ('2.0.0.alpha.pre.4', '2.0.0_alpha_pre_p4'),
        ('5.0.0.beta1.1', '5.0.0_beta1_p1'),
        ('2.0.0.alpha.pre4', '2.0.0_alpha_pre4'),
        ('5.a', '5_alpha'),
        ('5.b', '5_beta'),
        ('5.a1', '5_alpha1'),
    ])
    def test_translates(self, upstream, expected):
        assert pv.translate_dotted(upstream) == expected

    @pytest.mark.parametrize('upstream', [
        '5.0.0.racecar1', '1.0.0.RELEASE', 'not-a-version', '',
        '1.0.0-beta.1', 'v1.0.0',
    ])
    def test_rejects_untranslatable(self, upstream):
        assert pv.translate_dotted(upstream) is None

    def test_shorthand_can_be_disabled(self):
        assert pv.translate_dotted('5.a', shorthand_map={}) is None
        assert pv.translate_dotted('5.a1', shorthand_map={}) is None

    def test_every_translation_is_valid_pms(self):
        """A successful translation must always satisfy the PMS grammar."""
        for version in CORPUS:
            translated = pv.translate_dotted(version)
            if translated is not None:
                assert pv.is_valid(translated), \
                    '%r -> %r is not valid PMS' % (version, translated)


class TestRoundTrip:
    """translate_dotted and untranslate_dotted must be inverses."""

    @pytest.mark.parametrize('upstream', [
        '1.0.0', '2.0.0.alpha1', '3.0.0.beta2', '4.0.0.rc1', '5.0.0.pre',
        '2.0.0.alpha.pre.4', '5.0.0.beta1.1', '2.0.0.alpha.pre4',
    ])
    def test_round_trips(self, upstream):
        assert pv.untranslate_dotted(pv.translate_dotted(upstream)) == upstream

    def test_round_trips_across_corpus(self):
        """Round-trip holds for every translatable version except shorthand.

        Translation canonicalizes in two ways, so the round-trip is exact only
        up to those: single-letter shorthands expand (``5.a`` and ``5.alpha``
        both mean ``5_alpha``) and suffix components case-fold.
        """
        for version in CORPUS:
            translated = pv.translate_dotted(version)
            if translated is None:
                continue
            back = pv.untranslate_dotted(translated)
            assert back == _canonical_upstream(version), \
                '%r -> %r -> %r' % (version, translated, back)


class TestCanTranslateDotted:
    """The predicate must agree with the translator by construction."""

    def test_agrees_with_translator_across_corpus(self):
        for version in CORPUS:
            assert pv.can_translate_dotted(version) == \
                (pv.translate_dotted(version) is not None)

    @pytest.mark.parametrize('version,expected', [
        ('1.0.0', True), ('2.0.0.alpha1', True), ('2.0.0.alpha.pre.4', True),
        ('5.0.0.beta1.1', True), ('5.a', True),
        ('5.0.0.racecar1', False), ('1.0.0.RELEASE', False),
    ])
    def test_known_cases(self, version, expected):
        assert pv.can_translate_dotted(version) is expected

    @pytest.mark.parametrize('version', [
        '1.p1', '1.0.p', '1.2.3.p1', '10.20.30.p',
    ])
    def test_rejects_literal_p_suffix(self, version):
        """A literal dotted ``.p`` component is not accepted.

        In the dotted dialect a bare numeric component already encodes a
        patchlevel, so also accepting ``.p1`` would make the transform
        ambiguous. The RubyGems ``GentooVersionFilter`` used to accept these
        while the translator rejected them, letting versions pass filtering and
        then fail translation; deriving the predicate from the translator closes
        that gap.
        """
        assert not pv.can_translate_dotted(version)


class TestNormalization:
    """Trailing-.0 helpers."""

    @pytest.mark.parametrize('version,expected', [
        ('1.33.0', '1.33'), ('2.0.0', '2.0'), ('1.33', '1.33'),
        ('1.0', '1.0'), ('1.0.0_alpha1', '1.0.0_alpha1'),
    ])
    def test_shortest(self, version, expected):
        assert pv.normalize_shortest(version) == expected

    @pytest.mark.parametrize('version,expected', [
        ('1.33', '1.33.0'), ('1.33.0', '1.33.0'), ('1.0.0_alpha1', '1.0.0_alpha1'),
    ])
    def test_longest(self, version, expected):
        assert pv.normalize_longest(version) == expected

    @pytest.mark.parametrize('version,expected', [
        ('1.33', '1.33.0'), ('1.33.0', '1.33'), ('2.0.0', '2.0'),
    ])
    def test_equivalent_form(self, version, expected):
        assert pv.equivalent_form(version) == expected

    def test_equivalent_form_skips_suffixed(self):
        assert pv.equivalent_form('1.0_alpha1') is None


class TestGoldenCorpus:
    """
    Regression guard against the pre-consolidation RubyGems implementations.

    ``tests/data/gem_version_golden.json`` was captured from the RubyGems
    tokenizer *before* it was replaced, so these assertions stay meaningful
    after the delegation — unlike comparing the two live implementations, which
    would now be comparing the shared module against itself.
    """

    @staticmethod
    def _golden():
        path = pathlib.Path(__file__).parent / 'data' / 'gem_version_golden.json'
        with path.open() as handle:
            return json.load(handle)

    def test_translate_matches_golden(self):
        golden = self._golden()['translate']
        for version, expected in golden.items():
            assert pv.translate_dotted(version) == expected, \
                'drift on %r: golden %r' % (version, expected)

    def test_untranslate_matches_golden(self):
        golden = self._golden()['untranslate']
        for gentoo_version, expected in golden.items():
            assert pv.untranslate_dotted(gentoo_version) == expected, \
                'drift on %r: golden %r' % (gentoo_version, expected)

    def test_golden_covers_the_corpus(self):
        """The fixture must not silently fall out of sync with the corpus."""
        golden = self._golden()['translate']
        missing = [v for v in CORPUS if v not in golden]
        assert not missing, 'corpus entries absent from golden data: %r' % missing


def test_doctests():
    """The module's doctests are part of its contract."""
    results = doctest.testmod(pv, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed
