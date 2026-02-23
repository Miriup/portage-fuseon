"""
FUSE filesystem implementation for portage-pip adapter.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import errno
import logging
import os
from typing import Optional

from fuse import FUSE, FuseOSError, Operations

logger = logging.getLogger(__name__)


class PortagePipFS(Operations):
    """
    FUSE filesystem that provides a virtual interface between pip and portage.
    
    This filesystem presents PyPI packages as if they were portage ebuilds,
    allowing transparent access to Python packages through Gentoo's package
    management system.
    """
    
    def __init__(self, root: str = "/"):
        """
        Initialize the FUSE filesystem.
        
        Args:
            root: Root directory for the filesystem operations
        """
        self.root = root
        self.files = {}  # Virtual file storage
        self.dirs = {"/"}  # Virtual directory structure
        
        # Initialize with some basic structure
        self._init_filesystem()
        
    def _init_filesystem(self):
        """Initialize the basic filesystem structure."""
        # Create category directories similar to portage
        self.dirs.update({
            "/dev-python",  # Python packages category
            "/virtual",     # Virtual packages
            "/metadata",    # Metadata directory
        })
        
    def getattr(self, path, fh=None):
        """Get file attributes."""
        if path in self.dirs:
            # Directory attributes
            return {
                'st_mode': 0o755 | 0o040000,  # Directory with rwxr-xr-x
                'st_nlink': 2,
                'st_size': 4096,
                'st_ctime': 0,
                'st_mtime': 0,
                'st_atime': 0,
                'st_uid': os.getuid(),
                'st_gid': os.getgid(),
            }
        elif path in self.files:
            # File attributes
            content = self.files[path]
            return {
                'st_mode': 0o644 | 0o100000,  # File with rw-r--r--
                'st_nlink': 1,
                'st_size': len(content),
                'st_ctime': 0,
                'st_mtime': 0,
                'st_atime': 0,
                'st_uid': os.getuid(),
                'st_gid': os.getgid(),
            }
        else:
            raise FuseOSError(errno.ENOENT)
            
    def readdir(self, path, fh):
        """Read directory contents."""
        if path not in self.dirs:
            raise FuseOSError(errno.ENOTDIR)
            
        # Basic directory entries
        entries = ['.', '..']
        
        # Add subdirectories
        for dir_path in self.dirs:
            if dir_path != path and dir_path.startswith(path):
                # Get relative name
                rel_path = dir_path[len(path):].lstrip('/')
                if '/' not in rel_path and rel_path:
                    entries.append(rel_path)
                    
        # Add files in this directory
        for file_path in self.files:
            if file_path.startswith(path):
                rel_path = file_path[len(path):].lstrip('/')
                if '/' not in rel_path and rel_path:
                    entries.append(rel_path)
                    
        return entries
        
    def read(self, path, length, offset, fh):
        """Read file contents."""
        if path not in self.files:
            raise FuseOSError(errno.ENOENT)
            
        content = self.files[path]
        return content[offset:offset + length]
        
    def open(self, path, flags):
        """Open a file."""
        if path not in self.files:
            raise FuseOSError(errno.ENOENT)
        return 0
        
    def create(self, path, mode, fi=None):
        """Create a new file."""
        self.files[path] = b""
        return 0
        
    def write(self, path, data, offset, fh):
        """Write to a file."""
        if path not in self.files:
            raise FuseOSError(errno.ENOENT)
            
        # Convert data to bytes if needed
        if isinstance(data, str):
            data = data.encode('utf-8')
            
        # Update file content
        content = self.files.get(path, b"")
        self.files[path] = content[:offset] + data
        return len(data)
        
    def truncate(self, path, length, fh=None):
        """Truncate a file."""
        if path in self.files:
            self.files[path] = self.files[path][:length]
        return 0
        
    def unlink(self, path):
        """Remove a file."""
        if path in self.files:
            del self.files[path]
        else:
            raise FuseOSError(errno.ENOENT)
        return 0
        
    def mkdir(self, path, mode):
        """Create a directory."""
        self.dirs.add(path)
        return 0
        
    def rmdir(self, path):
        """Remove a directory."""
        if path in self.dirs:
            # Check if directory is empty
            for item_path in list(self.dirs) + list(self.files.keys()):
                if item_path != path and item_path.startswith(path + '/'):
                    raise FuseOSError(errno.ENOTEMPTY)
            self.dirs.remove(path)
        else:
            raise FuseOSError(errno.ENOENT)
        return 0


def mount_filesystem(mountpoint: str, foreground: bool = False, debug: bool = False):
    """
    Mount the portage-pip FUSE filesystem.
    
    Args:
        mountpoint: Path where the filesystem should be mounted
        foreground: Run in foreground instead of daemonizing
        debug: Enable debug output
    """
    if debug:
        logging.basicConfig(level=logging.DEBUG)
        
    fs = PortagePipFS()
    FUSE(fs, mountpoint, nothreads=True, foreground=foreground, debug=debug)