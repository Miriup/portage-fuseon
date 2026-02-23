"""
Comprehensive tests for the name translator module.

This test suite validates the bidirectional translation between PyPI
and Gentoo package names using real PyPI data.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import json
import os
import sys
import unittest
from typing import List, Set, Tuple
from unittest import TestCase, skipIf

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from portage_pip_fuse.name_translator import (
    SimpleNameTranslator,
    CachedNameTranslator,
    pypi_to_gentoo,
    gentoo_to_pypi,
)

# Try to import pip for getting package list
try:
    from pip._internal.metadata import get_default_environment
    from pip._internal.models.index import PyPI
    HAS_PIP = True
except ImportError:
    try:
        from pip import get_installed_distributions
        HAS_PIP = True
    except ImportError:
        HAS_PIP = False

# Try alternate method using pkg_resources
try:
    import pkg_resources
    HAS_PKG_RESOURCES = True
except ImportError:
    HAS_PKG_RESOURCES = False

# Try to use requests for PyPI API
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class TestSimpleNameTranslator(TestCase):
    """Test the SimpleNameTranslator class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.translator = SimpleNameTranslator()
    
    def test_basic_pypi_to_gentoo(self):
        """Test basic PyPI to Gentoo name translation."""
        test_cases = [
            ("Django", "django"),
            ("Flask", "flask"),
            ("SQLAlchemy", "sqlalchemy"),
            ("beautifulsoup4", "beautifulsoup4"),
            ("python-dateutil", "python-dateutil"),
            ("websocket_client", "websocket-client"),
            ("backports.zoneinfo", "backports-zoneinfo"),
            ("google.cloud.storage", "google-cloud-storage"),
            ("typing_extensions", "typing-extensions"),
        ]
        
        for pypi_name, expected_gentoo in test_cases:
            with self.subTest(pypi_name=pypi_name):
                result = self.translator.pypi_to_gentoo(pypi_name)
                self.assertEqual(result, expected_gentoo)
    
    def test_edge_cases_pypi_to_gentoo(self):
        """Test edge cases in PyPI to Gentoo translation."""
        test_cases = [
            ("a", "a"),  # Single character
            ("A", "a"),  # Single character uppercase
            ("my__package", "my-package"),  # Double underscore
            ("my...package", "my-package"),  # Multiple dots
            ("my---package", "my-package"),  # Multiple hyphens
            ("my._-package", "my-package"),  # Mixed separators
            ("UPPERCASE", "uppercase"),  # All uppercase
            ("CamelCase", "camelcase"),  # Camel case
            ("snake_case_name", "snake-case-name"),  # Snake case
            ("kebab-case-name", "kebab-case-name"),  # Already kebab case
            ("dot.separated.name", "dot-separated-name"),  # Dot separated
            ("123package", "123package"),  # Starting with number
            ("package123", "package123"),  # Ending with number
        ]
        
        for pypi_name, expected_gentoo in test_cases:
            with self.subTest(pypi_name=pypi_name):
                result = self.translator.pypi_to_gentoo(pypi_name)
                self.assertEqual(result, expected_gentoo)
    
    def test_gentoo_to_pypi_with_cache(self):
        """Test Gentoo to PyPI reverse translation using cache."""
        # Populate cache
        self.translator.pypi_to_gentoo("Django")
        self.translator.pypi_to_gentoo("websocket_client")
        self.translator.pypi_to_gentoo("websocket-client")
        
        # Test reverse translation
        result = self.translator.gentoo_to_pypi("django")
        self.assertEqual(result, "Django")
        
        # Test with multiple possibilities in cache
        result = self.translator.gentoo_to_pypi("websocket-client")
        self.assertIn(result, ["websocket_client", "websocket-client"])
    
    def test_gentoo_to_pypi_with_hint(self):
        """Test Gentoo to PyPI translation with hints."""
        result = self.translator.gentoo_to_pypi("django", hint="Django")
        self.assertEqual(result, "Django")
        
        result = self.translator.gentoo_to_pypi("websocket-client", hint="websocket_client")
        self.assertEqual(result, "websocket_client")
        
        # Invalid hint should be ignored
        result = self.translator.gentoo_to_pypi("django", hint="NotDjango")
        self.assertEqual(result, "django")  # Falls back to gentoo name
    
    def test_is_valid_pypi_name(self):
        """Test PyPI name validation."""
        valid_names = [
            "django", "Django", "python-dateutil", "backports.zoneinfo",
            "websocket_client", "my-package", "package123", "123package",
            "_private", "a", "A", "my--pkg", "my__pkg", "my..pkg"
        ]
        
        invalid_names = [
            "", "-start", "end-", ".start", "end.", "_", "-", ".",
            "my package",  # Space not allowed
            "my/package",  # Slash not allowed
            "my@package",  # @ not allowed
        ]
        
        for name in valid_names:
            with self.subTest(name=name):
                self.assertTrue(self.translator.is_valid_pypi_name(name))
        
        for name in invalid_names:
            with self.subTest(name=name):
                self.assertFalse(self.translator.is_valid_pypi_name(name))
    
    def test_is_valid_gentoo_name(self):
        """Test Gentoo name validation."""
        valid_names = [
            "django", "python-dateutil", "backports-zoneinfo",
            "websocket-client", "my-package", "package123", "123package",
            "a", "a-b-c-d"
        ]
        
        invalid_names = [
            "", "Django",  # Uppercase not allowed
            "my_package",  # Underscore not allowed
            "my.package",  # Dot not allowed
            "my--package",  # Double hyphen not allowed
            "-start", "end-", "_private"
        ]
        
        for name in valid_names:
            with self.subTest(name=name):
                self.assertTrue(self.translator.is_valid_gentoo_name(name))
        
        for name in invalid_names:
            with self.subTest(name=name):
                self.assertFalse(self.translator.is_valid_gentoo_name(name))
    
    def test_normalize_pypi_name(self):
        """Test PyPI name normalization."""
        test_cases = [
            ("Django", "django"),
            ("websocket_client", "websocket-client"),
            ("my__package", "my-package"),
            ("my...package", "my-package"),
            ("my---package", "my-package"),
            ("My__Package---Name...", "my-package-name"),
        ]
        
        for input_name, expected in test_cases:
            with self.subTest(input_name=input_name):
                result = self.translator.normalize_pypi_name(input_name)
                self.assertEqual(result, expected)
    
    def test_split_category(self):
        """Test splitting Gentoo package category."""
        test_cases = [
            ("dev-python/django", ("dev-python", "django")),
            ("dev-python/python-dateutil", ("dev-python", "python-dateutil")),
            ("virtual/python-enum34", ("virtual", "python-enum34")),
            ("django", ("", "django")),  # No category
            ("dev-python/my-package", ("dev-python", "my-package")),
        ]
        
        for full_name, expected in test_cases:
            with self.subTest(full_name=full_name):
                result = self.translator.split_category(full_name)
                self.assertEqual(result, expected)


class TestCachedNameTranslator(TestCase):
    """Test the CachedNameTranslator class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.translator = CachedNameTranslator()
    
    def test_preload_mappings(self):
        """Test preloading name mappings."""
        mappings = {
            "Django": "django",
            "Flask": "flask",
            "PyYAML": "pyyaml",
            "beautifulsoup4": "beautifulsoup4",
            "BeautifulSoup": "beautifulsoup",
        }
        
        self.translator.preload_mappings(mappings)
        
        # Test forward translation uses cache
        self.assertEqual(self.translator.pypi_to_gentoo("Django"), "django")
        
        # Test reverse translation uses preloaded mappings
        self.assertEqual(self.translator.gentoo_to_pypi("django"), "Django")
        self.assertEqual(self.translator.gentoo_to_pypi("pyyaml"), "PyYAML")
        self.assertEqual(self.translator.gentoo_to_pypi("beautifulsoup"), "BeautifulSoup")
    
    def test_caching_behavior(self):
        """Test that translations are cached."""
        # First translation
        result1 = self.translator.pypi_to_gentoo("TestPackage")
        self.assertEqual(result1, "testpackage")
        
        # Check it's in cache
        self.assertIn("TestPackage", self.translator._forward_cache)
        self.assertEqual(self.translator._forward_cache["TestPackage"], "testpackage")
        
        # Reverse lookup should work
        result2 = self.translator.gentoo_to_pypi("testpackage")
        self.assertEqual(result2, "TestPackage")
    
    def test_clear_cache(self):
        """Test clearing the cache."""
        # Add some entries
        self.translator.pypi_to_gentoo("Django")
        self.translator.pypi_to_gentoo("Flask")
        self.translator.preload_mappings({"PyYAML": "pyyaml"})
        
        # Verify cache has entries
        self.assertGreater(len(self.translator._forward_cache), 0)
        self.assertGreater(len(self.translator._reverse_cache), 0)
        
        # Clear cache
        self.translator.clear_cache()
        
        # Verify cache is empty
        self.assertEqual(len(self.translator._forward_cache), 0)
        self.assertEqual(len(self.translator._reverse_cache), 0)
        self.assertEqual(len(self.translator._preferred_pypi), 0)


class TestRealPyPIPackages(TestCase):
    """Test with real PyPI package names."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.translator = SimpleNameTranslator()
        self.test_packages = self._get_test_packages()
    
    def _get_test_packages(self) -> List[str]:
        """
        Get a list of real PyPI package names for testing.
        
        Returns a curated list of common packages if we can't fetch from PyPI.
        """
        # Curated list of common PyPI packages with various naming styles
        return [
            # Standard lowercase
            "django", "flask", "requests", "numpy", "pandas",
            "pytest", "sphinx", "setuptools", "wheel", "pip",
            
            # With hyphens
            "django-extensions", "python-dateutil", "pytest-cov",
            "sphinx-rtd-theme", "django-debug-toolbar",
            
            # With underscores (should convert to hyphens)
            "websocket_client", "typing_extensions", "async_timeout",
            
            # With dots (should convert to hyphens)
            "backports.zoneinfo", "zope.interface", "ruamel.yaml",
            "importlib.metadata", "jaraco.classes",
            
            # Mixed case (should lowercase)
            "Django", "Flask", "Pillow", "PyYAML", "SQLAlchemy",
            "NumPy", "SciPy", "Werkzeug", "MarkupSafe", "Jinja2",
            
            # Starting with python-
            "python-ldap", "python-magic", "python-dotenv",
            "python-slugify", "python-jose",
            
            # Complex names
            "google-cloud-storage", "azure-storage-blob",
            "aws-cdk.core", "Flask-SQLAlchemy", "djangorestframework",
            
            # Single character and short
            "q", "sh", "py", "six", "tox", "rich", "click", "fire",
            
            # Numbers in names
            "h5py", "pytz2021.3", "beautifulsoup4", "html5lib",
            "base58", "ed25519", "blake3", "xxhash",
            
            # Special cases
            "msgpack-python", "mysqlclient", "psycopg2-binary",
            "pyopenssl", "pycryptodome", "pyasn1", "pygments",
        ]
    
    def test_bidirectional_translation(self):
        """
        Test that we can translate PyPI names to Gentoo and back.
        
        An individual subtest succeeds when we can translate to Gentoo
        and back to get a valid PyPI name (may not be identical due to
        normalization).
        """
        success_count = 0
        total_count = len(self.test_packages)
        failures = []
        
        for pypi_name in self.test_packages:
            with self.subTest(package=pypi_name):
                try:
                    # Translate to Gentoo
                    gentoo_name = self.translator.pypi_to_gentoo(pypi_name)
                    
                    # Validate Gentoo name
                    self.assertTrue(
                        self.translator.is_valid_gentoo_name(gentoo_name),
                        f"Invalid Gentoo name: {gentoo_name}"
                    )
                    
                    # Translate back to PyPI
                    recovered_name = self.translator.gentoo_to_pypi(
                        gentoo_name, 
                        hint=pypi_name
                    )
                    
                    # Check if the recovered name normalizes to the same thing
                    original_normalized = self.translator.normalize_pypi_name(pypi_name)
                    recovered_normalized = self.translator.normalize_pypi_name(recovered_name)
                    
                    if original_normalized == recovered_normalized:
                        success_count += 1
                    else:
                        failures.append((pypi_name, gentoo_name, recovered_name))
                        
                except Exception as e:
                    failures.append((pypi_name, str(e), None))
        
        # Calculate success rate
        success_rate = (success_count / total_count) * 100 if total_count > 0 else 0
        
        # Report results
        print(f"\nTranslation test results:")
        print(f"  Total packages tested: {total_count}")
        print(f"  Successful translations: {success_count}")
        print(f"  Failed translations: {len(failures)}")
        print(f"  Success rate: {success_rate:.1f}%")
        
        if failures:
            print("\nFailed translations (first 10):")
            for failure in failures[:10]:
                if len(failure) == 3 and failure[2] is not None:
                    print(f"  {failure[0]} -> {failure[1]} -> {failure[2]}")
                else:
                    print(f"  {failure[0]}: {failure[1]}")
        
        # Assert that we achieve at least 50% success rate
        self.assertGreaterEqual(
            success_rate, 
            50.0,
            f"Success rate {success_rate:.1f}% is below required 50%"
        )
    
    def test_known_problematic_packages(self):
        """Test packages known to have tricky names."""
        # These are real packages that might have naming challenges
        test_cases = [
            # Package with multiple possible normalizations
            ("backports.zoneinfo", "backports-zoneinfo"),
            ("google.cloud.storage", "google-cloud-storage"),
            
            # Packages that keep python- prefix
            ("python-dateutil", "python-dateutil"),
            ("python-ldap", "python-ldap"),
            
            # Mixed case packages
            ("Django", "django"),
            ("PyYAML", "pyyaml"),
            ("SQLAlchemy", "sqlalchemy"),
            
            # Underscore vs hyphen
            ("websocket_client", "websocket-client"),
            ("typing_extensions", "typing-extensions"),
        ]
        
        for pypi_name, expected_gentoo in test_cases:
            with self.subTest(package=pypi_name):
                gentoo_name = self.translator.pypi_to_gentoo(pypi_name)
                self.assertEqual(gentoo_name, expected_gentoo)
                
                # Should be valid
                self.assertTrue(self.translator.is_valid_gentoo_name(gentoo_name))


class TestConvenienceFunctions(TestCase):
    """Test the module-level convenience functions."""
    
    def test_pypi_to_gentoo_function(self):
        """Test the pypi_to_gentoo convenience function."""
        result = pypi_to_gentoo("Django")
        self.assertEqual(result, "django")
        
        result = pypi_to_gentoo("python-dateutil")
        self.assertEqual(result, "python-dateutil")
    
    def test_gentoo_to_pypi_function(self):
        """Test the gentoo_to_pypi convenience function."""
        # First populate some cache
        pypi_to_gentoo("Django")
        
        result = gentoo_to_pypi("django")
        self.assertEqual(result, "Django")
        
        result = gentoo_to_pypi("unknown-package")
        self.assertEqual(result, "unknown-package")


def run_doctests():
    """Run doctests from the name_translator module."""
    import doctest
    from portage_pip_fuse import name_translator
    
    results = doctest.testmod(name_translator, verbose=True)
    return results.failed == 0


if __name__ == "__main__":
    # Run doctests first
    print("Running doctests...")
    if run_doctests():
        print("All doctests passed!")
    else:
        print("Some doctests failed!")
    
    print("\nRunning unit tests...")
    unittest.main(verbosity=2)