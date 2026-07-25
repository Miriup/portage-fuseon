# Adding npm/Node.js as a third ecosystem

Status: design proposal, not yet implemented.

## Context

portage-fuseon currently exposes PyPI and RubyGems to portage as FUSE overlays. The
motivating use case for npm is installing Node CLI tooling on Gentoo
(`@vue/cli-service` and friends), which today has no viable portage path: there is
**no `npm`/`nodejs`/`pnpm`/`yarn` eclass in ::gentoo** (verified: all 404 in
`gentoo/gentoo/eclass/`, while `cargo.eclass` is 200) and **no `dev-nodejs` category**
in `profiles/categories` (174 categories; only `dev-java` matches /node|js/).
Everything has to be supplied by the overlay itself.

The obvious starting point is to follow `pycargoebuild` — vendor the whole resolved
dependency closure into one ebuild, the way `CRATES=`/`cargo_crate_uris` does for
Rust. That works, but investigation shows a **pnpm-shaped model is strictly better
for this project**, because it removes the impedance mismatch *and* fits the existing
lazy-FUSE architecture instead of fighting it. That is the recommendation below.

Measurements in this document were taken against the live npm registry with
node 22.22.2 / npm 10.9.7 / pnpm 10.33.0.

## The core problem, quantified

npm permits multiple versions of one package to coexist in nested `node_modules`;
portage permits one version per SLOT. Measured against the actual target:

- `@vue/cli-service@^5.0.8` alone resolves to **656 packages** / 544 distinct names,
  with **57 names appearing at more than one version** (`semver`×6, `chalk`×7,
  `yallist`×4, `lru-cache`×3, …).
- `npm install --package-lock-only` for that closure takes **19 s cold** — far too
  slow to run inside a FUSE `read()` with portage blocking on it.
- The resulting `package-lock.json` is **292 KB**.

So the naive "one ebuild per npm package with translated RDEPEND" mapping — the
pypi/rubygems design — is unsatisfiable as-is. This is precisely the Rust/crates
situation.

## Three models considered

**A. Vendored closure (the pycargoebuild analogue).** One ebuild per app;
`NPM_DEPS="name@version …"` expands to a SRC_URI enumerating the whole closure.
Verified feasible: `npm cache add <tgz>` × N then `npm ci --offline` installs with
zero network. But: ~656-entry SRC_URI (~25 KB of URIs), a resolver required at
generation time, ~656 ranged GETs per ebuild just to learn Manifest `SIZE` (npm's
packument does **not** publish tarball byte size — recoverable only via
`Range: bytes=0-0` → `Content-Range: bytes 0-0/3619`), the 292 KB lockfile has to be
smuggled to the build, no dedup across installs, and generation cannot be lazy.

**B. One ebuild per package with semver-range RDEPEND.** Mirrors pypi/rubygems.
Unsatisfiable — 57 conflicting names in a single closure is the normal case, not the
exception.

**C. pnpm-layout model — RECOMMENDED.** pnpm's virtual store is *flat and
version-keyed*, and each entry owns only its own files, with dependency edges
expressed purely as symlinks. Verified on disk:

```
node_modules/.pnpm/chalk@4.1.2/node_modules/chalk/          <- real files, 7 of them
node_modules/.pnpm/chalk@4.1.2/node_modules/ansi-styles  -> ../../ansi-styles@4.3.0/node_modules/ansi-styles
node_modules/.pnpm/express@4.22.2/node_modules/accepts   -> ../../accepts@1.3.8/node_modules/accepts
```

(131 symlinks vs 74 real dirs in a small closure — the graph *is* the symlinks.)

That shape maps onto portage cleanly:

| pnpm concept | Gentoo mapping |
|---|---|
| `.pnpm/<name>@<ver>/node_modules/<name>/` | `dev-nodejs/<name>` ebuild, `SLOT="<ver>"`, installs under `/usr/lib/node_modules/.pnpm/<name>@<ver>/node_modules/<name>/` |
| symlink to a sibling entry | `RDEPEND="=dev-nodejs/<dep>-<ver>:<ver>"`, symlink created in `src_install` |
| top-level `node_modules/<name>` symlink | app package's own symlink farm + `/usr/bin` wrapper |

**Because the install path is version-keyed, `SLOT="${PV}"` makes every version
co-installable with no file collisions.** The impedance mismatch disappears without
vendoring. Consequences that matter:

- **SRC_URI is exactly one tarball per ebuild.** One Manifest `DIST` line, one SIZE
  probe. No 656-entry SRC_URI, no closure-sized fetch lists.
- **Lazy generation works again**, so npm reuses the existing FUSE architecture
  rather than needing a separate materialization pipeline.
- **No resolver at generation time beyond one level.** An ebuild only needs its own
  direct deps pinned; portage walks the graph transitively. That is a pure-Python
  semver-range-against-a-version-list problem, not a full SAT resolve.
- **`pnpm` is needed neither at build time nor at runtime** — verified that plain
  `node` resolves correctly through a hand-built symlink farm. The eclass creates the
  symlinks itself; we target the layout, not the tool.
- Real dedup across installed packages, and `emerge --depclean` GC works.

Keep **A as an escape hatch** (`--vendor` per package via the `.sys/` mechanism) for
pathological packages — chiefly peer-heavy packages and native `node-gyp` modules —
not as the default.

### Known limits of model C, accepted deliberately

- **Peer dependencies.** pnpm creates a distinct entry per (version, peer-set):
  `@vue+cli-service@5.0.8_lodash@4.18.1_react-dom@18.2.0_…`. Measured **70 of 616
  entries (11%)** in the `@vue/cli-service` + react + webpack closure. Portage cannot
  model that without exploding slots. **Decision: collapse to one entry per
  (name, version) and treat `peerDependencies` as ordinary RDEPEND, with
  `peerDependenciesMeta.optional` → optional.** Same simplification Debian and Fedora
  make; loses fidelity only where one version needs two different peer
  instantiations.
- **Exact pins drift.** `RDEPEND` pins a concrete version chosen at generation time;
  a later mount could pick differently and orphan an installed tree. Mitigate with a
  **resolution-lock patch store** recording chosen pins per (package, version) — this
  fits the existing `.sys/` patch-store pattern exactly (see `slot_patch.py`).
- **Graph size.** `emerge` on a ~656-node graph. Each node is unpack-and-copy with no
  compilation, so it is slow but tolerable.
- **Slot accumulation.** `SLOT="${PV}"` means portage never upgrades, only
  accumulates; `--depclean` is the GC, so only apps belong in `@world`.
- **Native modules** (`node-gyp`) need per-package build logic; defer behind a
  `has-bindings` filter and the `.sys/ebuild-append` mechanism.

## Architectural reality of this repo (drives the effort estimate)

The plugin architecture is **declarative but essentially unwired**. `get_metadata_provider`,
`get_ebuild_generator`, `get_name_translator`, `get_source_providers`,
`get_version_filters`, `get_package_filters`, `get_cli_handler`, `get_static_dirs`
have **zero call sites** outside their own class definitions in
`portage_pip_fuse/plugin.py`. Both filesystems instantiate their providers directly
(`filesystem.py:128-132`; `ecosystems/rubygems/filesystem.py:34`). A plugin that
implements the ABCs perfectly would do nothing.

- RubyGems was added by copying the PyPI *shape*: ~5,900 lines including its own
  2,323-line FUSE layer, its own `VersionFilterChain`, its own `SourceProviderChain`,
  its own two-level cache.
- `ecosystems/pypi/plugin.py` is **non-functional** — it calls five
  `EbuildDataExtractor` methods that do not exist (`generate_ebuild`,
  `_detect_pep517_backend`, `_generate_python_compat`, `_generate_rdepend`,
  `_generate_bdepend`). It is an actively misleading template.
- There are **six** independent upstream→Gentoo version translators and **three**
  license tables.
- `CLAUDE.md` claims ~70% code sharing; measured it is closer to 25-30%.
- `CLAUDE.md` documents `PluginRegistry.get_all_plugins()`/`get_plugin()`; the real
  names are `get_all()`/`get()`.

**Genuinely reusable as-is** (~4,000 lines, all `(category, package, version)`-keyed):
the nine patch stores (`dependency_patch.py`, `iuse_patch.py`,
`ebuild_append_patch.py`, `slot_patch.py`, `git_source_patch.py`,
`name_translation_patch.py`, `compat_patch.py` as a base class),
`constants.find_cache_dir`/`get_mount_point_key`/`HTTP_TIMEOUT`,
`git_provider.normalize_git_url`/`validate_git_url`/`is_git_host_url`,
`source_provider.SourceInfo`/`SourceProviderBase`, the filter chains/registries, and
`interrupt.py`.

## Plan

### Phase 0 — Minimal shared-infrastructure prerequisites

Do only what npm would otherwise duplicate for a fourth time. Each of these also
fixes an existing bug, so they pay for themselves.

1. **`portage_pip_fuse/pms_version.py`** — canonical PMS version regex (currently
   inlined at `filesystem.py:1100-1115`), the suffix vocabulary, `validate()`, and a
   tokenizer/inverse scaffold. Port the RubyGems tokenizer
   (`ecosystems/rubygems/filesystem.py:591-690` forward, `692-729` inverse) as the
   reference implementation — it is the only reversible one. Then route the six
   existing translators through it. This fixes the real bug that
   `pip_metadata.py:1766` and `cli.py:123` translate **without validation**, so
   unvalidated versions reach dependency atoms via
   `_format_gentoo_dependency` (`pip_metadata.py:1946`).
2. **`portage_pip_fuse/gentoo_license.py`** — merge the three tables
   (`pip_metadata.py:1038-1063` instance map, the `spdx_to_gentoo` local at
   `pip_metadata.py:1281-1308`, the local at `rubygems/plugin.py:984-997`), plus the
   `|| ( )` / AND expression builder and one sentinel. Fixes RubyGems'
   pass-through of unrecognised LICENSE strings (`rubygems/plugin.py:1003-1004`).
3. **`portage_pip_fuse/json_cache.py`** — the memory-dict + sharded-JSON-disk + TTL
   pattern, currently written three times (`pip_metadata.py:97-227`,
   `rubygems/plugin.py:180-235`, plus FS-level dicts).
4. **Fix or delete `ecosystems/pypi/plugin.py`.** Do not leave a broken template in
   place while adding a third ecosystem against it.

Explicitly **out of scope**: the full plugin-architecture rework (shared FUSE base
class, table-driven `.sys` dispatcher, registry-driven CLI). Worth doing, but not
before npm works.

### Phase 1 — `eclass/npm.eclass`, served from the overlay

::gentoo has no npm eclass, so ship one. Both filesystems already expose an
`/eclass` static dir (`filesystem.py:221`), currently empty — serve it from there,
and support writing it to disk for users who prefer that.

Responsibilities: unpack the single tarball; install `package/` to
`/usr/lib/node_modules/.pnpm/${NPM_STORE_NAME}@${PV}/node_modules/${NPM_NAME}/`;
create the sibling symlinks named in `NPM_DEPS`; for packages with a `bin` field,
build the top-level symlink farm and a `/usr/bin` wrapper setting `NODE_PATH`.
Scoped names use pnpm's `+` separator in the store path (`@vue/cli-overlay` →
`@vue+cli-overlay@5.0.9`) — verified against a real pnpm tree.

Sketch:

```bash
EAPI=8
# NPM_NAME    - upstream npm name, e.g. @vue/cli-service
# NPM_DEPS    - "name@version …", one per resolved direct dependency
# NPM_BIN     - bin-name:relative-path pairs
SRC_URI="https://registry.npmjs.org/${NPM_NAME}/-/${_npm_base}-${PV}.tgz -> ${P}.tgz"
SLOT="${PV}"
RDEPEND=">=net-libs/nodejs-18"
```

Note the SRC_URI `-> ` rename is **mandatory, not cosmetic**: scoped packages
download under a colliding basename (`@vue/cli-service` → `cli-service-5.0.8.tgz`).

### Phase 2 — `portage_pip_fuse/ecosystems/npm/`

Follow the RubyGems file layout. New modules:

- `plugin.py` — `NpmPlugin` (`name='npm'`, `default_category='dev-nodejs'`,
  `default_repo_location='/var/db/repos/npm'`, `repo_name='portage-npm-fuse'`),
  `NpmMetadataProvider`, `NpmEbuildGenerator`. Register at module scope via
  `PluginRegistry.register('npm', NpmPlugin)` — mirroring
  `rubygems/plugin.py:1141`, **not** the `__init__.py` recipe in `CLAUDE.md`,
  which is wrong. Add `'npm'` to `AVAILABLE_ECOSYSTEMS`
  (`ecosystems/__init__.py:21`).
- `semver.py` — the substantial new piece, and the only genuinely npm-specific
  algorithm. Parse npm ranges (`^`, `~`, `>=`, `||`, hyphen ranges, `x`/`*`
  wildcards, prerelease precedence) and select the highest matching version from a
  packument's version list. No npm binary required.
- `version_translator.py` — semver → PMS via `pms_version.py`:
  `1.2.3-alpha.1` → `1.2.3_alpha1`, `-beta.2` → `_beta2`, `-rc.0` → `_rc0`;
  strip `+build` metadata; **reject** `-next.5`, `-canary.3`, `-security`,
  `-insiders.*` and friends, exactly as `GentooVersionFilter`
  (`rubygems/filters.py:477-626`) does.
- `name_translator.py` — `@scope/name` → `scope-name`; `.` → `_` (npm allows dots,
  PMS `PN` does not — `[A-Za-z0-9+_-]` only, per
  `rubygems/name_translator.py:305-316`); reuse the trailing-digit rule
  (`http-2` → `http_2`). Collisions resolved through the existing
  `NameTranslationPatchStore`.
- `filters.py` — `node-compat` (`engines.node` vs installed `net-libs/nodejs`
  slots), `platform` (`os`/`cpu` → KEYWORDS, modelled on
  `rubygems/plugin.py:platform_to_keywords`, which never filters, only re-keywords),
  `gentoo-version`, `deprecated` (drop versions with a `deprecated` field),
  `has-bin` (opt-in, for browsing installable tooling out of ~3.5M packages).
- `filesystem.py` — `PortageNpmFS`. Copy `rubygems/filesystem.py`, and specifically
  copy its **three `_parse_sys_path_*` shape helpers (lines 444-505)** rather than
  PyPI's ~340-line `if len(parts) == N` ladder (`filesystem.py:459-795`).
- `resolution_lock.py` — new patch store recording chosen dependency pins per
  (package, version) so RDEPEND is stable across mounts. Model on `slot_patch.py`.
- `cli.py` — `npm_command()` (translate `npm install <pkg>` → `emerge`) and
  `npx_command()`; lockfile path (`package-lock.json` → portage set) modelled on
  `rubygems/cli.py:bundle_command()` at line 359. When writing the set name, use
  PyPI's sanitising `_derive_set_name` (`cli.py:597`), **not** the gem path's
  unsanitised inline version (`rubygems/cli.py:504`).

**Serve `profiles/categories`.** New requirement: `dev-nodejs` does not exist in
::gentoo, so the overlay must publish it. The FUSE layer currently serves only
`profiles/repo_name` (`filesystem.py:268`, `392-398`) — extend `_parse_path` and the
static-file map.

### Phase 3 — wiring

- `main_npm()` in `cli.py` plus `npm`/`mount`/`unmount`/`install`/`debug`
  subcommands, following `main_rubygems()` (`cli.py:1895`). Note the existing
  duplication: `mount_command`/`rubygems_mount_command`, etc. Do not add a third
  copy of the pid-file and logfile blocks (`cli.py:1727-1734` ≈ `2146-2153`) — factor
  those two helpers out while adding the npm variant.
- `[project.scripts]` entry `portage-npm-fuse = portage_pip_fuse.cli:main_npm` in
  `pyproject.toml` (packages are auto-discovered via `include = ["portage_pip_fuse*"]`,
  so no other packaging change is needed). Add `bin/portage-npm-fuse` to match the
  existing launchers.
- Manifest `SIZE`: npm does not publish tarball byte size. Probe with
  `Range: bytes=0-0` and read `Content-Range` (verified: returns
  `bytes 0-0/3619`); cache permanently, since published tarballs are immutable.
  `dist.integrity` already supplies sha512 (base64 → hex).

### Phase 4 — docs and tests

- `docs/npm.md` mirroring `docs/rubygems.md` (the best worked example in the
  repo), plus the npm rows in `CLAUDE.md`'s ecosystem table. Correct the
  `PluginRegistry` method names and the registration recipe in `CLAUDE.md` while
  there.
- Tests: `tests/test_npm_semver.py` (table-driven range→version selection — the
  highest-risk module, so cover it hardest), `tests/test_npm_version_translation.py`
  (round-trip, mirroring `tests/test_rubygems_filesystem.py:63`),
  `tests/test_npm_name_translation.py`, `tests/test_npm_filesystem.py` using the
  established `PortageNpmFS.__new__(PortageNpmFS)` no-`__init__` fixture pattern
  (`tests/test_rubygems_filesystem.py:23`), and `tests/test_npm_eclass.py` asserting
  generated-ebuild shape. Patch `urllib.request.urlopen`; no network in tests.
  Worth adding tests for `pms_version.py` and `gentoo_license.py` from Phase 0 too,
  since neither has coverage today.

## Verification

1. `pytest tests/ -v` — all new and existing tests green. Confirm Phase 0 did not
   regress pypi/rubygems version or license output.
2. Unit-check the resolver against ground truth: for a handful of packages, compare
   `semver.py`'s one-level pin against what `npm install --package-lock-only`
   actually chose. Disagreement here is the single most likely source of broken
   ebuilds.
3. Mount and inspect without portage:
   `portage-npm-fuse mount /var/db/repos/npm --foreground -v`, then read
   `dev-nodejs/vue-cli-service/vue-cli-service-5.0.8.ebuild` and its `Manifest`.
   Confirm one `DIST` line, correct `SIZE`, `SLOT="5.0.8"`, and `RDEPEND` pinned to
   concrete `=dev-nodejs/<dep>-<ver>:<ver>` atoms.
4. `ebuild <path> manifest` and `emerge -pv dev-nodejs/vue-cli-service` — confirm
   portage parses the overlay, resolves the graph, and reports a sane package count
   (expect a few hundred).
5. Full `emerge dev-nodejs/vue-cli-service` on a Gentoo box, then check the installed
   tree has the pnpm-shaped layout, that `vue-cli-service --version` runs, and that
   two conflicting versions of a shared dep (e.g. `semver`) are genuinely
   co-installed in separate slots.
6. `emerge --depclean` afterwards — confirm the slot accumulation is collectable.
7. `portage-npm-fuse npm install <pkg> --dry-run` and the lockfile→set path against a
   real `package-lock.json`.

## Biggest risks

1. **`semver.py` correctness.** Everything downstream depends on picking the same
   version npm would. Mitigate with the differential test in step 2 above.
2. **Graph size in portage.** ~656 nodes per app is untested territory for this
   project; step 4 is the gate.
3. **Peer-dependency collapse** (11% of entries measured) may break specific
   packages in ways only a real build reveals — step 5.
4. **Pin drift** orphaning installed trees, addressed by `resolution_lock.py` but
   needs a real cross-mount test.
