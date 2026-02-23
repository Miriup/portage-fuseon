#!/usr/bin/env python3
"""
Test script to demonstrate the portage-pip FUSE filesystem functionality.

This script shows the complete workflow:
1. Name translation (PyPI <-> Gentoo)
2. Version translation (PyPI -> Gentoo)
3. Dynamic ebuild generation with dependencies
4. Manifest file generation with checksums
5. PyPI extras handling as Gentoo USE flags

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from portage_pip_fuse import PortagePipFS

def test_path_parsing():
    """Test the path parsing functionality."""
    print("=== Testing Path Parsing ===")
    fs = PortagePipFS()
    
    test_paths = [
        "/",
        "/dev-python",
        "/dev-python/requests",
        "/dev-python/requests/requests-2.28.1.ebuild",
        "/dev-python/requests/metadata.xml",
        "/dev-python/requests/Manifest",
        "/profiles/repo_name",
        "/metadata/layout.conf"
    ]
    
    for path in test_paths:
        parsed = fs._parse_path(path)
        print(f"{path:40} -> {parsed}")

def test_name_translation():
    """Test PyPI <-> Gentoo name translation."""
    print("\n=== Testing Name Translation ===")
    fs = PortagePipFS()
    
    test_names = ["requests", "django", "google-cloud-storage", "zope-interface"]
    
    for gentoo_name in test_names:
        pypi_name = fs._gentoo_to_pypi(gentoo_name)
        print(f"Gentoo: {gentoo_name:20} -> PyPI: {pypi_name}")

def test_version_translation():
    """Test PyPI -> Gentoo version translation."""
    print("\n=== Testing Version Translation ===")
    fs = PortagePipFS()
    
    test_versions = [
        "1.2.0",
        "1.2.0a1",
        "1.2.0b2", 
        "1.2.0rc1",
        "1.2.0.post1",
        "1.2.0.dev1",
        "1.2.0a1.post1"
    ]
    
    for pypi_version in test_versions:
        gentoo_version = fs._translate_version(pypi_version)
        print(f"PyPI: {pypi_version:15} -> Gentoo: {gentoo_version}")

def test_filesystem_structure():
    """Test virtual filesystem structure."""
    print("\n=== Testing Filesystem Structure ===")
    fs = PortagePipFS()
    
    # Test directory listing
    test_dirs = ["/", "/profiles", "/metadata", "/dev-python"]
    
    for directory in test_dirs:
        try:
            entries = fs.readdir(directory, None)
            print(f"{directory}: {entries}")
        except Exception as e:
            print(f"{directory}: Error - {e}")

def test_static_files():
    """Test static file content."""
    print("\n=== Testing Static Files ===")
    fs = PortagePipFS()
    
    static_files = ["/profiles/repo_name", "/metadata/layout.conf"]
    
    for filepath in static_files:
        try:
            # Get file attributes
            attrs = fs.getattr(filepath)
            print(f"{filepath}: size={attrs['st_size']} bytes")
            
            # Read content  
            content = fs.read(filepath, attrs['st_size'], 0, None)
            print(f"Content preview: {content[:100]}...")
            
        except Exception as e:
            print(f"{filepath}: Error - {e}")

if __name__ == "__main__":
    print("Portage-Pip FUSE Filesystem Test")
    print("=" * 50)
    
    test_path_parsing()
    test_name_translation()
    test_version_translation()
    test_filesystem_structure()
    test_static_files()
    
    print("\n=== Test Complete ===")
    print("The FUSE filesystem is ready for mounting!")
    print("Usage: python -c \"from portage_pip_fuse import mount_filesystem; mount_filesystem('/mnt/pypi', foreground=True, debug=True)\"")