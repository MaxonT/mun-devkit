#!/bin/sh
# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0
# Fetch the pinned Godot release the adapter is checked with into
# .local/godot/<version>/ and verify it. A file already there is verified,
# never trusted unchecked. Needs curl, unzip and shasum or sha512sum.
#
#   engines/godot/fetch.sh            the Linux ARM64 editor (runs projects in the SDK environment)
#   engines/godot/fetch.sh source     also the engine source (to build export templates)
set -eu
root=$(cd "$(dirname "$0")/../.." && pwd)
version=4.7.2-stable
dest=$root/.local/godot/$version
base=https://github.com/godotengine/godot/releases/download/$version
mkdir -p "$dest"
cd "$dest"
cat > SHA512SUMS.pinned <<'SUMS'
dd59918da086bd49bde2f5450b5e567ff8650cbde9abbd7b8f4ca1197ff8c609baa38834666d032deafb47099078d7822279e2a0e06e5665745468f26533e7e2  Godot_v4.7.2-stable_linux.arm64.zip
15e23f8423003f5d32da07c34a8651e43af23b050cc0dea2d4fba6944ca445d079edb52b66fdf82b6d5739bba1fe707b9e27b8836b19f09f64f313966160f8fc  godot-4.7.2-stable.tar.xz
SUMS
if command -v sha512sum >/dev/null 2>&1; then sum="sha512sum -c -"; else sum="shasum -a 512 -c -"; fi

fetch() {
    [ -f "$1" ] || { curl -fsSL -o "$1.part" "$base/$1" && mv "$1.part" "$1"; }
    grep "  $1\$" SHA512SUMS.pinned | $sum
}

fetch "Godot_v$version""_linux.arm64.zip"
[ -x "Godot_v$version""_linux.arm64" ] || unzip -oq "Godot_v$version""_linux.arm64.zip"
[ "${1:-}" != source ] || fetch "godot-$version.tar.xz"
echo "Godot $version is in $dest"
