# portage-fuseon

A FUSE-based filesystem that bridges package managers (PyPI, RubyGems) with Gentoo portage, enabling direct installation via `emerge`.

## Overview

This project provides virtual filesystems that dynamically generate Gentoo ebuilds from upstream package metadata. When mounted, they appear as standard portage overlays containing all compatible packages, allowing seamless integration between language-specific package ecosystems and Gentoo's package management.

## Supported Ecosystems

| Ecosystem | CLI Command | Default Category | Overlay Path | Packages |
|-----------|-------------|------------------|--------------|----------|
| PyPI | `portage-pypi-fuse` | `dev-python` | `/var/db/repos/pypi` | ~746k |
| RubyGems | `portage-gem-fuse` | `dev-ruby` | `/var/db/repos/rubygems` | ~190k |

The legacy `portage-pip-fuse` command remains available for backwards compatibility.

## Features

### Core Features (All Ecosystems)
- **FUSE virtual overlay**: Presents upstream repositories as portage-compatible overlays
- **Dynamic ebuild generation**: Creates ebuilds on-the-fly from upstream metadata
- **Automatic name translation**: Converts between upstream and Gentoo naming conventions
- **Dependency mapping**: Translates dependencies to Gentoo atoms with version constraints
- **Manifest generation**: Creates Manifest files with checksums from upstream
- **Runtime patching**: Modify dependencies, compatibility, USE flags via `.sys/` virtual filesystem

### PyPI Features
- **pip command translation**: Run `pip install` and have it translated to `emerge`
- **SQLite metadata backend**: Uses bulk PyPI database (~1GB) for fast lookups
- **Python compatibility filtering**: Only shows packages compatible with system PYTHON_TARGETS
- **Source distribution filtering**: Only shows packages with source tarballs or git repositories
- **PEP517 backend detection**: Auto-detects and configures build systems

### RubyGems Features
- **gem/bundle command translation**: Run `gem install` or `bundle install` → `emerge`
- **Ruby compatibility filtering**: Only shows gems compatible with system USE_RUBY
- **Gemfile.lock parsing**: Parse and install entire project dependencies
- **Platform-to-KEYWORDS mapping**: Platform-specific gems get appropriate KEYWORDS
- **Dynamic Ruby target detection**: Reads implementations from `ruby-utils.eclass`

## Quick Start

### PyPI

```bash
# Create repos.conf entry and sync metadata
sudo portage-pypi-fuse install
portage-pypi-fuse sync  # Downloads ~1GB database

# Mount and use
portage-pypi-fuse mount
emerge -av dev-python/requests

# Or translate pip commands directly
portage-pypi-fuse pip install flask django
```

### RubyGems

```bash
# Create repos.conf entry
sudo portage-gem-fuse install

# Mount and use
portage-gem-fuse mount
emerge -av dev-ruby/rails

# Or translate gem/bundle commands
portage-gem-fuse gem install nokogiri puma
portage-gem-fuse bundle install  # From Gemfile.lock
```

## Requirements

- Python >= 3.8
- FUSE support in kernel (`modprobe fuse`)
- fusepy >= 3.0.1
- Gentoo Linux with portage
- ~1GB disk space for PyPI metadata cache (PyPI only)

## Installation

```bash
# Install from source
git clone https://github.com/Miriup/portage-pip-fuse
cd portage-pip-fuse
pip install -e .

# Create repository configs
sudo portage-pypi-fuse install   # For PyPI
sudo portage-gem-fuse install    # For RubyGems
```

## Usage

### Common Commands

Both ecosystems share the same command structure:

```bash
# Mount the FUSE filesystem
portage-{pypi,gem}-fuse mount [mountpoint] [options]

# Unmount the filesystem
portage-{pypi,gem}-fuse unmount [mountpoint]

# Create /etc/portage/repos.conf entry
portage-{pypi,gem}-fuse install [mountpoint] [--priority N]
```

### Mount Options

```bash
portage-pypi-fuse mount [mountpoint] [options]

Options:
  -f, --foreground     Run in foreground (default: daemonize)
  -d, --debug          Enable debug logging
  --logfile PATH       Log to file instead of stderr
  --cache-dir DIR      Cache directory for metadata
  --cache-ttl SEC      Cache TTL in seconds (default: 3600)
  --pid-file PATH      Write PID file for unmounting
  --timestamps         Enable PyPI upload timestamps (slower)
  --no-sqlite          Disable SQLite backend, use JSON API only (PyPI)
```

### pip Command (PyPI)

```bash
# Basic installation
portage-pypi-fuse pip install requests flask
# → emerge --ask dev-python/requests dev-python/flask

# With version constraints
portage-pypi-fuse pip install "django>=4.0" "celery~=5.3.0"
# → emerge --ask >=dev-python/django-4.0 >=dev-python/celery-5.3.0

# From requirements file (creates portage set)
portage-pypi-fuse pip install -r requirements.txt
# → Creates /etc/portage/sets/{project}-dependencies
# → emerge --ask @{project}-dependencies
```

### gem Command (RubyGems)

```bash
# Basic installation
portage-gem-fuse gem install rails nokogiri
# → emerge --ask dev-ruby/rails dev-ruby/nokogiri

# With version
portage-gem-fuse gem install rails -v 7.0.0
# → emerge --ask =dev-ruby/rails-7.0.0

# Dry run
portage-gem-fuse gem install --dry-run rails
```

### bundle Command (RubyGems)

```bash
# Install from Gemfile.lock
cd ~/src/myproject
portage-gem-fuse bundle install
# → Creates /etc/portage/sets/myproject-gems
# → emerge @myproject-gems
```

### debug Command (RubyGems)

```bash
# Show available versions
portage-gem-fuse debug versions faraday
portage-gem-fuse debug versions faraday --platforms

# Show gem metadata
portage-gem-fuse debug info rails
portage-gem-fuse debug info rails --version 7.0.0 --json

# Show name translation
portage-gem-fuse debug translate iso-639

# Show version filtering
portage-gem-fuse debug filter nokogiri --use-ruby "ruby32 ruby33"

# Show dependencies
portage-gem-fuse debug deps rails
```

## Patching and Customization

The `.sys/` virtual filesystem allows runtime modification of generated ebuilds:

| Directory | Purpose |
|-----------|---------|
| `.sys/RDEPEND/` | Modify runtime dependencies (RDEPEND) |
| `.sys/DEPEND/` | Add build-time dependencies (DEPEND) |
| `.sys/python-compat/` | Adjust Python version compatibility (PyPI) |
| `.sys/ruby-compat/` | Adjust Ruby version compatibility (RubyGems) |
| `.sys/iuse/` | Add/remove USE flags |
| `.sys/ebuild-append/` | Add custom phase functions |
| `.sys/pep517/` | Override DISTUTILS_USE_PEP517 backend (PyPI) |
| `.sys/slot/` | Override SLOT value |
| `.sys/git-source/` | Configure git repository sources |

### Quick Examples

```bash
# Remove incompatible Python version
echo '-- python3_13' > /var/db/repos/pypi/.sys/python-compat-patch/dev-python/oldpkg/_all.patch

# Remove incompatible Ruby version
echo '-- ruby34' > /var/db/repos/rubygems/.sys/ruby-compat-patch/dev-ruby/oldgem/_all.patch

# Add missing dependency
touch '/var/db/repos/pypi/.sys/RDEPEND/dev-python/broken-pkg/_all/>=dev-python::missing-1.0'

# Fix PEP517 backend mismatch
echo 'flit' > /var/db/repos/pypi/.sys/pep517/dev-python/pypdf/_all

# Add custom src_configure
echo 'export MY_VAR=1' > /var/db/repos/pypi/.sys/ebuild-append/dev-python/pkg/_all/src_configure
```

See [docs/build-error-fixes.md](docs/build-error-fixes.md) for comprehensive examples.

## Documentation

- [docs/installation.md](docs/installation.md) - Detailed setup instructions
- [docs/filtering.md](docs/filtering.md) - Package filtering system
- [docs/dependency-patching.md](docs/dependency-patching.md) - Runtime dependency modification
- [docs/rubygems.md](docs/rubygems.md) - RubyGems ecosystem documentation
- [docs/caching-architecture.md](docs/caching-architecture.md) - Two-level caching system
- [docs/build-error-fixes.md](docs/build-error-fixes.md) - Troubleshooting guide

## How It Works

### PyPI

1. **Metadata Backend**: Downloads a ~1GB SQLite database containing metadata for all PyPI packages (updated daily by [pypi-data](https://github.com/pypi-data/pypi-json-data))
2. **Package Filtering**: Only packages with source distributions AND compatible with your PYTHON_TARGETS are shown
3. **Ebuild Generation**: Creates ebuilds using `distutils-r1.eclass` with proper PYTHON_COMPAT and dependencies

### RubyGems

1. **Metadata Provider**: Fetches gem metadata on-demand from RubyGems.org API with disk caching
2. **Package Filtering**: Only gem versions compatible with your RUBY_TARGETS (from USE_RUBY) are shown
3. **Ebuild Generation**: Creates ebuilds using `ruby-fakegem.eclass` with proper USE_RUBY and dependencies

### Name Translation

Both ecosystems automatically convert between naming conventions:

**PyPI:**
- `Pillow` → `pillow`
- `scikit-learn` → `scikit-learn`
- `ruamel.yaml` → `ruamel-yaml`

**RubyGems:**
- `active_support` → `activesupport` (known mapping)
- `iso-639` → `iso_639` (trailing digits fix)
- `http-2` → `http_2` (distinct from `http2`)

## Repository Structure

When mounted, the filesystems provide:

```
/var/db/repos/pypi/                    /var/db/repos/rubygems/
├── dev-python/                        ├── dev-ruby/
│   ├── requests/                      │   ├── rails/
│   │   ├── requests-2.31.0.ebuild     │   │   ├── rails-7.0.0.ebuild
│   │   ├── metadata.xml               │   │   ├── metadata.xml
│   │   └── Manifest                   │   │   └── Manifest
│   └── ...                            │   └── ...
├── .sys/                              ├── .sys/
│   ├── RDEPEND/                       │   ├── RDEPEND/
│   ├── python-compat/                 │   ├── ruby-compat/
│   └── ...                            │   └── ...
├── metadata/                          ├── metadata/
│   └── layout.conf                    │   └── layout.conf
└── profiles/                          └── profiles/
    └── repo_name                          └── repo_name
```

## Troubleshooting

### "Repository is missing masters attribute"

This warning is harmless. The FUSE filesystem provides `layout.conf` with `masters = gentoo`.

### Slow package listings

The first access may be slow as metadata is fetched. Subsequent accesses use the cache. For PyPI, use `--no-timestamps` for faster directory listings.

### PyPI database sync fails

```bash
# Check disk space (needs ~12GB total)
df -h ~/.cache/portage-pip-fuse

# Retry with debug output
portage-pypi-fuse sync --debug
```

### RubyGems version not available

```bash
# Check if gem exists and why versions are filtered
portage-gem-fuse debug versions gemname --platforms
portage-gem-fuse debug filter gemname
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

## Official Transliterations

- Cyrillic: **Портидж Фюжн** — because unlike a certain German techno festival at a former Soviet airfield, we transliterate what we actually say, not what we spell.
- Arabic: **بورتيدج فيوجن**

## License

GPL-2.0 - See LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit pull requests.

## Related Projects

- [g-sorcery](https://github.com/jauhien/g-sorcery) - Framework for ebuild generators
- [gs-pypi](https://github.com/jauhien/gs-pypi) - PyPI backend for g-sorcery
- [pypi-data](https://github.com/pypi-data/pypi-json-data) - Daily PyPI metadata dumps
