"""
Tests for the FUSE filesystem implementation.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import pytest
from portage_pip_fuse.filesystem import PortagePipFS


class TestPortagePipFS:
    """Test the PortagePipFS class."""
    
    def test_init(self):
        """Test filesystem initialization."""
        fs = PortagePipFS()
        assert "/" in fs.dirs
        assert "/dev-python" in fs.dirs
        assert "/virtual" in fs.dirs
        assert "/metadata" in fs.dirs
        
    def test_getattr_directory(self):
        """Test getting attributes for a directory."""
        fs = PortagePipFS()
        attr = fs.getattr("/dev-python")
        assert attr['st_mode'] & 0o040000  # Check it's a directory
        
    def test_readdir_root(self):
        """Test reading root directory."""
        fs = PortagePipFS()
        entries = fs.readdir("/", None)
        assert "." in entries
        assert ".." in entries
        assert "dev-python" in entries
        assert "virtual" in entries
        assert "metadata" in entries
        
    def test_create_and_read_file(self):
        """Test creating and reading a file."""
        fs = PortagePipFS()
        test_path = "/test.txt"
        test_content = b"Hello, World!"
        
        # Create file
        fs.create(test_path, 0o644)
        
        # Write content
        fs.write(test_path, test_content, 0, None)
        
        # Read content
        content = fs.read(test_path, len(test_content), 0, None)
        assert content == test_content
        
    def test_mkdir_and_rmdir(self):
        """Test creating and removing directories."""
        fs = PortagePipFS()
        test_dir = "/test-dir"
        
        # Create directory
        fs.mkdir(test_dir, 0o755)
        assert test_dir in fs.dirs
        
        # Remove directory
        fs.rmdir(test_dir)
        assert test_dir not in fs.dirs