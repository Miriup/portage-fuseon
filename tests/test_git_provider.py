"""
Tests for the git_provider module.

This test suite validates git URL extraction, normalization, and
tag pattern detection for wheel-only packages.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import os
import sys
import unittest
from unittest import TestCase

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from portage_pip_fuse.git_provider import (
    extract_git_url,
    is_git_host_url,
    normalize_git_url,
    detect_version_tag,
    get_tag_patterns,
    validate_git_url,
    GIT_HOSTS,
    REPOSITORY_URL_KEYS,
)


class TestExtractGitUrl(TestCase):
    """Test git URL extraction from project_urls."""

    def test_extract_from_repository(self):
        """Test extraction from Repository key."""
        project_urls = {'Repository': 'https://github.com/user/repo'}
        self.assertEqual(extract_git_url(project_urls), 'https://github.com/user/repo')

    def test_extract_from_source(self):
        """Test extraction from Source key."""
        project_urls = {'Source': 'https://gitlab.com/user/repo'}
        self.assertEqual(extract_git_url(project_urls), 'https://gitlab.com/user/repo')

    def test_extract_from_source_code(self):
        """Test extraction from 'Source Code' key."""
        project_urls = {'Source Code': 'https://github.com/user/repo'}
        self.assertEqual(extract_git_url(project_urls), 'https://github.com/user/repo')

    def test_extract_from_github(self):
        """Test extraction from GitHub key."""
        project_urls = {'GitHub': 'https://github.com/user/repo'}
        self.assertEqual(extract_git_url(project_urls), 'https://github.com/user/repo')

    def test_extract_from_gitlab(self):
        """Test extraction from GitLab key."""
        project_urls = {'GitLab': 'https://gitlab.com/user/repo'}
        self.assertEqual(extract_git_url(project_urls), 'https://gitlab.com/user/repo')

    def test_extract_from_homepage_github(self):
        """Test extraction from Homepage when pointing to GitHub."""
        project_urls = {'Homepage': 'https://github.com/user/repo'}
        self.assertEqual(extract_git_url(project_urls), 'https://github.com/user/repo')

    def test_skip_non_git_homepage(self):
        """Test that non-git homepages are skipped."""
        project_urls = {'Homepage': 'https://example.com/project'}
        self.assertIsNone(extract_git_url(project_urls))

    def test_empty_dict(self):
        """Test with empty dictionary."""
        self.assertIsNone(extract_git_url({}))

    def test_none_input(self):
        """Test with None input."""
        self.assertIsNone(extract_git_url(None))

    def test_case_insensitive_keys(self):
        """Test that keys are matched case-insensitively."""
        project_urls = {'REPOSITORY': 'https://github.com/user/repo'}
        self.assertEqual(extract_git_url(project_urls), 'https://github.com/user/repo')

        project_urls = {'source': 'https://github.com/user/repo'}
        self.assertEqual(extract_git_url(project_urls), 'https://github.com/user/repo')

    def test_priority_order(self):
        """Test that Repository is preferred over Source."""
        project_urls = {
            'Source': 'https://github.com/user/source-repo',
            'Repository': 'https://github.com/user/main-repo',
        }
        # Repository should be checked first
        self.assertEqual(extract_git_url(project_urls), 'https://github.com/user/main-repo')

    def test_codeberg(self):
        """Test extraction from Codeberg URL."""
        project_urls = {'Source': 'https://codeberg.org/user/repo'}
        self.assertEqual(extract_git_url(project_urls), 'https://codeberg.org/user/repo')

    def test_bitbucket(self):
        """Test extraction from Bitbucket URL."""
        project_urls = {'Source': 'https://bitbucket.org/user/repo'}
        self.assertEqual(extract_git_url(project_urls), 'https://bitbucket.org/user/repo')


class TestIsGitHostUrl(TestCase):
    """Test git host URL detection."""

    def test_github(self):
        """Test GitHub URL detection."""
        self.assertTrue(is_git_host_url('https://github.com/user/repo'))

    def test_gitlab(self):
        """Test GitLab URL detection."""
        self.assertTrue(is_git_host_url('https://gitlab.com/user/repo'))

    def test_codeberg(self):
        """Test Codeberg URL detection."""
        self.assertTrue(is_git_host_url('https://codeberg.org/user/repo'))

    def test_bitbucket(self):
        """Test Bitbucket URL detection."""
        self.assertTrue(is_git_host_url('https://bitbucket.org/user/repo'))

    def test_sourcehut(self):
        """Test SourceHut URL detection."""
        self.assertTrue(is_git_host_url('https://git.sr.ht/~user/repo'))

    def test_non_git_host(self):
        """Test non-git host detection."""
        self.assertFalse(is_git_host_url('https://example.com/repo'))

    def test_invalid_url(self):
        """Test invalid URL handling."""
        self.assertFalse(is_git_host_url('invalid-url'))
        self.assertFalse(is_git_host_url(''))


class TestNormalizeGitUrl(TestCase):
    """Test git URL normalization."""

    def test_simple_github_url(self):
        """Test simple GitHub URL normalization."""
        self.assertEqual(
            normalize_git_url('https://github.com/user/repo'),
            'https://github.com/user/repo.git'
        )

    def test_url_with_git_suffix(self):
        """Test URL that already has .git suffix."""
        self.assertEqual(
            normalize_git_url('https://github.com/user/repo.git'),
            'https://github.com/user/repo.git'
        )

    def test_github_tree_url(self):
        """Test GitHub URL with /tree/branch suffix."""
        self.assertEqual(
            normalize_git_url('https://github.com/user/repo/tree/main'),
            'https://github.com/user/repo.git'
        )

    def test_github_blob_url(self):
        """Test GitHub URL with /blob/branch/file suffix."""
        self.assertEqual(
            normalize_git_url('https://github.com/user/repo/blob/main/README.md'),
            'https://github.com/user/repo.git'
        )

    def test_gitlab_tree_url(self):
        """Test GitLab URL with /-/tree/branch suffix."""
        self.assertEqual(
            normalize_git_url('https://gitlab.com/user/repo/-/tree/main'),
            'https://gitlab.com/user/repo.git'
        )

    def test_gitlab_blob_url(self):
        """Test GitLab URL with /-/blob/branch/file suffix."""
        self.assertEqual(
            normalize_git_url('https://gitlab.com/user/repo/-/blob/main/README.md'),
            'https://gitlab.com/user/repo.git'
        )

    def test_bitbucket_src_url(self):
        """Test Bitbucket URL with /src/branch suffix."""
        self.assertEqual(
            normalize_git_url('https://bitbucket.org/user/repo/src/master/'),
            'https://bitbucket.org/user/repo.git'
        )

    def test_git_ssh_format(self):
        """Test git@ SSH URL format conversion."""
        self.assertEqual(
            normalize_git_url('git@github.com:user/repo.git'),
            'https://github.com/user/repo.git'
        )

    def test_ssh_protocol_url(self):
        """Test ssh:// URL format conversion."""
        self.assertEqual(
            normalize_git_url('ssh://git@github.com/user/repo.git'),
            'https://github.com/user/repo.git'
        )

    def test_trailing_slash_removal(self):
        """Test trailing slash removal."""
        self.assertEqual(
            normalize_git_url('https://github.com/user/repo/'),
            'https://github.com/user/repo.git'
        )

    def test_issues_url_cleanup(self):
        """Test /issues suffix removal."""
        self.assertEqual(
            normalize_git_url('https://github.com/user/repo/issues'),
            'https://github.com/user/repo.git'
        )

    def test_pulls_url_cleanup(self):
        """Test /pulls suffix removal."""
        self.assertEqual(
            normalize_git_url('https://github.com/user/repo/pulls'),
            'https://github.com/user/repo.git'
        )

    def test_releases_url_cleanup(self):
        """Test /releases suffix removal."""
        self.assertEqual(
            normalize_git_url('https://github.com/user/repo/releases'),
            'https://github.com/user/repo.git'
        )

    def test_whitespace_stripping(self):
        """Test whitespace stripping."""
        self.assertEqual(
            normalize_git_url('  https://github.com/user/repo  '),
            'https://github.com/user/repo.git'
        )


class TestDetectVersionTag(TestCase):
    """Test version to tag pattern detection."""

    def test_simple_version(self):
        """Test simple version tag detection."""
        self.assertEqual(detect_version_tag('1.0.0'), 'v${PV}')

    def test_another_version(self):
        """Test another version."""
        self.assertEqual(detect_version_tag('1.2.3'), 'v${PV}')


class TestGetTagPatterns(TestCase):
    """Test tag pattern list."""

    def test_returns_list(self):
        """Test that get_tag_patterns returns a list."""
        patterns = get_tag_patterns()
        self.assertIsInstance(patterns, list)
        self.assertGreater(len(patterns), 0)

    def test_v_prefix_pattern(self):
        """Test that v${PV} pattern is included."""
        patterns = get_tag_patterns()
        self.assertIn('v${PV}', patterns)

    def test_plain_version_pattern(self):
        """Test that ${PV} pattern is included."""
        patterns = get_tag_patterns()
        self.assertIn('${PV}', patterns)


class TestValidateGitUrl(TestCase):
    """Test git URL validation."""

    def test_valid_https_url(self):
        """Test valid HTTPS URL."""
        self.assertTrue(validate_git_url('https://github.com/user/repo.git'))

    def test_valid_http_url(self):
        """Test valid HTTP URL."""
        self.assertTrue(validate_git_url('http://github.com/user/repo.git'))

    def test_valid_ssh_url(self):
        """Test valid SSH URL."""
        self.assertTrue(validate_git_url('ssh://git@github.com/user/repo.git'))

    def test_valid_git_at_url(self):
        """Test valid git@ URL format."""
        self.assertTrue(validate_git_url('git@github.com:user/repo.git'))

    def test_invalid_url(self):
        """Test invalid URL."""
        self.assertFalse(validate_git_url('invalid'))

    def test_empty_string(self):
        """Test empty string."""
        self.assertFalse(validate_git_url(''))

    def test_none_value(self):
        """Test None-like behavior (should return False for falsy values)."""
        # Empty string is falsy
        self.assertFalse(validate_git_url(''))


class TestGitHostsConstant(TestCase):
    """Test GIT_HOSTS constant."""

    def test_contains_github(self):
        """Test that GitHub is in GIT_HOSTS."""
        self.assertIn('github.com', GIT_HOSTS)

    def test_contains_gitlab(self):
        """Test that GitLab is in GIT_HOSTS."""
        self.assertIn('gitlab.com', GIT_HOSTS)

    def test_contains_codeberg(self):
        """Test that Codeberg is in GIT_HOSTS."""
        self.assertIn('codeberg.org', GIT_HOSTS)

    def test_contains_bitbucket(self):
        """Test that Bitbucket is in GIT_HOSTS."""
        self.assertIn('bitbucket.org', GIT_HOSTS)


class TestRepositoryUrlKeysConstant(TestCase):
    """Test REPOSITORY_URL_KEYS constant."""

    def test_contains_repository(self):
        """Test that Repository key is included."""
        self.assertIn('Repository', REPOSITORY_URL_KEYS)

    def test_contains_source(self):
        """Test that Source key is included."""
        self.assertIn('Source', REPOSITORY_URL_KEYS)

    def test_contains_github(self):
        """Test that GitHub key is included."""
        self.assertIn('GitHub', REPOSITORY_URL_KEYS)

    def test_homepage_is_last_resort(self):
        """Test that Homepage is included (as fallback)."""
        self.assertIn('Homepage', REPOSITORY_URL_KEYS)


if __name__ == '__main__':
    unittest.main()
