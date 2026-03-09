"""
Unit tests for RubyGems FUSE filesystem.

Tests the Manifest generation, version translation, and path parsing
for the PortageGemFS class.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from io import BytesIO


class TestVersionTranslation:
    """Tests for gem <-> Gentoo version translation."""

    @pytest.fixture
    def fs(self):
        """Create a PortageGemFS instance without full initialization."""
        from portage_pip_fuse.ecosystems.rubygems.filesystem import PortageGemFS
        fs = PortageGemFS.__new__(PortageGemFS)
        return fs

    def test_translate_gem_version_stable(self, fs):
        """Stable versions should remain unchanged."""
        assert fs._translate_gem_version('1.0.0') == '1.0.0'
        assert fs._translate_gem_version('2.3.4') == '2.3.4'
        assert fs._translate_gem_version('10.20.30') == '10.20.30'

    def test_translate_gem_version_alpha(self, fs):
        """Alpha versions should use underscore."""
        assert fs._translate_gem_version('1.0.0.alpha') == '1.0.0_alpha'
        assert fs._translate_gem_version('1.0.0.alpha1') == '1.0.0_alpha1'
        assert fs._translate_gem_version('2.0.0.alpha12') == '2.0.0_alpha12'

    def test_translate_gem_version_beta(self, fs):
        """Beta versions should use underscore."""
        assert fs._translate_gem_version('1.0.0.beta') == '1.0.0_beta'
        assert fs._translate_gem_version('1.0.0.beta2') == '1.0.0_beta2'

    def test_translate_gem_version_rc(self, fs):
        """RC versions should use underscore."""
        assert fs._translate_gem_version('1.0.0.rc1') == '1.0.0_rc1'
        assert fs._translate_gem_version('5.0.0.rc') == '5.0.0_rc'

    def test_translate_gem_version_pre(self, fs):
        """Pre-release versions should use underscore."""
        assert fs._translate_gem_version('1.0.0.pre') == '1.0.0_pre'
        assert fs._translate_gem_version('1.0.0.pre3') == '1.0.0_pre3'

    def test_gentoo_to_gem_version_stable(self, fs):
        """Stable versions should remain unchanged."""
        assert fs._gentoo_to_gem_version('1.0.0') == '1.0.0'
        assert fs._gentoo_to_gem_version('2.3.4') == '2.3.4'

    def test_gentoo_to_gem_version_alpha(self, fs):
        """Alpha versions should convert back to dot notation."""
        assert fs._gentoo_to_gem_version('1.0.0_alpha') == '1.0.0.alpha'
        assert fs._gentoo_to_gem_version('1.0.0_alpha1') == '1.0.0.alpha1'

    def test_gentoo_to_gem_version_roundtrip(self, fs):
        """Version translation should be reversible."""
        versions = ['1.0.0', '2.0.0.alpha1', '3.0.0.beta2', '4.0.0.rc1', '5.0.0.pre']
        for v in versions:
            gentoo = fs._translate_gem_version(v)
            back = fs._gentoo_to_gem_version(gentoo)
            assert back == v, f"Roundtrip failed for {v}: {v} -> {gentoo} -> {back}"


class TestPathParsing:
    """Tests for filesystem path parsing."""

    @pytest.fixture
    def fs(self):
        """Create a PortageGemFS instance without full initialization."""
        from portage_pip_fuse.ecosystems.rubygems.filesystem import PortageGemFS
        fs = PortageGemFS.__new__(PortageGemFS)
        return fs

    def test_parse_root(self, fs):
        """Root path should parse correctly."""
        assert fs._parse_path('/') == {'type': 'root'}
        assert fs._parse_path('') == {'type': 'root'}

    def test_parse_category(self, fs):
        """Category path should parse correctly."""
        result = fs._parse_path('/dev-ruby')
        assert result == {'type': 'category', 'category': 'dev-ruby'}

    def test_parse_package(self, fs):
        """Package path should parse correctly."""
        result = fs._parse_path('/dev-ruby/rails')
        assert result == {'type': 'package', 'category': 'dev-ruby', 'package': 'rails'}

    def test_parse_ebuild(self, fs):
        """Ebuild path should parse with version extracted."""
        result = fs._parse_path('/dev-ruby/rails/rails-7.0.0.ebuild')
        assert result == {
            'type': 'ebuild',
            'category': 'dev-ruby',
            'package': 'rails',
            'version': '7.0.0',
            'filename': 'rails-7.0.0.ebuild'
        }

    def test_parse_ebuild_with_prerelease(self, fs):
        """Ebuild path with pre-release version should parse correctly."""
        result = fs._parse_path('/dev-ruby/rails/rails-7.0.0_alpha1.ebuild')
        assert result['type'] == 'ebuild'
        assert result['version'] == '7.0.0_alpha1'

    def test_parse_manifest(self, fs):
        """Manifest path should parse correctly."""
        result = fs._parse_path('/dev-ruby/rails/Manifest')
        assert result == {
            'type': 'manifest',
            'category': 'dev-ruby',
            'package': 'rails',
            'filename': 'Manifest'
        }

    def test_parse_metadata_xml(self, fs):
        """metadata.xml path should parse correctly."""
        result = fs._parse_path('/dev-ruby/rails/metadata.xml')
        assert result == {
            'type': 'package_metadata',
            'category': 'dev-ruby',
            'package': 'rails',
            'filename': 'metadata.xml'
        }

    def test_parse_profiles_repo_name(self, fs):
        """profiles/repo_name should parse correctly."""
        result = fs._parse_path('/profiles/repo_name')
        assert result == {'type': 'profiles_file', 'filename': 'repo_name'}

    def test_parse_layout_conf(self, fs):
        """metadata/layout.conf should parse correctly."""
        result = fs._parse_path('/metadata/layout.conf')
        assert result == {'type': 'metadata_file', 'filename': 'layout.conf'}


class TestManifestGeneration:
    """Tests for Manifest file generation."""

    @pytest.fixture
    def mock_metadata_provider(self):
        """Create a mock metadata provider."""
        provider = Mock()
        provider.get_versions_metadata.return_value = [
            {'number': '1.2.0', 'sha': 'abc123def456'},
            {'number': '1.1.0', 'sha': 'def789ghi012'},
            {'number': '1.0.0', 'sha': '111222333444'},
        ]
        return provider

    @pytest.fixture
    def fs(self, mock_metadata_provider):
        """Create a PortageGemFS with mocked dependencies."""
        from portage_pip_fuse.ecosystems.rubygems.filesystem import PortageGemFS

        fs = PortageGemFS.__new__(PortageGemFS)
        fs.metadata_provider = mock_metadata_provider
        fs.cache_ttl = 3600
        fs._metadata_cache = {}
        fs._versions_cache = {}
        fs.version_filter_chain = None
        fs.max_versions = 0
        fs.name_translator = Mock()
        fs.name_translator.gentoo_to_rubygems.return_value = 'testgem'
        return fs

    def test_manifest_format(self, fs):
        """Manifest should have correct DIST line format."""
        # Mock _get_package_versions to return specific versions
        fs._get_package_versions = Mock(return_value=['1.2.0', '1.1.0'])

        # Mock file size fetching
        fs._get_gem_file_size = Mock(return_value=12345)

        manifest = fs._generate_manifest('testgem', 'testgem')

        assert 'DIST testgem-1.2.0.gem 12345 SHA256 abc123def456' in manifest
        assert 'DIST testgem-1.1.0.gem 12345 SHA256 def789ghi012' in manifest

    def test_manifest_empty_when_no_versions(self, fs):
        """Manifest should be empty when no versions available."""
        fs._get_package_versions = Mock(return_value=[])

        manifest = fs._generate_manifest('testgem', 'testgem')

        assert manifest == ''

    def test_manifest_skips_missing_sha(self, fs):
        """Manifest should skip versions without SHA256."""
        fs.metadata_provider.get_versions_metadata.return_value = [
            {'number': '1.2.0', 'sha': 'abc123'},
            {'number': '1.1.0'},  # No sha
        ]
        fs._get_package_versions = Mock(return_value=['1.2.0', '1.1.0'])
        fs._get_gem_file_size = Mock(return_value=1000)

        manifest = fs._generate_manifest('testgem', 'testgem')

        assert 'testgem-1.2.0.gem' in manifest
        assert 'testgem-1.1.0.gem' not in manifest

    def test_manifest_skips_zero_size(self, fs):
        """Manifest should skip versions where size fetch fails."""
        fs._get_package_versions = Mock(return_value=['1.2.0'])
        fs._get_gem_file_size = Mock(return_value=0)

        manifest = fs._generate_manifest('testgem', 'testgem')

        assert manifest == ''

    def test_manifest_version_translation(self, fs):
        """Manifest should handle pre-release version translation."""
        fs.metadata_provider.get_versions_metadata.return_value = [
            {'number': '2.0.0.alpha1', 'sha': 'alpha123'},
        ]
        # Gentoo version uses underscore
        fs._get_package_versions = Mock(return_value=['2.0.0_alpha1'])
        fs._get_gem_file_size = Mock(return_value=5000)

        manifest = fs._generate_manifest('testgem', 'testgem')

        # Filename should use gem version (dot notation)
        assert 'DIST testgem-2.0.0.alpha1.gem 5000 SHA256 alpha123' in manifest


class TestGetGemFileSize:
    """Tests for gem file size fetching."""

    @pytest.fixture
    def fs(self):
        """Create a PortageGemFS with minimal setup."""
        from portage_pip_fuse.ecosystems.rubygems.filesystem import PortageGemFS

        fs = PortageGemFS.__new__(PortageGemFS)
        fs.cache_ttl = 3600
        fs._metadata_cache = {}
        return fs

    def test_size_from_cache(self, fs):
        """Should return cached size if available."""
        import time
        fs._metadata_cache['size_testgem_1.0.0'] = (54321, time.time())

        size = fs._get_gem_file_size('testgem', '1.0.0')

        assert size == 54321

    def test_size_cache_expired(self, fs):
        """Should fetch new size if cache expired."""
        import time
        # Set cache with old timestamp
        fs._metadata_cache['size_testgem_1.0.0'] = (54321, time.time() - 7200)

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.headers.get.return_value = '12345'
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=False)
            mock_urlopen.return_value = mock_response

            size = fs._get_gem_file_size('testgem', '1.0.0')

            assert size == 12345
            mock_urlopen.assert_called_once()

    def test_size_fetch_failure(self, fs):
        """Should return 0 if fetch fails."""
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = Exception("Network error")

            size = fs._get_gem_file_size('testgem', '1.0.0')

            assert size == 0

    def test_size_caches_result(self, fs):
        """Should cache the fetched size."""
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.headers.get.return_value = '99999'
            mock_response.__enter__ = Mock(return_value=mock_response)
            mock_response.__exit__ = Mock(return_value=False)
            mock_urlopen.return_value = mock_response

            fs._get_gem_file_size('newgem', '2.0.0')

            assert 'size_newgem_2.0.0' in fs._metadata_cache
            cached_size, _ = fs._metadata_cache['size_newgem_2.0.0']
            assert cached_size == 99999


class TestLayoutConf:
    """Tests for layout.conf generation."""

    @pytest.fixture
    def fs(self):
        """Create a PortageGemFS instance."""
        from portage_pip_fuse.ecosystems.rubygems.filesystem import PortageGemFS
        fs = PortageGemFS.__new__(PortageGemFS)
        return fs

    def test_layout_conf_content(self, fs):
        """layout.conf should contain required fields."""
        layout = fs._generate_layout_conf()

        assert 'repo-name = portage-gem-fuse' in layout
        assert 'masters = gentoo' in layout
        assert 'thin-manifests = true' in layout
        assert 'profile-formats = portage-2' in layout


# Run doctests when module is executed
if __name__ == '__main__':
    import doctest
    from portage_pip_fuse.ecosystems.rubygems import filesystem
    doctest.testmod(filesystem, verbose=True)
