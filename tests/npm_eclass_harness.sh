#!/bin/bash
# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2
#
# Exercise npm.eclass phase functions outside portage.
#
# Portage is not installable in most development or CI environments, but the
# eclass's real product -- the on-disk store layout and its symlink targets --
# can be verified without it. This harness stubs the handful of portage bash
# helpers the eclass uses, sources the eclass, and runs its phases against a
# throwaway image directory. The Python test then inspects the result and checks
# that node itself resolves modules through it.
#
# Usage: npm_eclass_harness.sh <eclass path> <workdir> <image dir>

set -euo pipefail

ECLASS_PATH=${1:?eclass path required}
export WORKDIR=${2:?workdir required}
export ED=${3:?image dir required}

export EAPI=8
export CATEGORY=${CATEGORY:-dev-nodejs}
export PN=${PN:-test}
export PV=${PV:-0}
# Portage always provides these; the eclass builds SRC_URI from P.
export P=${P:-${PN}-${PV}}
export PF=${PF:-${P}}

# --- portage helper stubs ------------------------------------------------------

die() { echo "die: $*" >&2; exit 1; }
einfo() { echo " * $*"; }
elog() { echo " * $*"; }
ewarn() { echo " ! $*" >&2; }
default() { :; }
einstalldocs() { :; }
inherit() { :; }
EXPORT_FUNCTIONS() { :; }

dodir() {
	local d
	for d in "$@"; do
		mkdir -p "${ED}${d}" || die "dodir ${d} failed"
	done
}

fperms() {
	local mode=$1; shift
	local f
	for f in "$@"; do
		chmod "${mode}" "${ED}${f}" || die "fperms ${mode} ${f} failed"
	done
}

# Only the "newbin - <name>" form the eclass uses is supported.
newbin() {
	[[ $1 == "-" ]] || die "harness newbin only supports reading from stdin"
	local name=$2
	mkdir -p "${ED}/usr/bin"
	cat > "${ED}/usr/bin/${name}" || die "newbin ${name} failed"
	chmod +x "${ED}/usr/bin/${name}"
}

# --- run the phases -----------------------------------------------------------

# shellcheck source=/dev/null
source "${ECLASS_PATH}" || die "failed to source eclass"

cd "${S}" || die "S=${S} does not exist"

npm_src_prepare
npm_src_compile
npm_src_install

echo "harness: ok"
