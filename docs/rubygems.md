# RubyGems Ecosystem Support

This document covers the RubyGems ecosystem plugin for portage-fuseon, enabling installation of Ruby gems through Gentoo's portage using `ruby-fakegem.eclass`.

## Overview

The RubyGems plugin provides:

- **FUSE virtual overlay**: Presents RubyGems.org as a portage-compatible repository
- **Command translation**: Run `gem install` or `bundle install` and have them translated to `emerge`
- **Ruby compatibility filtering**: Only shows gems compatible with your system's RUBY_TARGETS
- **Dynamic ebuild generation**: Creates ebuilds on-the-fly from RubyGems API metadata
- **Automatic name translation**: Converts between gem names and Gentoo package naming conventions
- **Dependency mapping**: Translates gem dependencies to Gentoo atoms with proper version constraints
- **Manifest generation**: Creates Manifest files with SHA256 checksums from RubyGems
- **Runtime patching**: Modify dependencies, Ruby compatibility, and ebuild phases via `.sys/` virtual filesystem

## Quick Start

```bash
# Mount the RubyGems overlay
portage-gem-fuse mount

# Install a gem via emerge
emerge -av dev-ruby/rails

# Or translate gem install syntax
portage-gem-fuse gem install rails nokogiri

# Install from Gemfile.lock
cd ~/myproject
portage-gem-fuse bundle install

# Unmount when done
portage-gem-fuse unmount
```

## CLI Commands

### mount

Mount the FUSE filesystem:

```bash
portage-gem-fuse mount [mountpoint] [options]

Options:
  -f, --foreground     Run in foreground (default: daemonize)
  -d, --debug          Enable debug logging
  --logfile PATH       Log to file instead of stderr
  --cache-dir DIR      Cache directory for metadata
  --cache-ttl SEC      Cache TTL in seconds (default: 3600)
  --pid-file PATH      Write PID file for unmounting
  --use-ruby FLAGS     Override USE_RUBY (e.g., "ruby32 ruby33")
```

### gem

Translate `gem install` commands to `emerge`:

```bash
# Basic install
portage-gem-fuse gem install rails
# → emerge --ask dev-ruby/rails

# Multiple gems
portage-gem-fuse gem install rails nokogiri puma
# → emerge --ask dev-ruby/rails dev-ruby/nokogiri dev-ruby/puma

# With version
portage-gem-fuse gem install rails -v 7.0.0
# → emerge --ask =dev-ruby/rails-7.0.0

# Dry run
portage-gem-fuse gem install --dry-run rails
# Shows: Would run: emerge --ask dev-ruby/rails
```

### bundle

Parse `Gemfile.lock` and install all dependencies:

```bash
# From project directory
cd ~/src/myproject
portage-gem-fuse bundle install

# Creates: /etc/portage/sets/myproject-gems
# Runs: emerge @myproject-gems

# Dry run to see what would be installed
portage-gem-fuse bundle install --dry-run
```

### debug

Inspect gem metadata and troubleshoot issues:

```bash
# Show available versions (semantically sorted, newest first)
portage-gem-fuse debug versions rails
portage-gem-fuse debug versions faraday --platforms  # Show platform info

# Show gem metadata
portage-gem-fuse debug info rails
portage-gem-fuse debug info rails --version 7.0.0  # Specific version
portage-gem-fuse debug info rails --json           # JSON output

# Show name translation
portage-gem-fuse debug translate iso-639
# Output: Gem 'iso-639' → Gentoo 'iso_639'

# Show version filtering
portage-gem-fuse debug filter nokogiri
portage-gem-fuse debug filter nokogiri --use-ruby "ruby32 ruby33"

# Show dependencies with Gentoo names
portage-gem-fuse debug deps rails
```

## Ebuild Generation

The plugin generates ebuilds using `ruby-fakegem.eclass`, which is Gentoo's standard for Ruby packages.

### Example Generated Ebuild

```bash
# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

USE_RUBY="ruby32 ruby33 ruby34"
RUBY_FAKEGEM_RECIPE_TEST="none"
RUBY_FAKEGEM_RECIPE_DOC="none"
RUBY_FAKEGEM_BINWRAP=""

inherit ruby-fakegem

DESCRIPTION="Full-stack web framework optimized for programmer happiness"
HOMEPAGE="https://rubyonrails.org"
SRC_URI="https://rubygems.org/gems/${PN}-${PV}.gem"

LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64 ~arm64"

RDEPEND="
	>=dev-ruby/activesupport-7.0.0 <dev-ruby/activesupport-8
	>=dev-ruby/actionpack-7.0.0 <dev-ruby/actionpack-8
	>=dev-ruby/railties-7.0.0 <dev-ruby/railties-8
"
```

### Key Differences from PyPI Ebuilds

| Aspect | RubyGems | PyPI |
|--------|----------|------|
| Compatibility variable | `USE_RUBY="ruby32 ruby33"` | `PYTHON_COMPAT=(python3_{11,12})` |
| Primary eclass | `ruby-fakegem` | `distutils-r1` |
| Dependency helpers | Direct atoms in RDEPEND | Atoms with `[${PYTHON_USEDEP}]` |
| Source format | `.gem` archive | sdist / wheel |
| Version constraint | `~> 2.1` (pessimistic) | `~= 2.1` (compatible) |

## Version Constraint Translation

Ruby's version constraints are translated to Gentoo atoms:

| Ruby Constraint | Gentoo Atoms |
|-----------------|--------------|
| `~> 2.1.3` | `>=dev-ruby/pkg-2.1.3 <dev-ruby/pkg-2.2` |
| `~> 2.1` | `>=dev-ruby/pkg-2.1 <dev-ruby/pkg-3` |
| `>= 1.0, < 2.0` | `>=dev-ruby/pkg-1.0 <dev-ruby/pkg-2.0` |
| `= 1.0.0` | `~dev-ruby/pkg-1.0.0` (matches -r* revisions) |
| `!= 1.5.0` | `!=dev-ruby/pkg-1.5.0` |

### Pre-release Version Translation

| Gem Version | Gentoo Version |
|-------------|----------------|
| `1.0.0.alpha1` | `1.0.0_alpha1` |
| `2.0.0.beta2` | `2.0.0_beta2` |
| `3.0.0.rc1` | `3.0.0_rc1` |
| `5.a` | `5_alpha` |
| `5.b` | `5_beta` |
| `5.0.0.alpha.pre.4` | `5.0.0_alpha_pre_p4` |

## Name Translation

Gem names are used **exactly as specified** in RubyGems, with minimal transformations for PMS compatibility.

### Translation Rules

1. **Underscores preserved**: Valid per PMS 3.1.2, distinguishes different gems
   - `devise-secure_password` → `dev-ruby/devise-secure_password`
   - `devise-secure-password` → `dev-ruby/devise-secure-password`

2. **Trailing digit hyphen fix**: Names ending in `-NUMBER` conflict with version parsing
   - `iso-639` → `dev-ruby/iso_639`
   - `http-2` → `dev-ruby/http_2` (distinct from `http2` → `dev-ruby/http2`)

3. **Known mappings**: Rails ecosystem gems have built-in mappings
   - `active_support` → `activesupport`
   - `active_record` → `activerecord`

4. **Gentoo metadata.xml**: Mappings extracted from existing Gentoo packages

### No Heuristic Matching

Unlike earlier versions, we do NOT try to match `ruby-foo` to `foo` or vice versa. Each gem gets its own package name. This prevents subtle bugs from incorrect guessing.

**Handling mismatches**: When a gem name differs from an existing Gentoo package, use the `.sys/name-translation/` mechanism to configure explicit mappings.

## Version Filtering

The following filters are applied to determine which gem versions are visible:

| Filter | Description | Default |
|--------|-------------|---------|
| `ruby-compat` | Filters by `required_ruby_version` against system USE_RUBY | Enabled |
| `gem-source` | Filters to gems with `.gem` files or git repositories | Enabled |
| `gentoo-version` | Filters versions that can't be translated to PMS format | Enabled |
| `platform` | Maps platform-specific gems to appropriate KEYWORDS | N/A |
| `pre-release` | Excludes alpha/beta/rc versions | Disabled |

### Ruby Compatibility Detection

The filter automatically detects your system's RUBY_TARGETS from:

1. `RUBY_TARGETS` environment variable
2. Portage API (`portage.settings`)
3. `/etc/portage/make.conf`
4. Profile defaults via `emerge --info`

Available Ruby implementations are read dynamically from `ruby-utils.eclass`.

## Platform Handling

Platform-specific gems are **not filtered out**. Instead, they receive appropriate KEYWORDS:

| RubyGems Platform | Gentoo KEYWORDS |
|-------------------|-----------------|
| `ruby` (pure) | `~amd64 ~arm64` |
| `x86_64-linux*` | `~amd64` |
| `arm64-linux*` / `aarch64-linux*` | `~arm64` |
| `x86-linux*` / `i686-linux*` | `~x86` |
| `universal-darwin*` | `~x64-macos ~arm64-macos` |
| `x86_64-darwin*` | `~x64-macos` |
| `arm64-darwin*` | `~arm64-macos` |
| `java` / `jruby` | Empty (visible, not installable) |
| `mswin*` / `mingw*` | Empty (visible, not installable) |

This provides better user experience: instead of cryptic "checksum verification" errors, users see clear "no KEYWORDS for your architecture" messages.

## Patching and Customization

The `.sys/` virtual filesystem allows runtime modification of generated ebuilds.

### Available Patch Directories

| Directory | Purpose |
|-----------|---------|
| `.sys/RDEPEND/` | Modify runtime dependencies |
| `.sys/DEPEND/` | Add build-time dependencies |
| `.sys/ruby-compat/` | Adjust Ruby version compatibility (USE_RUBY) |
| `.sys/ruby-compat-patch/` | Direct patch files for USE_RUBY |
| `.sys/slot/` | Override SLOT value |
| `.sys/git-source/` | Configure git repository sources |

### Example: Fix Ruby Compatibility

```bash
# Remove Ruby 3.4 from a gem that doesn't support it yet
echo '-- ruby34' > /var/db/repos/rubygems/.sys/ruby-compat-patch/dev-ruby/oldgem/_all.patch

# Set explicit compatible versions
echo '== ruby32 ruby33' > /var/db/repos/rubygems/.sys/ruby-compat-patch/dev-ruby/specific-gem/1.0.0.patch
```

### Example: Fix Dependency Conflict

```bash
# Loosen exact version to minimum
cat > /var/db/repos/rubygems/.sys/RDEPEND-patch/dev-ruby/mygem/1.0.0.patch << 'EOF'
# Allow newer rack versions
-> ~dev-ruby/rack-2.0.0 >=dev-ruby/rack-2.0.0
EOF
```

### Example: Add Missing Dependency

```bash
# Add runtime dependency
touch '/var/db/repos/rubygems/.sys/RDEPEND/dev-ruby/mygem/_all/dev-ruby::missing-dep'

# Add build-time dependency
touch '/var/db/repos/rubygems/.sys/DEPEND/dev-ruby/mygem/_all/dev-libs::libfoo'
```

### Patch File Format

```
# Comments start with #
-> old_dep new_dep    # Modify dependency
-- dep_to_remove      # Remove dependency
++ new_dep            # Add dependency
== ruby32 ruby33      # Set explicit list (for ruby-compat)
```

## Gemfile.lock Support

The bundle command parses Gemfile.lock files with full support for:

- **GEM**: Gems from RubyGems.org
- **GIT**: Gems from git repositories (creates git-r3 ebuilds)
- **PATH**: Local gems (skipped with warning)
- **PLATFORMS**: Target platforms
- **DEPENDENCIES**: Direct dependencies
- **RUBY VERSION**: Ruby version constraint
- **BUNDLED WITH**: Bundler version

### Usage

```bash
cd ~/src/myproject
portage-gem-fuse bundle install

# Creates a portage set at /etc/portage/sets/myproject-gems
# Then runs: emerge @myproject-gems
```

### Programmatic Access

```python
from portage_pip_fuse.ecosystems.rubygems.gemfile_parser import (
    parse_gemfile_lock,
    parse_gemfile_lock_full,
)

# Simple interface - list of gems
gems = parse_gemfile_lock('/path/to/Gemfile.lock')
for gem in gems:
    print(f"{gem.name} {gem.version} ({gem.source_type})")

# Full data including platforms, direct dependencies
data = parse_gemfile_lock_full('/path/to/Gemfile.lock')
print(f"Direct dependencies: {data.direct_dependencies}")
print(f"Bundler version: {data.bundled_with}")
```

## Known Issues and Workarounds

### Gems with Missing License

Some gems don't properly declare their license in the gemspec. Add to `/etc/portage/package.license`:

```
# ai-agents gem missing license declaration
dev-ruby/ai-agents unknown

# selectize-rails has malformed license field
dev-ruby/selectize-rails MIT,\ Apache\ License\ v2.0
```

### Pre-release Gems

Pre-release gem versions (alpha, beta, rc) get empty KEYWORDS and require explicit keywording:

```bash
# Accept pre-release version
echo "~dev-ruby/rails-8.0.0_beta1" >> /etc/portage/package.accept_keywords/rubygems
```

### JRuby-Only Gems

Gems that only work with JRuby (like `jruby-openssl`) are visible but have empty KEYWORDS, making them uninstallable on CRuby systems.

## Troubleshooting

### "No ebuilds to satisfy"

1. Check if the gem exists:
   ```bash
   portage-gem-fuse debug info gemname
   ```

2. Check if versions pass filters:
   ```bash
   portage-gem-fuse debug filter gemname
   ```

3. Check name translation:
   ```bash
   portage-gem-fuse debug translate gemname
   ```

### Version Not Available

Use the debug command to see all versions and why they're filtered:

```bash
portage-gem-fuse debug versions nokogiri --platforms
portage-gem-fuse debug filter nokogiri
```

### Build Fails with Ruby Incompatibility

Check if the gem's `required_ruby_version` is compatible:

```bash
portage-gem-fuse debug info problematic-gem --version X.Y.Z
```

If needed, patch the USE_RUBY:

```bash
echo '== ruby32 ruby33' > /var/db/repos/rubygems/.sys/ruby-compat-patch/dev-ruby/problematic-gem/_all.patch
```

### Dependency Conflict with Gentoo Package

If a gem dependency conflicts with a system package version:

```bash
cat > /var/db/repos/rubygems/.sys/RDEPEND-patch/dev-ruby/mygem/1.0.0.patch << 'EOF'
# Allow gentoo's newer version
-> ~dev-ruby/dep-1.0.0 >=dev-ruby/dep-1.0.0
EOF
```

## Architecture

### Plugin System

The RubyGems ecosystem is implemented as a plugin that shares ~70% of the codebase with PyPI:

- **Shared**: FUSE mechanics, caching infrastructure, CLI framework, patching system
- **RubyGems-specific**: Metadata provider, ebuild generator, name translator, filters

### Code Structure

```
ecosystems/rubygems/
├── plugin.py         # RubyGemsPlugin, metadata provider, ebuild generator
├── name_translator.py # Gem to Gentoo name translation
├── source_provider.py # GemSourceProvider, RubyGitProvider
├── filters.py        # RubyCompatFilter, PlatformFilter, etc.
├── ruby_targets.py   # Dynamic Ruby target detection
├── cli.py            # gem_command(), bundle_command()
├── gemfile_parser.py # Gemfile.lock parsing
├── filesystem.py     # RubyGems-specific FUSE operations
└── ruby_compat_patch.py # USE_RUBY patching
```

### Metadata Flow

```
User accesses /var/db/repos/rubygems/dev-ruby/rails/
    ↓
FUSE filesystem intercepts
    ↓
RubyGemsMetadataProvider.get_package_info()
    ↓
Check memory cache → disk cache → RubyGems API
    ↓
Apply version filters (ruby-compat, gentoo-version, etc.)
    ↓
RubyGemsEbuildGenerator.generate_ebuild()
    ↓
Apply patches (.sys/RDEPEND, .sys/ruby-compat, etc.)
    ↓
Return generated ebuild content
```

## Design Decisions

### Why ruby-fakegem Instead of Manual Ebuild Structure?

`ruby-fakegem.eclass` is Gentoo's standard for Ruby packages. It handles:
- Multi-Ruby builds (testing against multiple Ruby implementations)
- Gem installation to correct paths
- Documentation generation
- Test running
- Executable wrapping

Using it ensures compatibility with the existing Gentoo Ruby ecosystem.

### Why Exact Name Preservation?

Previous versions attempted heuristic matching (e.g., `ruby-foo` → `foo`). This caused subtle bugs:
- `ruby-debug` should NOT map to `debug` (different gems)
- `ruby-openssl` should NOT map to `openssl` (different gems)

Exact name preservation prevents these issues. The `.sys/name-translation/` mechanism handles explicit exceptions.

### Why Trailing Digit Underscore Transformation?

PMS 3.1.2 says "A package name must not end in a hyphen followed by digits" because this conflicts with version parsing. We transform `iso-639` to `iso_639` (not `iso639`) to avoid collisions with gems that actually have no hyphen.

### Why Platform-to-KEYWORDS Instead of Filtering?

Filtering platform-specific gems causes confusing errors during dependency resolution. Mapping to KEYWORDS provides:
- Clear error messages ("no KEYWORDS for your arch")
- Visibility into what exists
- Ability to install if you keyword manually

### Why Dynamic Ruby Target Detection?

Hardcoding Ruby versions would require updates with each new Ruby release. Dynamic detection from `ruby-utils.eclass` automatically picks up new versions.
