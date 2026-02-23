#!/usr/bin/env python3
"""
Command-line interface for portage-pip-fuse.

Copyright (C) 2026 Dirk Tilger <dirk@systemication.com>
Licensed under GPL-2.0
"""

import argparse
import logging
import os
import sys

from portage_pip_fuse.filesystem import mount_filesystem


def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description="FUSE filesystem adapter between pip and portage"
    )
    parser.add_argument(
        "mountpoint",
        help="Mount point for the FUSE filesystem"
    )
    parser.add_argument(
        "-f", "--foreground",
        action="store_true",
        help="Run in foreground (don't daemonize)"
    )
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Enable debug output"
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version="%(prog)s 0.1.0"
    )
    
    args = parser.parse_args()
    
    # Validate mountpoint
    if not os.path.exists(args.mountpoint):
        print(f"Error: Mountpoint '{args.mountpoint}' does not exist", file=sys.stderr)
        sys.exit(1)
        
    if not os.path.isdir(args.mountpoint):
        print(f"Error: Mountpoint '{args.mountpoint}' is not a directory", file=sys.stderr)
        sys.exit(1)
        
    # Check if mountpoint is empty
    if os.listdir(args.mountpoint):
        print(f"Warning: Mountpoint '{args.mountpoint}' is not empty", file=sys.stderr)
        
    try:
        print(f"Mounting portage-pip-fuse at {args.mountpoint}...")
        mount_filesystem(
            mountpoint=args.mountpoint,
            foreground=args.foreground,
            debug=args.debug
        )
    except KeyboardInterrupt:
        print("\nUnmounting...")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()