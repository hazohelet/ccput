#!/usr/bin/env python3
from __future__ import annotations

import bz2
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
FAMILY = os.environ["FAMILY"]
TARGET = os.environ["TARGET"]
DRIVER = os.environ["DRIVER"]
BUILD = os.environ["BUILD"]
TAG = os.environ["TAG"]
INSTALL = ROOT / "gcc-install"
SOURCE = ROOT / "gcc"
BUILD_DIR = ROOT / "gcc-build"
# Dumped by the build job: the exact cross packages the toolchain consumed.
cross_txt = ROOT / "ubuntu-cross.txt"
cross_source = " ".join(cross_txt.read_text().split()) if cross_txt.is_file() else "Ubuntu 24.04 archive"


def copy(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


DIST.mkdir(exist_ok=True)
staging = DIST / f".staging-{FAMILY}-{BUILD}"
subprocess.run(["chmod", "-R", "u+w", staging], check=False)
shutil.rmtree(staging, ignore_errors=True)

# A cross build nests everything under the target triplet (matching the
# Compiler Explorer layout); a native build lays it out flat, like CE's host
# archives: bin/, lib/, lib64/, libexec/ directly under the family root.
cross_build = (INSTALL / TARGET / "sysroot").is_dir()
tree = staging / f"{FAMILY}-{BUILD}"
if cross_build:
    tree = tree / TARGET
tree.mkdir(parents=True)

# The install contains only the C/LTO GCC build. Keep its C-facing programs,
# compiler proper, startup objects, libgcc, headers, and the target tools and
# sysroot the cross build assembled.
copy(INSTALL / "bin", tree / "bin")
if cross_build:
    for name in ("bin", "lib"):
        source = INSTALL / TARGET / name
        if source.is_dir():
            copy(source, tree / name)
    sysroot = INSTALL / TARGET / "sysroot"
    copy(sysroot / "usr" / "include", tree / "sysroot" / "usr" / "include")
    copy(sysroot / "usr" / "lib", tree / "sysroot" / "usr" / "lib")
    # Recreate the alias symlinks the build script set up: one real lib
    # directory, every name the linker or the loader may look for.
    (tree / "sysroot" / "lib").symlink_to("usr/lib")
    (tree / "sysroot" / "lib64").symlink_to("usr/lib")
    (tree / "sysroot" / "usr" / "lib64").symlink_to("lib")
else:
    for name in ("lib", "lib64"):
        source = INSTALL / name
        if source.is_dir():
            copy(source, tree / name)
copy(INSTALL / "share" / "licenses", tree / "share" / "licenses")

for path in (INSTALL / "lib" / "gcc" / TARGET).glob("*/include*"):
    copy(path, tree / "lib" / "gcc" / TARGET / path.parent.name / path.name)
for path in (INSTALL / "lib" / "gcc" / TARGET).glob("*/*"):
    if path.is_file() and (path.name.endswith(".o") or path.name.startswith("lib")):
        copy(path, tree / "lib" / "gcc" / TARGET / path.parent.name / path.name)
for name in (
    "cc1",
    "collect2",
    "lto1",
    "lto-wrapper",
    "liblto_plugin.so",
    "liblto_plugin.so.0",
    "liblto_plugin.so.0.0.0",
):
    for path in (INSTALL / "libexec" / "gcc" / TARGET).glob(f"*/{name}"):
        copy(path, tree / "libexec" / "gcc" / TARGET / path.parent.name / path.name)

# lto-dump only inspects LTO bytecode; no compile ever runs it.
for path in tree.glob("bin/*lto-dump*"):
    path.unlink()

logs = b""
for name in ("configure.log", "build.log", "install.log"):
    path = BUILD_DIR / name
    logs += f"\n===== {name} =====\n".encode() + path.read_bytes()
(tree / "build.log.bz2").write_bytes(bz2.compress(logs, compresslevel=9))

revision = subprocess.check_output(
    ["git", "-C", SOURCE, "rev-parse", "HEAD"], text=True
).strip()
version = subprocess.check_output([tree.parent / DRIVER, "--version"], text=True).strip()
provenance = {
    "family": FAMILY,
    "date": BUILD,
    "tag": TAG,
    "source": f"https://github.com/gcc-mirror/gcc/commit/{revision}",
    "gcc_revision": revision,
    "driver": DRIVER,
    "version": version,
    "languages": ["c", "lto"],
    "gcc_checking": "yes",
    "cross_tools_source": cross_source,
}
(tree.parent / "provenance.json").write_text(json.dumps(provenance, indent=1) + "\n")

# The build is -g with checking, and the debug info dwarfs the code. Keep the
# symbol tables, drop the line info, and leave the pinned binutils untouched.
def strip_debug(path: Path) -> None:
    with path.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            return
    subprocess.run(["strip", "--strip-debug", path], check=True)


patterns = (
    (f"{TARGET}-gcc*", f"{TARGET}-cpp", f"{TARGET}-gcov*")
    if cross_build
    else ("gcc", "gcc-*", "cpp", "gcov", "gcov-*")
)
for pattern in patterns:
    for path in (tree / "bin").glob(pattern):
        if not path.is_symlink():
            strip_debug(path)
for path in (tree / "libexec" / "gcc" / TARGET).glob("*/*"):
    if path.is_file() and not path.is_symlink():
        strip_debug(path)

asset = DIST / f"{FAMILY}-{BUILD}.tar.xz"
asset.unlink(missing_ok=True)
subprocess.run(
    [
        "tar",
        "--sort=name",
        "--owner=0",
        "--group=0",
        "--numeric-owner",
        "--mtime=@0",
        "--mode=u=rwX,go=rX",
        "-I",
        "xz --threads=1 -6",
        "-cf",
        asset,
        "-C",
        staging,
        tree.parent.name,
    ],
    check=True,
)
# The bootstrap sysroots ship read-only directories; copytree preserves their
# modes, so make staging user-writable again before removing it.
subprocess.run(["chmod", "-R", "u+w", staging], check=True)
shutil.rmtree(staging)

record = {
    **provenance,
    "asset": asset.name,
    "asset_bytes": asset.stat().st_size,
    "asset_sha256": sha256(asset),
}
(DIST / f"{FAMILY}-{BUILD}.record.json").write_text(
    json.dumps(record, indent=1) + "\n"
)
print(f"packaged {asset} ({asset.stat().st_size / 2**20:.0f} MiB)")
