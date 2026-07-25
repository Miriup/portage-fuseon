"""
Tests for PEP 440 to Gentoo PMS version translation.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import doctest

import pytest

from portage_pip_fuse import pms_version
from portage_pip_fuse import pypi_version as pypiv


#: Cases the three former PyPI translators all agreed on. Kept as an explicit
#: table so the consolidated implementation cannot drift from them.
TRANSLATABLE = [
    ('1.2.3', '1.2.3'),
    ('1.0', '1.0'),
    ('0.1', '0.1'),
    ('2024.1.1', '2024.1.1'),
    ('1.2.3.4', '1.2.3.4'),
    ('2.0a0', '2.0_alpha0'),
    ('1.0b1', '1.0_beta1'),
    ('3.0rc1', '3.0_rc1'),
    ('1.0c1', '1.0_rc1'),
    ('1.0.post1', '1.0_p1'),
    ('1.0.dev1', '1.0_pre1'),
    ('1.0.alpha1', '1.0_alpha1'),
    ('1.0.beta2', '1.0_beta2'),
    ('21.12b0', '21.12_beta0'),
    ('1.0a1.post2', '1.0_alpha1_p2'),
    ('1.0b2.dev3', '1.0_beta2_pre3'),
    ('1.0.0rc1.post1', '1.0.0_rc1_p1'),
    ('1.0.0.dev20240101', '1.0.0_pre20240101'),
]

#: Versions PEP 440 permits but PMS cannot express. Before consolidation the
#: pip_metadata and cli translators passed these through unvalidated, so they
#: reached ebuild names and dependency atoms in unparseable form.
UNTRANSLATABLE = [
    '1.0+local.build',
    '1.0+ubuntu1',
    '2024.rubbish',
    '1!2.0',
    'v1.0',
    'not-a-version',
    '',
]


class TestTranslatePep440:

    @pytest.mark.parametrize('upstream,expected', TRANSLATABLE)
    def test_translates(self, upstream, expected):
        assert pypiv.translate_pep440(upstream) == expected

    @pytest.mark.parametrize('upstream', UNTRANSLATABLE)
    def test_rejects_unrepresentable(self, upstream):
        assert pypiv.translate_pep440(upstream) is None

    @pytest.mark.parametrize('upstream,expected', TRANSLATABLE)
    def test_output_is_valid_pms(self, upstream, expected):
        assert pms_version.is_valid(pypiv.translate_pep440(upstream))

    def test_unvalidated_mode_returns_raw_rewrite(self):
        """validate=False exposes the rewrite for diagnostics, without a gate."""
        assert pypiv.translate_pep440('1.0+local.build', validate=False) == \
            '1.0+local.build'
        assert pypiv.translate_pep440('2.0a0', validate=False) == '2.0_alpha0'

    def test_rc_is_matched_before_bare_c(self):
        """Rewrite order matters: a bare 'c' rule must not eat 'rc'."""
        assert pypiv.translate_pep440('3.0rc1') == '3.0_rc1'
        assert pypiv.translate_pep440('1.0c1') == '1.0_rc1'

    def test_long_markers_matched_before_short(self):
        """'alpha'/'beta' must be consumed before the single-letter forms."""
        assert pypiv.translate_pep440('1.0.alpha1') == '1.0_alpha1'
        assert pypiv.translate_pep440('1.0.beta2') == '1.0_beta2'


class TestCanTranslatePep440:

    @pytest.mark.parametrize('upstream,_expected', TRANSLATABLE)
    def test_accepts_translatable(self, upstream, _expected):
        assert pypiv.can_translate_pep440(upstream)

    @pytest.mark.parametrize('upstream', UNTRANSLATABLE)
    def test_rejects_untranslatable(self, upstream):
        assert not pypiv.can_translate_pep440(upstream)

    def test_agrees_with_translator(self):
        for upstream in [u for u, _ in TRANSLATABLE] + UNTRANSLATABLE:
            assert pypiv.can_translate_pep440(upstream) == \
                (pypiv.translate_pep440(upstream) is not None)


class TestCallersHandleUntranslatableVersions:
    """
    The consolidation changed these callers from emitting a broken atom to
    degrading gracefully. Pin that behaviour.
    """

    def test_dependency_specifier_is_skipped_not_mangled(self):
        packaging = pytest.importorskip('packaging.requirements')
        from portage_pip_fuse.pip_metadata import EbuildDataExtractor

        extractor = EbuildDataExtractor()
        # A local version label; packaging only permits these with == or !=.
        req = packaging.Requirement('example==1.0+local.build')
        result = extractor._format_gentoo_dependency('dev-python/example',
                                                     req.specifier)
        # Falls back to the unversioned atom rather than emitting an empty
        # string or an atom portage cannot parse.
        assert result == 'dev-python/example'

    def test_epoch_specifier_is_skipped_not_mangled(self):
        packaging = pytest.importorskip('packaging.requirements')
        from portage_pip_fuse.pip_metadata import EbuildDataExtractor

        extractor = EbuildDataExtractor()
        # PMS has no epoch concept, so '1!2.0' has no representation.
        req = packaging.Requirement('example>=1!2.0')
        result = extractor._format_gentoo_dependency('dev-python/example',
                                                     req.specifier)
        assert result == 'dev-python/example'

    def test_mixed_specifiers_keep_the_translatable_ones(self):
        packaging = pytest.importorskip('packaging.requirements')
        from portage_pip_fuse.pip_metadata import EbuildDataExtractor

        extractor = EbuildDataExtractor()
        req = packaging.Requirement('example>=1.0,!=1.5+local')
        result = extractor._format_gentoo_dependency('dev-python/example',
                                                     req.specifier)
        assert '>=dev-python/example-1.0' in result
        assert 'local' not in result

    def test_wildcard_specifier_still_works(self):
        packaging = pytest.importorskip('packaging.requirements')
        from portage_pip_fuse.pip_metadata import EbuildDataExtractor

        extractor = EbuildDataExtractor()
        req = packaging.Requirement('example==23.*')
        result = extractor._format_gentoo_dependency('dev-python/example',
                                                     req.specifier)
        assert result == '=dev-python/example-23*'


def test_doctests():
    results = doctest.testmod(pypiv, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed
