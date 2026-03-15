# Tasks

## Upstream Issues to Report

### ai-agents gem missing license in gemspec

**Gem:** ai-agents
**Version:** 0.9.0
**Repository:** https://github.com/chatwoot/ai-agents

**Issue:** The gem's LICENSE file specifies MIT, but the gemspec doesn't declare a license. This causes package managers (like Gentoo Portage) to mark the package as having an "unknown" license.

**Fix needed in upstream:**
Add to the gemspec:
```ruby
spec.license = "MIT"
```

Or for multiple licenses:
```ruby
spec.licenses = ["MIT"]
```

**Workaround applied:** Added `unknown` license to `/etc/portage/package.license`

**Status:** Not yet reported

### gmail_xoauth gem missing license in gemspec

**Gem:** gmail_xoauth
**Version:** 0.4.3

**Issue:** Same as ai-agents - missing license declaration in gemspec.

**Status:** Not yet reported

### selectize-rails gem malformed license in gemspec

**Gem:** selectize-rails
**Version:** 0.12.6
**Repository:** https://github.com/manuelvanrijn/selectize-rails

**Issue:** The gemspec declares license as a single string containing multiple licenses: `"MIT, Apache License v2.0"` instead of using the proper array format.

**Current (incorrect):**
```ruby
spec.license = "MIT, Apache License v2.0"
```

**Fix needed in upstream:**
```ruby
spec.licenses = ["MIT", "Apache-2.0"]
```

**Workaround applied:** Added `"MIT, Apache License v2.0"` license to `/etc/portage/package.license`:
```
dev-ruby/selectize-rails MIT,\ Apache\ License\ v2.0
```

**Status:** Not yet reported

## Refactoring Tasks

### Simplify RubyGems platform handling

**Current state:** Complex KEYWORDS mapping for platform-specific gems (java, x86_64-linux, etc.)

**Problem:** The current implementation maps RubyGems platforms to Gentoo KEYWORDS (e.g., `java` → empty KEYWORDS, `x86_64-linux` → `~amd64`). This is overengineered.

**Insight:** RubyGems platforms are analogous to Python's sdist/wheel:
- `ruby` platform = source code (like sdist) → **what we want**
- `x86_64-linux`, `arm64-darwin`, etc. = pre-compiled binaries (like wheels)
- `java` = pre-compiled JVM bytecode for JRuby

**Correct approach:** Since Gentoo builds from source, we should:
1. **Only include versions with `platform='ruby'`** (source available)
2. **Filter out all platform-specific variants** (pre-compiled binaries)
3. Gems without a `ruby` platform variant (like `jruby-openssl`) simply don't appear

This is consistent with the `source-dist` filter philosophy for PyPI.

**Files to modify:**
- `ecosystems/rubygems/filesystem.py` - Simplify `_get_package_versions()` to only include `platform='ruby'`
- `ecosystems/rubygems/plugin.py` - Remove `platform_to_keywords()` function
- `ecosystems/rubygems/filters.py` - Update or remove `PlatformFilter`
- `CLAUDE.md` - Remove "Platform-to-KEYWORDS Mapping" section

**Debug command:** Use `portage-gem-fuse debug versions <gem> --platforms` to see which gems have `ruby` platform variants.

**Status:** Not started

## Documentation Tasks

### Add RubyGems .sys patching examples

**Files to update:**
- `README.md` - Add RubyGems examples to "Patching and Customization" section
- `docs/dependency-patching.md` - Add RubyGems dependency patching examples
- `docs/build-error-fixes.md` - Add RubyGems-specific build error fixes

**Context:** The .sys patching mechanism is currently documented with PyPI examples only. With the simplified name translation (exact gem names, no heuristic matching), users will need to use .sys patches for name mismatches between gems and existing Gentoo packages.

**Status:** Not started
