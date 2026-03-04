"""
Tests for the git_source_patch module.

This test suite validates the git source patch store for
manual configuration of git repository sources.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import TestCase

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from portage_pip_fuse.git_source_patch import (
    GitSourcePatch,
    PackageGitSourcePatch,
    GitSourcePatchStore,
    is_valid_source_mode,
    VALID_SOURCE_MODES,
)


class TestIsValidSourceMode(TestCase):
    """Test is_valid_source_mode function."""

    def test_git_is_valid(self):
        """Test that 'git' is a valid mode."""
        self.assertTrue(is_valid_source_mode('git'))

    def test_wheel_is_valid(self):
        """Test that 'wheel' is a valid mode."""
        self.assertTrue(is_valid_source_mode('wheel'))

    def test_auto_is_valid(self):
        """Test that 'auto' is a valid mode."""
        self.assertTrue(is_valid_source_mode('auto'))

    def test_invalid_mode(self):
        """Test that invalid modes return False."""
        self.assertFalse(is_valid_source_mode('invalid'))
        self.assertFalse(is_valid_source_mode('sdist'))
        self.assertFalse(is_valid_source_mode(''))


class TestGitSourcePatch(TestCase):
    """Test GitSourcePatch dataclass."""

    def test_basic_creation(self):
        """Test basic patch creation."""
        patch = GitSourcePatch('git', None, None, 1700000000.0)
        self.assertEqual(patch.mode, 'git')
        self.assertIsNone(patch.git_url)
        self.assertIsNone(patch.tag_pattern)

    def test_creation_with_url(self):
        """Test patch creation with URL."""
        patch = GitSourcePatch(
            'git',
            'https://github.com/user/repo.git',
            'v{version}',
            1700000000.0
        )
        self.assertEqual(patch.git_url, 'https://github.com/user/repo.git')
        self.assertEqual(patch.tag_pattern, 'v{version}')

    def test_invalid_mode_raises(self):
        """Test that invalid mode raises ValueError."""
        with self.assertRaises(ValueError):
            GitSourcePatch('invalid', None, None, 1700000000.0)

    def test_to_dict(self):
        """Test serialization to dict."""
        patch = GitSourcePatch('git', 'https://github.com/user/repo.git', None, 1700000000.0)
        d = patch.to_dict()
        self.assertEqual(d['mode'], 'git')
        self.assertEqual(d['git_url'], 'https://github.com/user/repo.git')

    def test_from_dict(self):
        """Test deserialization from dict."""
        data = {
            'mode': 'git',
            'git_url': 'https://github.com/user/repo.git',
            'tag_pattern': 'v{version}',
            'timestamp': 1700000000.0
        }
        patch = GitSourcePatch.from_dict(data)
        self.assertEqual(patch.mode, 'git')
        self.assertEqual(patch.git_url, 'https://github.com/user/repo.git')


class TestPackageGitSourcePatch(TestCase):
    """Test PackageGitSourcePatch dataclass."""

    def test_basic_creation(self):
        """Test basic creation."""
        pp = PackageGitSourcePatch('dev-python', 'faster-whisper', '1.2.1', None)
        self.assertEqual(pp.category, 'dev-python')
        self.assertEqual(pp.package, 'faster-whisper')
        self.assertEqual(pp.version, '1.2.1')

    def test_is_all_versions(self):
        """Test is_all_versions property."""
        pp_specific = PackageGitSourcePatch('dev-python', 'test', '1.0', None)
        self.assertFalse(pp_specific.is_all_versions)

        pp_all = PackageGitSourcePatch('dev-python', 'test', '_all', None)
        self.assertTrue(pp_all.is_all_versions)

    def test_key_property(self):
        """Test key property."""
        pp = PackageGitSourcePatch('dev-python', 'test', '1.0', None)
        self.assertEqual(pp.key, 'dev-python/test/1.0')

    def test_to_dict_and_from_dict(self):
        """Test serialization round-trip."""
        patch = GitSourcePatch('git', 'https://github.com/user/repo.git', None, 1700000000.0)
        pp = PackageGitSourcePatch('dev-python', 'test', '1.0', patch)
        d = pp.to_dict()
        pp2 = PackageGitSourcePatch.from_dict(d)
        self.assertEqual(pp2.category, 'dev-python')
        self.assertEqual(pp2.patch.mode, 'git')


class TestGitSourcePatchStore(TestCase):
    """Test GitSourcePatchStore class."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_file = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
        self.temp_file.close()
        self.store = GitSourcePatchStore(self.temp_file.name)

    def tearDown(self):
        """Clean up test fixtures."""
        if os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)

    def test_set_git_source(self):
        """Test setting git source for a package."""
        self.store.set_git_source('dev-python', 'test', '_all')
        mode, url, pattern = self.store.get_git_source('dev-python', 'test', '1.0')
        self.assertEqual(mode, 'git')
        self.assertIsNone(url)
        self.assertIsNone(pattern)

    def test_set_git_source_with_url(self):
        """Test setting git source with custom URL."""
        self.store.set_git_source(
            'dev-python', 'test', '_all',
            git_url='https://github.com/custom/repo.git'
        )
        mode, url, pattern = self.store.get_git_source('dev-python', 'test', '1.0')
        self.assertEqual(mode, 'git')
        self.assertEqual(url, 'https://github.com/custom/repo.git')

    def test_set_git_source_with_tag_pattern(self):
        """Test setting git source with tag pattern."""
        self.store.set_git_source(
            'dev-python', 'test', '_all',
            git_url='https://github.com/user/repo.git',
            tag_pattern='release-{version}'
        )
        mode, url, pattern = self.store.get_git_source('dev-python', 'test', '1.0')
        self.assertEqual(pattern, 'release-{version}')

    def test_set_wheel_fallback(self):
        """Test setting wheel fallback."""
        self.store.set_wheel_fallback('dev-python', 'test', '_all')
        mode, url, pattern = self.store.get_git_source('dev-python', 'test', '1.0')
        self.assertEqual(mode, 'wheel')

    def test_should_use_git(self):
        """Test should_use_git method."""
        self.store.set_git_source('dev-python', 'test', '_all')
        self.assertTrue(self.store.should_use_git('dev-python', 'test', '1.0'))

        self.store.set_wheel_fallback('dev-python', 'other', '_all')
        self.assertFalse(self.store.should_use_git('dev-python', 'other', '1.0'))

        # No patch
        self.assertIsNone(self.store.should_use_git('dev-python', 'unpatched', '1.0'))

    def test_version_specific_override(self):
        """Test version-specific patches override _all patches."""
        self.store.set_git_source('dev-python', 'test', '_all')
        self.store.set_wheel_fallback('dev-python', 'test', '2.0')

        # 1.0 uses _all (git)
        self.assertTrue(self.store.should_use_git('dev-python', 'test', '1.0'))
        # 2.0 uses version-specific (wheel)
        self.assertFalse(self.store.should_use_git('dev-python', 'test', '2.0'))

    def test_remove_patch(self):
        """Test removing a patch."""
        self.store.set_git_source('dev-python', 'test', '_all')
        self.assertTrue(self.store.has_patch('dev-python', 'test', '1.0'))

        self.store.remove_patch('dev-python', 'test', '_all')
        self.assertFalse(self.store.has_patch('dev-python', 'test', '1.0'))

    def test_save_and_load(self):
        """Test persistence."""
        self.store.set_git_source(
            'dev-python', 'test', '_all',
            git_url='https://github.com/user/repo.git'
        )
        self.store.save()

        # Create new store from same file
        store2 = GitSourcePatchStore(self.temp_file.name)
        mode, url, pattern = store2.get_git_source('dev-python', 'test', '1.0')
        self.assertEqual(mode, 'git')
        self.assertEqual(url, 'https://github.com/user/repo.git')

    def test_get_package_versions_with_patches(self):
        """Test listing versions with patches."""
        self.store.set_git_source('dev-python', 'test', '_all')
        self.store.set_git_source('dev-python', 'test', '1.0')
        self.store.set_git_source('dev-python', 'test', '2.0')

        versions = self.store.get_package_versions_with_patches('dev-python', 'test')
        self.assertIn('_all', versions)
        self.assertIn('1.0', versions)
        self.assertIn('2.0', versions)

    def test_generate_patch_file(self):
        """Test patch file generation."""
        self.store.set_git_source(
            'dev-python', 'test', '_all',
            git_url='https://github.com/user/repo.git'
        )
        content = self.store.generate_patch_file('dev-python', 'test', '_all')
        self.assertIn('== git https://github.com/user/repo.git', content)

    def test_generate_patch_file_wheel(self):
        """Test patch file generation for wheel."""
        self.store.set_wheel_fallback('dev-python', 'test', '_all')
        content = self.store.generate_patch_file('dev-python', 'test', '_all')
        self.assertIn('== wheel', content)

    def test_parse_patch_file(self):
        """Test patch file parsing."""
        content = """
        # Git source patch
        == git https://github.com/user/repo.git
        """
        count = self.store.parse_patch_file(content, 'dev-python', 'test', '_all')
        self.assertEqual(count, 1)
        mode, url, pattern = self.store.get_git_source('dev-python', 'test', '1.0')
        self.assertEqual(mode, 'git')
        self.assertEqual(url, 'https://github.com/user/repo.git')

    def test_parse_patch_file_with_tag(self):
        """Test patch file parsing with tag pattern."""
        content = "== git https://github.com/user/repo.git v{version}"
        count = self.store.parse_patch_file(content, 'dev-python', 'test', '_all')
        self.assertEqual(count, 1)
        mode, url, pattern = self.store.get_git_source('dev-python', 'test', '1.0')
        self.assertEqual(pattern, 'v{version}')

    def test_parse_patch_file_wheel(self):
        """Test patch file parsing for wheel."""
        content = "== wheel"
        count = self.store.parse_patch_file(content, 'dev-python', 'test', '_all')
        self.assertEqual(count, 1)
        mode, url, pattern = self.store.get_git_source('dev-python', 'test', '1.0')
        self.assertEqual(mode, 'wheel')

    def test_list_patched_packages(self):
        """Test listing all patched packages."""
        self.store.set_git_source('dev-python', 'pkg1', '_all')
        self.store.set_wheel_fallback('dev-python', 'pkg2', '1.0')

        patches = self.store.list_patched_packages()
        self.assertIn(('dev-python', 'pkg1', '_all'), patches)
        self.assertIn(('dev-python', 'pkg2', '1.0'), patches)

    def test_is_dirty(self):
        """Test dirty flag."""
        self.assertFalse(self.store.is_dirty)
        self.store.set_git_source('dev-python', 'test', '_all')
        self.assertTrue(self.store.is_dirty)
        self.store.save()
        self.assertFalse(self.store.is_dirty)


class TestGitSourcePatchStoreMemoryOnly(TestCase):
    """Test GitSourcePatchStore in memory-only mode."""

    def test_memory_only(self):
        """Test store without file path."""
        store = GitSourcePatchStore(None)
        store.set_git_source('dev-python', 'test', '_all')
        mode, url, pattern = store.get_git_source('dev-python', 'test', '1.0')
        self.assertEqual(mode, 'git')
        # Save should succeed (no-op)
        self.assertTrue(store.save())


if __name__ == '__main__':
    unittest.main()
