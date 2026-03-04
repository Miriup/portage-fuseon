"""
Tests for the source_provider module.

This test suite validates the source provider abstraction layer
for different source types (sdist, git, wheel).

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import os
import sys
import unittest
from unittest import TestCase

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from portage_pip_fuse.source_provider import (
    SourceInfo,
    SourceProviderBase,
    SourceDistProvider,
    GitProvider,
    WheelProvider,
    SourceProviderChain,
)


class TestSourceInfo(TestCase):
    """Test SourceInfo dataclass."""

    def test_sdist_source_info(self):
        """Test creating SourceInfo for sdist."""
        info = SourceInfo(
            provider_name='sdist',
            eclass_inherits=['distutils-r1', 'pypi'],
            src_uri='https://pypi.org/packages/example-1.0.tar.gz'
        )
        self.assertEqual(info.provider_name, 'sdist')
        self.assertIn('pypi', info.eclass_inherits)
        self.assertFalse(info.uses_git())

    def test_git_source_info(self):
        """Test creating SourceInfo for git."""
        info = SourceInfo(
            provider_name='git',
            eclass_inherits=['distutils-r1', 'git-r3'],
            git_repo_uri='https://github.com/user/repo.git',
            git_commit='v${PV}'
        )
        self.assertEqual(info.provider_name, 'git')
        self.assertIn('git-r3', info.eclass_inherits)
        self.assertTrue(info.uses_git())
        self.assertEqual(info.git_repo_uri, 'https://github.com/user/repo.git')
        self.assertEqual(info.git_commit, 'v${PV}')

    def test_wheel_source_info(self):
        """Test creating SourceInfo for wheel."""
        info = SourceInfo(
            provider_name='wheel',
            eclass_inherits=['python-r1'],
            src_uri='https://pypi.org/packages/example-1.0-py3-none-any.whl',
            extra_variables={'wheel_filename': 'example-1.0-py3-none-any.whl'}
        )
        self.assertEqual(info.provider_name, 'wheel')
        self.assertFalse(info.uses_git())
        self.assertIn('wheel_filename', info.extra_variables)

    def test_uses_git_method(self):
        """Test the uses_git() method."""
        git_info = SourceInfo(
            provider_name='git',
            git_repo_uri='https://github.com/user/repo.git'
        )
        self.assertTrue(git_info.uses_git())

        non_git_info = SourceInfo(provider_name='sdist')
        self.assertFalse(non_git_info.uses_git())


class TestSourceDistProvider(TestCase):
    """Test SourceDistProvider class."""

    def setUp(self):
        """Set up test fixtures."""
        self.provider = SourceDistProvider()

    def test_name(self):
        """Test provider name."""
        self.assertEqual(self.provider.name(), 'sdist')

    def test_priority(self):
        """Test provider priority."""
        self.assertEqual(self.provider.priority(), 100)

    def test_can_provide_with_sdist(self):
        """Test can_provide with sdist available."""
        package_info = {
            'source_distribution': {'url': 'https://pypi.org/packages/example-1.0.tar.gz'}
        }
        self.assertTrue(self.provider.can_provide(package_info))

    def test_can_provide_without_sdist(self):
        """Test can_provide without sdist."""
        package_info = {'source_distribution': None}
        self.assertFalse(self.provider.can_provide(package_info))

    def test_can_provide_empty_dict(self):
        """Test can_provide with empty dict."""
        self.assertFalse(self.provider.can_provide({}))

    def test_get_source_info(self):
        """Test get_source_info method."""
        package_info = {
            'source_distribution': {'url': 'https://pypi.org/packages/example-1.0.tar.gz'}
        }
        info = self.provider.get_source_info(package_info, '1.0')
        self.assertIsNotNone(info)
        self.assertEqual(info.provider_name, 'sdist')
        self.assertIn('pypi', info.eclass_inherits)

    def test_get_source_info_when_not_available(self):
        """Test get_source_info returns None when sdist not available."""
        package_info = {'source_distribution': None}
        info = self.provider.get_source_info(package_info, '1.0')
        self.assertIsNone(info)


class TestGitProvider(TestCase):
    """Test GitProvider class."""

    def setUp(self):
        """Set up test fixtures."""
        self.provider = GitProvider()

    def test_name(self):
        """Test provider name."""
        self.assertEqual(self.provider.name(), 'git')

    def test_priority(self):
        """Test provider priority."""
        self.assertEqual(self.provider.priority(), 75)

    def test_can_provide_with_git_url(self):
        """Test can_provide with git URL available."""
        package_info = {'git_repo_url': 'https://github.com/user/repo'}
        self.assertTrue(self.provider.can_provide(package_info))

    def test_can_provide_without_git_url(self):
        """Test can_provide without git URL."""
        package_info = {'git_repo_url': None}
        self.assertFalse(self.provider.can_provide(package_info))

    def test_can_provide_empty_dict(self):
        """Test can_provide with empty dict."""
        self.assertFalse(self.provider.can_provide({}))

    def test_get_source_info(self):
        """Test get_source_info method."""
        package_info = {'git_repo_url': 'https://github.com/user/repo'}
        info = self.provider.get_source_info(package_info, '1.0.0')
        self.assertIsNotNone(info)
        self.assertEqual(info.provider_name, 'git')
        self.assertIn('git-r3', info.eclass_inherits)
        self.assertTrue(info.uses_git())
        self.assertEqual(info.git_commit, 'v${PV}')

    def test_get_source_info_normalizes_url(self):
        """Test that get_source_info normalizes the git URL."""
        package_info = {'git_repo_url': 'https://github.com/user/repo/tree/main'}
        info = self.provider.get_source_info(package_info, '1.0.0')
        self.assertIsNotNone(info)
        # URL should be normalized (tree/main removed, .git added)
        self.assertEqual(info.git_repo_uri, 'https://github.com/user/repo.git')


class TestWheelProvider(TestCase):
    """Test WheelProvider class."""

    def setUp(self):
        """Set up test fixtures."""
        self.provider = WheelProvider()

    def test_name(self):
        """Test provider name."""
        self.assertEqual(self.provider.name(), 'wheel')

    def test_priority(self):
        """Test provider priority."""
        self.assertEqual(self.provider.priority(), 50)

    def test_can_provide_with_wheel(self):
        """Test can_provide with wheel available."""
        package_info = {
            'wheel_distribution': {'url': 'https://pypi.org/packages/example-1.0-py3-none-any.whl'}
        }
        self.assertTrue(self.provider.can_provide(package_info))

    def test_can_provide_without_wheel(self):
        """Test can_provide without wheel."""
        package_info = {'wheel_distribution': None}
        self.assertFalse(self.provider.can_provide(package_info))

    def test_get_source_info(self):
        """Test get_source_info method."""
        package_info = {
            'wheel_distribution': {
                'url': 'https://pypi.org/packages/example-1.0-py3-none-any.whl',
                'filename': 'example-1.0-py3-none-any.whl'
            }
        }
        info = self.provider.get_source_info(package_info, '1.0')
        self.assertIsNotNone(info)
        self.assertEqual(info.provider_name, 'wheel')
        self.assertFalse(info.uses_git())


class TestSourceProviderChain(TestCase):
    """Test SourceProviderChain class."""

    def test_default_providers(self):
        """Test default provider initialization."""
        chain = SourceProviderChain()
        self.assertEqual(len(chain.providers), 3)

    def test_provider_priority_order(self):
        """Test that providers are ordered by priority."""
        chain = SourceProviderChain()
        # Should be ordered: sdist (100), git (75), wheel (50)
        self.assertEqual(chain.providers[0].name(), 'sdist')
        self.assertEqual(chain.providers[1].name(), 'git')
        self.assertEqual(chain.providers[2].name(), 'wheel')

    def test_sdist_takes_priority(self):
        """Test that sdist is preferred when available."""
        chain = SourceProviderChain()
        package_info = {
            'source_distribution': {'url': 'https://pypi.org/packages/example-1.0.tar.gz'},
            'git_repo_url': 'https://github.com/user/repo',
            'wheel_distribution': {'url': 'https://pypi.org/packages/example-1.0-py3-none-any.whl'}
        }
        info = chain.get_source_info(package_info, '1.0')
        self.assertIsNotNone(info)
        self.assertEqual(info.provider_name, 'sdist')

    def test_git_fallback_when_no_sdist(self):
        """Test that git is used when no sdist is available."""
        chain = SourceProviderChain()
        package_info = {
            'source_distribution': None,
            'git_repo_url': 'https://github.com/user/repo',
            'wheel_distribution': {'url': 'https://pypi.org/packages/example-1.0-py3-none-any.whl'}
        }
        info = chain.get_source_info(package_info, '1.0')
        self.assertIsNotNone(info)
        self.assertEqual(info.provider_name, 'git')

    def test_wheel_fallback_when_no_sdist_or_git(self):
        """Test that wheel is used when no sdist or git is available."""
        chain = SourceProviderChain()
        package_info = {
            'source_distribution': None,
            'git_repo_url': None,
            'wheel_distribution': {'url': 'https://pypi.org/packages/example-1.0-py3-none-any.whl'}
        }
        info = chain.get_source_info(package_info, '1.0')
        self.assertIsNotNone(info)
        self.assertEqual(info.provider_name, 'wheel')

    def test_none_when_no_source_available(self):
        """Test that None is returned when no source is available."""
        chain = SourceProviderChain()
        package_info = {
            'source_distribution': None,
            'git_repo_url': None,
            'wheel_distribution': None
        }
        info = chain.get_source_info(package_info, '1.0')
        self.assertIsNone(info)

    def test_disable_git(self):
        """Test disabling git provider."""
        chain = SourceProviderChain(enable_git=False)
        self.assertEqual(len(chain.providers), 2)
        # Git should not be in providers
        provider_names = [p.name() for p in chain.providers]
        self.assertNotIn('git', provider_names)

    def test_get_provider_for_package(self):
        """Test get_provider_for_package method."""
        chain = SourceProviderChain()
        package_info = {
            'source_distribution': None,
            'git_repo_url': 'https://github.com/user/repo',
        }
        provider = chain.get_provider_for_package(package_info)
        self.assertIsNotNone(provider)
        self.assertEqual(provider.name(), 'git')


if __name__ == '__main__':
    unittest.main()
