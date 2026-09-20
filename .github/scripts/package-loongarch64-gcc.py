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
BUILD = os.environ["BUILD"]
TAG = os.environ["TAG"]
BOOTSTRAP_VERSION = os.environ["BOOTSTRAP_VERSION"]
INSTALL = ROOT / "gcc-install"
SOURCE = ROOT / "gcc"
BUILD_DIR = ROOT / "gcc-build"


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
shutil.rmtree(staging, ignore_errors=True)
tree = staging / f"{FAMILY}-{BUILD}" / TARGET
tree.mkdir(parents=True)

# The install contains only the C/LTO GCC build. Keep its C-facing programs,
# compiler proper, startup objects, libgcc, headers, and the pinned linker/sysroot.
copy(INSTALL / "bin", tree / "bin")
copy(INSTALL / TARGET / "bin", tree / TARGET / "bin")
copy(INSTALL / TARGET / "sysroot", tree / TARGET / "sysroot")
copy(INSTALL / TARGET / "lib", tree / TARGET / "lib")
copy(INSTALL / "share" / "licenses", tree / "share" / "licenses")

for path in (INSTALL / "lib" / "gcc" / TARGET).glob("*/include*"):
    copy(path, tree / "lib" / "gcc" / TARGET / path.parent.name / path.name)
for path in (INSTALL / "lib" / "gcc" / TARGET).glob("*/*"):
    if path.is_file() and (path.name.endswith(".o") or path.name.startswith("lib")):
        copy(path, tree / "lib" / "gcc" / TARGET / path.parent.name / path.name)
for name in (
    "cc1",
    "collect2",
    "lto-wrapper",
    "liblto_plugin.so",
    "liblto_plugin.so.0",
    "liblto_plugin.so.0.0.0",
):
    for path in (INSTALL / "libexec" / "gcc" / TARGET).glob(f"*/{name}"):
        copy(path, tree / "libexec" / "gcc" / TARGET / path.parent.name / path.name)

logs = b""
for name in ("configure.log", "build.log", "install.log"):
    path = BUILD_DIR / name
    logs += f"\n===== {name} =====\n".encode() + path.read_bytes()
(tree / "build.log.bz2").write_bytes(bz2.compress(logs, compresslevel=9))

revision = subprocess.check_output(
    ["git", "-C", SOURCE, "rev-parse", "HEAD"], text=True
).strip()
driver = f"{TARGET}/bin/{TARGET}-gcc"
version = subprocess.check_output([tree.parent / driver, "--version"], text=True).strip()
bootstrap_archive = ROOT / "bootstrap.tar.xz"
provenance = {
    "family": FAMILY,
    "date": BUILD,
    "tag": TAG,
    "source": f"https://github.com/gcc-mirror/gcc/commit/{revision}",
    "gcc_revision": revision,
    "driver": driver,
    "version": version,
    "languages": ["c", "lto"],
    "gcc_checking": "yes",
    "bootstrap_source": f"https://compiler-explorer.s3.amazonaws.com/opt/loongarch64-gcc-{BOOTSTRAP_VERSION}.tar.xz",
    "bootstrap_sha256": sha256(bootstrap_archive),
}
(tree.parent / "provenance.json").write_text(json.dumps(provenance, indent=1) + "\n")

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
