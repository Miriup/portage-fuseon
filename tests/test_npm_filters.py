"""
Tests for npm version filters, KEYWORDS mapping and Node target detection.

The parametrised data here is taken from real registry metadata (26 packages,
16665 versions) rather than invented, so the filters are exercised against
shapes npm actually publishes -- including the ``engines`` array form that 44 of
those versions use.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import doctest
import os

import pytest

from portage_pip_fuse.ecosystems.npm import filters as F
from portage_pip_fuse.ecosystems.npm import node_targets, semver

NODE_22 = ['22.22.2']


class TestNodeCompatFilter:

    @pytest.mark.parametrize('requirement,expected', [
        # The eight most common engines.node values in the sampled registry data.
        ('>=0.10.0', True),
        ('>=4.2.0', True),
        ('^18.18.0 || ^20.9.0 || >=21.1.0', True),
        ('>=14.17', True),
        ('>=10', True),
        ('>=6.4.0', True),
        ('>=0.8.0', True),
        ('>=0.6', True),
        # Real ranges that genuinely exclude a modern Node.
        ('>= 0.2.0 < 0.4.0', False),
        ('0.4.x', False),
        ('>= 0.4.1 < 0.5.0', False),
        ('0.10.x', False),
        ('^12.0.0 || >= 14.0.0', True),
        ('>=99', False),
    ])
    def test_real_engines_values(self, requirement, expected):
        node_filter = F.NodeCompatFilter(node_versions=NODE_22)
        assert node_filter.should_include_version(
            'pkg', '1.0.0', {'engines': {'node': requirement}}) is expected

    def test_all_real_engines_values_are_parseable_ranges(self):
        """Every engines.node value in the sample parsed; guard that."""
        for requirement in ['>=0.10.0', '>=4.2.0', '>=14.17', '>=10',
                            '^18.18.0 || ^20.9.0 || >=21.1.0', '>= 0.2.0 < 0.4.0',
                            '0.4.x', '>=0.6', '^12.0.0 || >= 14.0.0']:
            assert semver.is_range(requirement), requirement

    @pytest.mark.parametrize('metadata', [
        {},
        {'engines': None},
        {'engines': {}},
        {'engines': {'npm': '>=8'}},
        {'engines': {'node': ''}},
    ])
    def test_missing_declaration_is_compatible(self, metadata):
        node_filter = F.NodeCompatFilter(node_versions=NODE_22)
        assert node_filter.should_include_version('pkg', '1.0.0', metadata)

    def test_engines_as_a_list_is_tolerated(self):
        """
        44 real versions in the sample declare engines as an array, e.g.
        ['node >=0.4']. It cannot be interpreted, so the version is included
        rather than hidden.
        """
        node_filter = F.NodeCompatFilter(node_versions=NODE_22)
        assert node_filter.should_include_version(
            'pkg', '1.0.0', {'engines': ['node >=0.4']})

    def test_unparseable_range_is_permissive(self):
        node_filter = F.NodeCompatFilter(node_versions=NODE_22)
        assert node_filter.should_include_version(
            'pkg', '1.0.0', {'engines': {'node': 'latest'}})

    def test_any_installed_version_satisfying_is_enough(self):
        node_filter = F.NodeCompatFilter(node_versions=['18.0.0', '22.0.0'])
        assert node_filter.should_include_version(
            'pkg', '1.0.0', {'engines': {'node': '^22.0.0'}})

    def test_filter_versions_matches_per_version_decisions(self):
        node_filter = F.NodeCompatFilter(node_versions=NODE_22)
        versions = {
            '1.0.0': {'engines': {'node': '>=18'}},
            '0.1.0': {'engines': {'node': '0.4.x'}},
            '2.0.0': {},
        }
        assert sorted(node_filter.filter_versions('pkg', versions)) == \
            ['1.0.0', '2.0.0']

    def test_description_names_the_versions(self):
        node_filter = F.NodeCompatFilter(node_versions=NODE_22)
        assert '22.22.2' in node_filter.get_description()


class TestGentooVersionFilter:

    def test_keeps_plain_versions(self):
        version_filter = F.GentooVersionFilter()
        versions = {'1.0.0': {}, '1.1.0': {}, '2.0.0': {}}
        assert sorted(version_filter.filter_versions('pkg', versions)) == \
            ['1.0.0', '1.1.0', '2.0.0']

    @pytest.mark.parametrize('version', [
        '1.6.0-dev.20150722.1',      # typescript nightly
        '16.4.0-alpha.0911da3',      # react alpha with commit hash
        '0.0.0-experimental-abc',
        '5.9.0-canary.1',
        '1.0.0-next.5',
        '1.0.0-0',
    ])
    def test_drops_unrepresentable_real_versions(self, version):
        version_filter = F.GentooVersionFilter()
        assert version not in version_filter.filter_versions(
            'pkg', {version: {}, '1.0.0': {}})

    def test_never_drops_a_stable_version(self):
        """
        Measured across 26 real packages and 16665 versions: no package loses a
        version without a prerelease tag.
        """
        version_filter = F.GentooVersionFilter()
        stable = {'0.8.1': {}, '1.0.0': {}, '1.6.2': {}, '5.9.2': {},
                  '18.3.1': {}, '0.0.1': {}}
        assert sorted(version_filter.filter_versions('pkg', stable)) == \
            sorted(stable)

    def test_order_inverting_spellings_are_trimmed(self):
        version_filter = F.GentooVersionFilter()
        kept = version_filter.filter_versions(
            'pkg', {'0.9.0-beta8': {}, '0.9.0-beta25': {}})
        assert len(kept) == 1

    def test_per_version_check_is_weaker_than_the_set_check(self):
        """
        should_include_version cannot see siblings, so it accepts a version the
        set-level pass may still drop. Documented, and asserted so the weaker
        contract is not mistaken for the stronger one.
        """
        version_filter = F.GentooVersionFilter()
        assert version_filter.should_include_version('pkg', '0.9.0-beta25', {})
        assert '0.9.0-beta25' not in version_filter.filter_versions(
            'pkg', {'0.9.0-beta8': {}, '0.9.0-beta25': {}})

    def test_preserves_metadata(self):
        version_filter = F.GentooVersionFilter()
        versions = {'1.0.0': {'bin': {'x': 'y'}}}
        assert version_filter.filter_versions('pkg', versions)['1.0.0'] == \
            {'bin': {'x': 'y'}}


class TestDeprecatedFilter:

    def test_off_by_default(self):
        version_filter = F.DeprecatedFilter()
        versions = {'1.0.0': {'deprecated': 'old'}, '2.0.0': {}}
        assert sorted(version_filter.filter_versions('pkg', versions)) == \
            ['1.0.0', '2.0.0']

    def test_excludes_when_enabled(self):
        version_filter = F.DeprecatedFilter(exclude_deprecated=True)
        versions = {'1.0.0': {'deprecated': 'use 2.x'}, '2.0.0': {}}
        assert sorted(version_filter.filter_versions('pkg', versions)) == ['2.0.0']

    def test_empty_string_is_not_a_deprecation(self):
        version_filter = F.DeprecatedFilter(exclude_deprecated=True)
        assert version_filter.should_include_version(
            'pkg', '1.0.0', {'deprecated': ''})

    def test_default_is_off_deliberately(self):
        """
        Deprecated npm versions still install and run, and deprecation is
        common; hiding them by default would make packages vanish for reasons
        the user never asked about.
        """
        assert 'deprecated' not in F.DEFAULT_FILTERS
        assert 'deprecated' in F.OPTIONAL_FILTERS


class TestHasBinFilter:

    def test_off_by_default(self):
        version_filter = F.HasBinFilter()
        versions = {'1.0.0': {}, '2.0.0': {'bin': {'t': 'c.js'}}}
        assert sorted(version_filter.filter_versions('pkg', versions)) == \
            ['1.0.0', '2.0.0']

    @pytest.mark.parametrize('bin_value,expected', [
        ({'tool': 'cli.js'}, True),
        ('cli.js', True),
        ({}, False),
        (None, False),
    ])
    def test_recognises_both_bin_shapes(self, bin_value, expected):
        version_filter = F.HasBinFilter(require_bin=True)
        assert version_filter.should_include_version(
            'pkg', '1.0.0', {'bin': bin_value}) is expected

    def test_is_for_browsing_not_resolution(self):
        """Enabling it during resolution would hide legitimate libraries."""
        assert 'has-bin' not in F.DEFAULT_FILTERS
        assert 'has-bin' in F.OPTIONAL_FILTERS


class TestOsCpuToKeywords:

    @pytest.mark.parametrize('os_values,cpu_values,expected', [
        (None, None, '~amd64 ~arm64'),
        (['linux'], None, '~amd64 ~arm64'),
        (['linux'], ['x64'], '~amd64'),
        (['linux'], ['arm64'], '~arm64'),
        (['linux'], ['ia32'], '~x86'),
        (['linux'], ['arm'], '~arm'),
        (['darwin'], ['arm64'], '~arm64-macos'),
        (['darwin'], ['x64'], '~x64-macos'),
        (['darwin'], None, '~x64-macos ~arm64-macos'),
        (['linux', 'darwin'], ['x64'], '~amd64 ~x64-macos'),
    ])
    def test_maps_platforms(self, os_values, cpu_values, expected):
        assert F.os_cpu_to_keywords(os_values, cpu_values) == expected

    @pytest.mark.parametrize('os_values,cpu_values', [
        (['win32'], None),
        (['win32'], ['x64']),
        (['sunos'], None),
        (['android'], ['arm64']),
        (['linux'], ['mips']),
        (['freebsd'], None),
    ])
    def test_unsupported_platforms_get_empty_keywords(self, os_values, cpu_values):
        """
        Empty KEYWORDS keeps the package visible so portage can say "no
        KEYWORDS for your architecture", rather than the package silently not
        existing. Same philosophy as the RubyGems platform mapping.
        """
        assert F.os_cpu_to_keywords(os_values, cpu_values) == ''

    @pytest.mark.parametrize('os_values,cpu_values,expected', [
        (['!win32'], ['x64'], '~amd64'),
        (['!win32'], None, '~amd64 ~arm64'),
        (['linux'], ['!ia32'], '~amd64 ~arm64'),
        (['!linux'], None, ''),
    ])
    def test_negation_means_everything_but(self, os_values, cpu_values, expected):
        assert F.os_cpu_to_keywords(os_values, cpu_values) == expected

    def test_unrestricted_does_not_claim_macos(self):
        """
        A package stating no 'os' restriction has not been tested on macOS
        Prefix, so it must not be credited with darwin keywords.
        """
        assert 'macos' not in F.os_cpu_to_keywords()


class TestRegistry:

    def test_all_filters_registered(self):
        assert sorted(F.NpmVersionFilterRegistry.get_all_filters()) == \
            ['deprecated', 'gentoo-version', 'has-bin', 'node-compat']

    def test_lookup(self):
        registry = F.NpmVersionFilterRegistry
        assert registry.get_filter_class('node-compat') is F.NodeCompatFilter
        assert registry.get_filter_class('missing') is None

    def test_default_membership(self):
        registry = F.NpmVersionFilterRegistry
        assert registry.is_default('gentoo-version')
        assert not registry.is_default('has-bin')

    def test_names_match_filter_declarations(self):
        for name, cls in F.NpmVersionFilterRegistry.get_all_filters().items():
            assert cls.get_filter_name() == name

    def test_registry_is_separate_from_the_shared_one(self):
        """
        The shared registry is one global namespace with no ecosystem
        partitioning, so ecosystems keep their own to avoid name collisions.
        """
        from portage_pip_fuse.version_filter import VersionFilterRegistry
        assert VersionFilterRegistry.get_filter_class('gentoo-version') is None


class TestCreateFilterChain:

    def test_defaults(self):
        chain = F.create_filter_chain(node_versions=NODE_22)
        assert sorted(type(f).__name__ for f in chain.filters) == \
            ['GentooVersionFilter', 'NodeCompatFilter']

    def test_disable(self):
        chain = F.create_filter_chain(disabled_filters=['node-compat'],
                                      node_versions=NODE_22)
        assert [type(f).__name__ for f in chain.filters] == ['GentooVersionFilter']

    def test_enable_optional(self):
        chain = F.create_filter_chain(enabled_filters=['has-bin', 'deprecated'],
                                      node_versions=NODE_22)
        names = sorted(type(f).__name__ for f in chain.filters)
        assert names == ['DeprecatedFilter', 'GentooVersionFilter',
                         'HasBinFilter', 'NodeCompatFilter']

    def test_optional_filters_are_activated_when_named(self):
        chain = F.create_filter_chain(enabled_filters=['has-bin', 'deprecated'],
                                      node_versions=NODE_22)
        by_type = {type(f).__name__: f for f in chain.filters}
        assert by_type['HasBinFilter'].require_bin
        assert by_type['DeprecatedFilter'].exclude_deprecated

    def test_unknown_filter_raises(self):
        with pytest.raises(ValueError, match='Unknown npm version filter'):
            F.create_filter_chain(enabled_filters=['nope'], node_versions=NODE_22)

    def test_reuses_the_shared_chain(self):
        from portage_pip_fuse.version_filter import VersionFilterChain
        chain = F.create_filter_chain(node_versions=NODE_22)
        assert isinstance(chain, VersionFilterChain)

    def test_chain_applies_all_filters(self):
        chain = F.create_filter_chain(node_versions=NODE_22)
        versions = {
            '1.0.0': {'engines': {'node': '>=18'}},
            '0.1.0': {'engines': {'node': '0.4.x'}},
            '2.0.0-next.5': {},
        }
        assert sorted(chain.filter_versions('pkg', versions)) == ['1.0.0']

    def test_empty_chain_is_a_no_op(self):
        chain = F.create_filter_chain(
            disabled_filters=['gentoo-version', 'node-compat'],
            node_versions=NODE_22)
        assert chain.filters == []
        versions = {'1.0.0-next.5': {}}
        assert chain.filter_versions('pkg', versions) == versions


class TestNodeTargets:

    def setup_method(self):
        node_targets.clear_cache()

    def teardown_method(self):
        os.environ.pop('NODE_VERSIONS', None)
        node_targets.clear_cache()

    def test_environment_override_wins(self):
        os.environ['NODE_VERSIONS'] = '18.0.0 20.1.2'
        assert node_targets.get_node_versions() == ['20.1.2', '18.0.0']

    def test_never_returns_empty(self):
        assert len(node_targets.get_node_versions()) >= 1

    def test_results_are_cached(self):
        os.environ['NODE_VERSIONS'] = '18.0.0'
        first = node_targets.get_node_versions()
        os.environ['NODE_VERSIONS'] = '20.0.0'
        assert node_targets.get_node_versions() == first, 'should be cached'
        node_targets.clear_cache()
        assert node_targets.get_node_versions() == ['20.0.0']

    @pytest.mark.parametrize('text,expected', [
        ('v22.22.2', '22.22.2'),
        ('22.22.2', '22.22.2'),
        ('20.11.1-r1', '20.11.1'),
        ('  v18.0.0  ', '18.0.0'),
        ('22', None),
        ('', None),
    ])
    def test_normalisation(self, text, expected):
        assert node_targets.NodeTargetDetector._normalise(text) == expected

    def test_sorted_newest_first(self):
        os.environ['NODE_VERSIONS'] = '18.0.0 22.0.0 20.0.0 18.0.0'
        assert node_targets.get_node_versions() == \
            ['22.0.0', '20.0.0', '18.0.0']

    def test_garbage_override_falls_through_to_detection(self):
        os.environ['NODE_VERSIONS'] = 'nonsense'
        assert len(node_targets.get_node_versions()) >= 1


def test_filters_doctests():
    results = doctest.testmod(F, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed


def test_node_targets_doctests():
    results = doctest.testmod(node_targets, verbose=False)
    assert results.failed == 0, '%d doctest(s) failed' % results.failed
