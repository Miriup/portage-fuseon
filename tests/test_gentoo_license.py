"""
Tests for upstream-to-Gentoo license translation.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import doctest

import pytest

from portage_pip_fuse import gentoo_license as gl


class TestExactMappings:

    @pytest.mark.parametrize('name,expected', [
        ('MIT', 'MIT'),
        ('Apache-2.0', 'Apache-2.0'),
        ('BSD-2-Clause', 'BSD-2'),
        ('BSD-3-Clause', 'BSD'),
        ('GPL-2.0', 'GPL-2'),
        ('GPL-2.0-or-later', 'GPL-2+'),
        ('GPL-3.0-only', 'GPL-3'),
        ('LGPL-2.1-or-later', 'LGPL-2.1+'),
        ('LGPL-3.0', 'LGPL-3'),
        ('ISC', 'ISC'),
        ('MPL-2.0', 'MPL-2.0'),
        ('CC0-1.0', 'CC0-1.0'),
        ('Unlicense', 'Unlicense'),
        ('Python-2.0', 'PSF-2'),
        ('Ruby', 'Ruby'),
        ('MIT License', 'MIT'),
        ('Apache Software License', 'Apache-2.0'),
        ('Python Software Foundation License', 'PSF-2'),
    ])
    def test_translates(self, name, expected):
        assert gl.translate(name) == expected


class TestLgplIsNotGpl:
    """
    Regression tests for the headline defect.

    The old heuristics tested ``'gpl' in text`` before ``'lgpl' in text``.
    Because ``'lgpl'`` contains ``'gpl'``, every LGPL string not matched
    exactly by the table was emitted as GPL — the wrong license.
    """

    @pytest.mark.parametrize('name,expected', [
        ('LGPL-2.1+', 'LGPL-2.1+'),
        ('LGPL-3.0+', 'LGPL-3+'),
        ('lgpl-3', 'LGPL-3'),
        ('LGPL v3', 'LGPL-3'),
        ('LGPL v2.1 or later', 'LGPL-2.1+'),
        ('GNU Lesser General Public License v3 or later', 'LGPL-3+'),
        ('GNU Lesser General Public License v2.1', 'LGPL-2.1+'),
    ])
    def test_lgpl_stays_lgpl(self, name, expected):
        assert gl.translate(name) == expected

    @pytest.mark.parametrize('name', [
        'LGPL-2.1+', 'LGPL-3.0+', 'lgpl-3', 'LGPL v3', 'LGPL v2.1 or later',
    ])
    def test_never_reports_plain_gpl(self, name):
        assert not gl.translate(name).startswith('GPL')

    @pytest.mark.parametrize('name,expected', [
        ('GPL-2.0', 'GPL-2'),
        ('GPL v2 or later', 'GPL-2+'),
        ('GNU General Public License v3', 'GPL-3+'),
        ('GPL v3', 'GPL-3'),
    ])
    def test_real_gpl_still_resolves(self, name, expected):
        assert gl.translate(name) == expected


class TestHeuristics:

    @pytest.mark.parametrize('name,expected', [
        ('some weird mit license', 'MIT'),
        ('python software foundation', 'PSF-2'),
        ('the isc license', 'ISC'),
        ('mozilla public license', 'MPL-2.0'),
        ('cc0 waiver', 'CC0-1.0'),
    ])
    def test_free_text(self, name, expected):
        assert gl.translate(name) == expected

    @pytest.mark.parametrize('name', ['', '   ', 'Unknown License', 'Bogus'])
    def test_falls_back(self, name):
        assert gl.translate(name) == gl.UNKNOWN_LICENSE

    def test_fallback_is_a_real_gentoo_license(self):
        """'unknown' is not a Gentoo license; 'all-rights-reserved' is."""
        assert gl.UNKNOWN_LICENSE == 'all-rights-reserved'


class TestExpressions:

    @pytest.mark.parametrize('expr,expected', [
        ('MIT', 'MIT'),
        ('Apache-2.0 OR BSD-2-Clause', '|| ( Apache-2.0 BSD-2 )'),
        ('MIT AND Apache-2.0', 'MIT Apache-2.0'),
        ('MIT OR Apache-2.0 OR ISC', '|| ( MIT Apache-2.0 ISC )'),
    ])
    def test_translates(self, expr, expected):
        assert gl.translate_expression(expr) == expected

    @pytest.mark.parametrize('expr', [
        'MIT OR NotARealLicense', 'NotARealLicense AND MIT', 'NotARealLicense', '',
    ])
    def test_all_or_nothing(self, expr):
        """A partly-translated license statement is worse than falling back."""
        assert gl.translate_expression(expr) is None

    def test_expression_takes_precedence(self):
        assert gl.translate('GPL-3.0', 'MIT') == 'MIT'

    def test_untranslatable_expression_falls_through_to_name(self):
        assert gl.translate('MIT', 'NotARealLicense') == 'MIT'


class TestTranslateList:

    @pytest.mark.parametrize('licenses,expected', [
        (['MIT'], 'MIT'),
        (['MIT', 'Apache-2.0'], 'MIT Apache-2.0'),
        (['Ruby', 'BSD-2-Clause'], 'Ruby BSD-2'),
        (['MIT', 'MIT'], 'MIT'),
    ])
    def test_translates(self, licenses, expected):
        assert gl.translate_list(licenses) == expected

    @pytest.mark.parametrize('licenses', [[], ['Bogus'], ['Bogus', 'AlsoBogus']])
    def test_falls_back(self, licenses):
        assert gl.translate_list(licenses) == gl.UNKNOWN_LICENSE

    def test_drops_unrecognised_rather_than_passing_through(self):
        """
        RubyGems used to emit unknown names verbatim into LICENSE=, which fails
        at build time. Dropping them keeps the accurate ones usable.
        """
        assert gl.translate_list(['MIT', 'Bogus']) == 'MIT'


class TestCallersDelegate:
    """Both ecosystems must resolve through the shared tables."""

    def test_pypi_extractor(self):
        from portage_pip_fuse.pip_metadata import EbuildDataExtractor

        extractor = EbuildDataExtractor()
        assert extractor.translate_license('LGPL-2.1+') == 'LGPL-2.1+'
        assert extractor.translate_license('', 'Apache-2.0 OR BSD-2-Clause') == \
            '|| ( Apache-2.0 BSD-2 )'
        assert extractor.translate_license('') == gl.UNKNOWN_LICENSE

    def test_rubygems_generator(self):
        pytest.importorskip('fuse')
        from portage_pip_fuse.ecosystems.rubygems.plugin import RubyGemsEbuildGenerator

        generator = RubyGemsEbuildGenerator.__new__(RubyGemsEbuildGenerator)
        assert generator._translate_license(['MIT', 'Apache-2.0']) == 'MIT Apache-2.0'
        assert generator._translate_license([]) == gl.UNKNOWN_LICENSE
        assert generator._translate_license(['Bogus']) == gl.UNKNOWN_LICENSE


def test_doctests():
    results = doctest.testmod(gl, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed
