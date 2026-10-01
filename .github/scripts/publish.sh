#!/usr/bin/env bash
set -euo pipefail

# Upload one checked build to its release: a stable build to its own
# <family>-<version> release, with that release's manifest; a nightly to the
# day's release, which the publish job indexes once every family is in.

: "${FAMILY:?}" "${BUILD:?}" "${TAG:?}" "${KIND:?}" "${GH_TOKEN:?}"

asset="dist/$FAMILY-$BUILD.tar.xz"

if [[ "$KIND" == stable ]]; then
  ref=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["ref"])' \
    "dist/$FAMILY-$BUILD.record.json")
  title="$FAMILY $BUILD"
  notes="$FAMILY $BUILD, built by this repository's workflow from the upstream release tag $ref, with the same configuration as the nightly $FAMILY-trunk: assertions enabled (clang: LLVM_ENABLE_ASSERTIONS=ON; gcc: --enable-checking=yes,extra,rtl), trimmed to the parts a C compile touches. Stable builds ship no sanitizer runtimes. The archive carries a provenance.json naming the exact source commit, and manifest.json indexes the release. gcc and binutils are GPLv3 -- each gcc archive carries build.log.bz2 with its configure and build details and the share/licenses/ tree for the packages it consumed; clang is Apache-2.0 -- LLVM's LICENSE.TXT ships inside the archive."
  if gh release view "$TAG" >/dev/null 2>&1; then
    # A release from before the switch to building from source holds Compiler
    # Explorer's tarball under the same name; this build replaces it.
    gh release edit "$TAG" --title "$title" --notes "$notes"
  else
    gh release create "$TAG" --title "$title" --notes "$notes"
  fi
  gh release upload "$TAG" "$asset" --clobber
  ./ccput.py manifest "$TAG"
  gh release upload "$TAG" dist/manifest.json --clobber
else
  gh release upload "$TAG" "$asset" --clobber
fi
