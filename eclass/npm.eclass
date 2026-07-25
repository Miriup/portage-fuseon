# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

# @ECLASS: npm.eclass
# @MAINTAINER:
# Dirk Tilger <dirk@systemication.com>
# @SUPPORTED_EAPIS: 8
# @BLURB: Install a single npm package into a pnpm-shaped content store.
# @DESCRIPTION:
# Installs one npm package version into a flat, version-keyed store modelled on
# pnpm's virtual store:
#
# @CODE
# /usr/lib/node_modules/.pnpm/<name>@<version>/node_modules/<name>/    <- real files
# /usr/lib/node_modules/.pnpm/<name>@<version>/node_modules/<dep>      -> sibling entry
# @CODE
#
# The layout is what makes npm packaging tractable under portage. npm allows
# several versions of one package to coexist in nested node_modules trees, which
# portage cannot express; because each entry here is keyed by version and owns
# only its own files, SLOT="${PV}" makes every version co-installable with no
# file collisions. Dependency edges are plain relative symlinks, so node's
# ordinary upward search for node_modules resolves them with no NODE_PATH, no
# loader shim, and no pnpm at build or run time -- only the directory shape
# matters.
#
# Consumers list their resolved direct dependencies in NPM_DEPS as name@version
# pairs. The versions must be the *upstream* ones, matching the store keys, not
# the translated Gentoo versions.
#
# @EXAMPLE:
# @CODE
# EAPI=8
#
# NPM_PN="@vue/cli-service"
# NPM_PV="5.0.8"
# NPM_DEPS="@vue/cli-overlay@5.0.9 webpack@5.89.0 chalk@4.1.2"
#
# inherit npm
#
# DESCRIPTION="Service layer for vue-cli"
# HOMEPAGE="https://www.npmjs.com/package/@vue/cli-service"
#
# LICENSE="MIT"
# KEYWORDS="~amd64 ~arm64"
# @CODE

case ${EAPI} in
	8) ;;
	*) die "${ECLASS}: EAPI ${EAPI:-0} not supported" ;;
esac

if [[ -z ${_NPM_ECLASS:-} ]]; then
_NPM_ECLASS=1

# @ECLASS_VARIABLE: NPM_PN
# @DESCRIPTION:
# Upstream npm package name, including any scope, e.g. "@vue/cli-service".
# Defaults to ${PN}, which is only correct for unscoped names that needed no
# translation.
: "${NPM_PN:=${PN}}"

# @ECLASS_VARIABLE: NPM_PV
# @DESCRIPTION:
# Upstream npm version, e.g. "1.0.0-beta.1". Defaults to ${PV}, which differs
# whenever the version needed translating into PMS form: PV would be
# "1.0.0_beta1" for that example. Store keys and NPM_DEPS use upstream
# versions, so this must be set whenever the two spellings diverge.
: "${NPM_PV:=${PV}}"

# @ECLASS_VARIABLE: NPM_DEPS
# @DEFAULT_UNSET
# @DESCRIPTION:
# Space-separated resolved direct dependencies as name@version, e.g.
# "chalk@4.1.2 @types/node@20.1.0". One symlink is created per entry. Versions
# are upstream versions, matching the store keys.

# @ECLASS_VARIABLE: NPM_BIN
# @DEFAULT_UNSET
# @DESCRIPTION:
# Space-separated command:relative-path pairs to expose in /usr/bin, e.g.
# "vue-cli-service:bin/vue-cli-service.js". When unset the package's own
# package.json "bin" field is used, which is correct for almost every package;
# set this only to override or to suppress installation with NPM_BIN="-".

# Declared so the eclass stays correct under `set -u`; portage itself does not
# enable it, but a bare reference to an unset optional variable is a latent trap.
: "${NPM_DEPS:=}"
: "${NPM_BIN:=}"

# @ECLASS_VARIABLE: NPM_STORE_ROOT
# @DESCRIPTION:
# Root of the module tree. Matches where "npm install -g" puts modules, so a
# hand-installed tree and a portage-installed one sit side by side.
: "${NPM_STORE_ROOT:=/usr/lib/node_modules}"

# @ECLASS_VARIABLE: NPM_REGISTRY
# @DESCRIPTION:
# Registry base URL used to build SRC_URI.
: "${NPM_REGISTRY:=https://registry.npmjs.org}"

# @ECLASS_VARIABLE: NPM_NODE_DEP
# @DESCRIPTION:
# Dependency atom for the Node runtime, overridable for packages declaring a
# stricter engines.node range.
: "${NPM_NODE_DEP:=>=net-libs/nodejs-18}"

# @FUNCTION: npm_store_name
# @USAGE: <npm package name>
# @DESCRIPTION:
# Convert an npm package name to its store directory component, replacing the
# scope separator with "+" as pnpm does: "@vue/cli-service" becomes
# "@vue+cli-service". A "/" would otherwise add a directory level and break the
# flat store.
# Note: this is called from a command substitution, so a `die` here would exit
# only the subshell. The guard therefore reports an eclass-internal misuse; it
# does not and cannot abort a merge. Package-data validation happens in the
# calling function's own shell.
npm_store_name() {
	if [[ ${#} -ne 1 ]]; then
		eerror "${FUNCNAME[0]}: expected exactly one argument, got ${#}"
		return 1
	fi
	echo "${1/\//+}"
}

# Tarball basename drops the scope: @vue/cli-service publishes cli-service-5.0.8.tgz.
_NPM_TARBALL_BASE=${NPM_PN##*/}

# The rename in SRC_URI is required, not cosmetic: scoped packages from
# different scopes share a basename, so cli-service-5.0.8.tgz would collide in
# DISTDIR with any other package called cli-service.
SRC_URI="${NPM_REGISTRY}/${NPM_PN}/-/${_NPM_TARBALL_BASE}-${NPM_PV}.tgz -> ${P}.tgz"
S="${WORKDIR}/package"

SLOT="${PV}"
RDEPEND="${NPM_NODE_DEP}"
BDEPEND="${NPM_NODE_DEP}"

# @FUNCTION: npm_src_prepare
# @DESCRIPTION:
# Remove any node_modules directory shipped inside the tarball. Bundled
# dependencies would shadow the store symlinks and silently defeat dependency
# tracking, so they are dropped in favour of the resolved deps in NPM_DEPS.
npm_src_prepare() {
	if [[ -d node_modules ]]; then
		einfo "Removing bundled node_modules in favour of NPM_DEPS"
		rm -rf node_modules || die "failed to remove bundled node_modules"
	fi

	default
}

# @FUNCTION: npm_install_deps_symlinks
# @USAGE: <absolute path to the entry's node_modules>
# @DESCRIPTION:
# Create one relative symlink per NPM_DEPS entry, pointing at the sibling store
# entry that provides it.
npm_install_deps_symlinks() {
	[[ ${#} -eq 1 ]] || die "${FUNCNAME[0]}: expected the entry node_modules path"

	local modules_dir=${1}
	local spec name version dep_store link_dir depth up

	for spec in ${NPM_DEPS}; do
		# Parsed inline rather than via npm_split_spec: `die` inside a command
		# substitution exits only the subshell, so a malformed spec would be
		# reported and then silently skipped instead of aborting the build.
		# Split on the last '@' so scoped names, which start with '@', work.
		name=${spec%@*}
		version=${spec##*@}
		[[ -n ${name} && -n ${version} && ${name} != "${spec}" ]] ||
			die "${FUNCNAME[0]}: cannot parse dependency spec '${spec}', expected name@version"

		dep_store="$(npm_store_name "${name}")@${version}"

		# A scoped link sits one level deeper (node_modules/@scope/name), so it
		# needs one more "..' to climb back to the .pnpm directory.
		depth=2
		[[ ${name} == @*/* ]] && depth=3

		up=
		for (( i = 0; i < depth; i++ )); do
			up+="../"
		done

		link_dir=${modules_dir}/${name%/*}
		if [[ ${name} == @*/* ]]; then
			dodir "${link_dir#"${ED}"}"
		fi

		ln -s "${up}${dep_store}/node_modules/${name}" \
			"${modules_dir}/${name}" ||
			die "failed to link dependency ${spec}"
	done
}

# @FUNCTION: npm_get_bin_entries
# @DESCRIPTION:
# Print the package's command:path pairs, one per line. Uses NPM_BIN when set,
# otherwise reads the "bin" field from package.json. node does the parsing
# because it is already a build dependency and hand-rolled JSON parsing in bash
# mishandles escapes.
npm_get_bin_entries() {
	if [[ ${NPM_BIN} == "-" ]]; then
		return 0
	fi

	if [[ -n ${NPM_BIN} ]]; then
		local entry
		for entry in ${NPM_BIN}; do
			echo "${entry}"
		done
		return 0
	fi

	[[ -f ${S}/package.json ]] || return 0

	# A string "bin" means a single command named after the package; an object
	# maps command names to paths.
	node -e '
		const fs = require("fs");
		const pkg = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
		const bin = pkg.bin;
		if (!bin) process.exit(0);
		const short = (pkg.name || "").replace(/^@[^/]+\//, "");
		if (typeof bin === "string") {
			console.log(short + ":" + bin);
		} else {
			for (const [name, path] of Object.entries(bin)) {
				if (name && path) console.log(name + ":" + path);
			}
		}
	' "${S}/package.json" || die "failed to read bin entries from package.json"
}

# @FUNCTION: npm_install_bin_wrappers
# @USAGE: <absolute path to the installed package directory>
# @DESCRIPTION:
# Install a /usr/bin wrapper per command. The wrapper execs node on the script
# inside the store entry; because the script lives under
# .pnpm/<entry>/node_modules/<name>/, node's ordinary upward search finds the
# entry's node_modules and resolves the dependency symlinks without any
# environment variable.
npm_install_bin_wrappers() {
	[[ ${#} -eq 1 ]] || die "${FUNCNAME[0]}: expected the installed package path"

	local pkg_dir=${1}
	local entry command script target

	while IFS= read -r entry; do
		[[ -n ${entry} ]] || continue

		command=${entry%%:*}
		script=${entry#*:}
		target=${pkg_dir}/${script}

		if [[ ! -f ${target} ]]; then
			ewarn "bin entry ${command} points at missing ${script}, skipping"
			continue
		fi

		fperms +x "${target#"${ED}"}"

		newbin - "${command}" <<-EOF
			#!/bin/sh
			# Generated by npm.eclass for ${CATEGORY}/${PF}
			exec node "${target#"${ED}"}" "\$@"
		EOF
	done < <(npm_get_bin_entries)
}

# @FUNCTION: npm_src_compile
# @DESCRIPTION:
# No-op. npm packages are published pre-built; anything needing a compile step
# has native bindings and must override this.
npm_src_compile() {
	:
}

# @FUNCTION: npm_src_install
# @DESCRIPTION:
# Install the package into its store entry, link its dependencies, and install
# any command wrappers.
npm_src_install() {
	local store_entry pkg_path modules_dir pkg_dir

	store_entry="$(npm_store_name "${NPM_PN}")@${NPM_PV}"
	pkg_path=${NPM_STORE_ROOT}/.pnpm/${store_entry}/node_modules
	modules_dir=${ED}${pkg_path}
	pkg_dir=${modules_dir}/${NPM_PN}

	# Create the parent of the package directory, but never the package
	# directory itself: cp -R copies *into* an existing destination, which would
	# nest the tree one level too deep as <name>/package/. Note that
	# ${NPM_PN%/*} leaves an unscoped name unchanged, so the scoped case has to
	# be tested explicitly rather than relying on the expansion.
	if [[ ${NPM_PN} == @*/* ]]; then
		dodir "${pkg_path}/${NPM_PN%/*}"
	else
		dodir "${pkg_path}"
	fi

	einfo "Installing ${NPM_PN}@${NPM_PV} to ${pkg_path}/${NPM_PN}"
	cp -R "${S}" "${pkg_dir}" || die "failed to install package files"

	npm_install_deps_symlinks "${modules_dir}"
	npm_install_bin_wrappers "${pkg_dir}"

	einstalldocs
}

fi

EXPORT_FUNCTIONS src_prepare src_compile src_install
