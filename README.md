# portage-pip-fuse

A FUSE-based filesystem adapter that bridges Python's pip package installer and Gentoo's portage package manager.

## Overview

This project provides a virtual filesystem interface that transparently translates between pip (PyPI) packages and portage ebuilds, enabling seamless integration between Python's package ecosystem and Gentoo Linux's package management system.

## Features

- FUSE-based virtual filesystem
- Real-time translation between pip and portage package formats
- Transparent access to PyPI packages through portage
- Automatic dependency resolution and mapping

## Requirements

- Python >= 3.8
- FUSE support in kernel
- fusepy >= 3.0.1
- Gentoo Linux with portage

## Installation

```bash
pip install portage-pip-fuse
```

Or for development:

```bash
git clone https://github.com/Miriup/portage-pip-fuse
cd portage-pip-fuse
pip install -e .
```

## Usage

```bash
# Mount the FUSE filesystem
portage-pip-fuse /mnt/pip-portage

# Access PyPI packages as ebuilds
ls /mnt/pip-portage/

# Unmount when done
fusermount -u /mnt/pip-portage
```

## Development

```bash
# Install development dependencies
pip install -e .[dev]

# Run tests
pytest

# Format code
black portage_pip_fuse
isort portage_pip_fuse
```

## License

GPL-2.0 - See LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit pull requests.
