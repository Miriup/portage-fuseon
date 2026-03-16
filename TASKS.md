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

### RubyGems platform checksum mismatches

**Gems affected:**
- `io-console-0.6.0`
- `google-protobuf-3.25.7`

**Symptoms:** SHA256 checksum verification fails during emerge:
```
!!! Fetched file: google-protobuf-3.25.7.gem VERIFY FAILED!
!!! Reason: Failed on SHA256 verification
!!! Got:      a860ead0c79a4598082ef2be638e23f61602524b3d48a001f48cf33d7f8cd9a9
!!! Expected: acc5d3c1d9a1a92be262702b506c7f7163daa5fb8eadefdffdd13dc3ac449d96
```

**Root cause:** These gems have platform-specific variants (native extensions). The RubyGems API returns all platform variants, and `_generate_manifest()` was iterating through all of them without filtering, causing the last platform's checksum to overwrite earlier ones.

Example for `google-protobuf-3.25.7`:
- `ruby` platform SHA: `a860ead0c79a4598...` (correct - what gets downloaded)
- `aarch64-linux` SHA: `acc5d3c1d9a1a92b...` (was last in API response, overwrote the correct one)

**Fix applied:** Updated `_generate_manifest()` in `filesystem.py` to use the same platform preference logic as `_get_package_versions()` - prefer `ruby` platform over platform-specific variants.

**Workaround applied:** Can now be removed from `/etc/portage/package.mask/rubygems`:
```
=dev-ruby/io-console-0.6.0
```

**Status:** ✅ Fixed

### SRC_URI uses wrong name for translated packages (iso_639)

**Gem affected:** `iso-639` (translated to Gentoo package `iso_639`)

**Symptoms:**
```
!!! Fetched file: iso_639-0.3.8.gem VERIFY FAILED!
!!! Reason: Insufficient data for checksum verification
```

**Root cause:** The ebuild generator used `${PN}` (Gentoo package name) for SRC_URI, but for translated packages this is wrong:
- Package name: `iso_639` (underscore)
- Actual gem file: `iso-639-0.3.8.gem` (hyphen)
- Generated SRC_URI: `iso_639-0.3.8.gem` (wrong!)
- Manifest entry: `iso-639-0.3.8.gem` (correct)

**Fix applied:** Changed `plugin.py` to use the original gem name in SRC_URI instead of `${PN}`:
```python
# Before (buggy):
lines.append(f'SRC_URI="https://rubygems.org/gems/${{PN}}-${{PV}}.gem"')

# After (fixed):
lines.append(f'SRC_URI="https://rubygems.org/gems/{name}-{version}.gem"')
```

**Status:** ✅ Fixed

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

**Status:** Partially completed
- `README.md` updated with RubyGems patching examples
- `docs/rubygems.md` created with comprehensive patching documentation
- `docs/dependency-patching.md` still needs RubyGems examples added
- `docs/build-error-fixes.md` still needs RubyGems section
