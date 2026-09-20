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
# The sysroot is a full target rootfs; keep the parts a C compile touches and
# drop the other languages' runtimes, as the repacked families' drop list does.
for part in ("usr/include", "usr/lib64", "lib64"):
    copy(INSTALL / TARGET / "sysroot" / part, tree / TARGET / "sysroot" / part)
RUNTIME_PREFIXES = (
    "libga68",
    "libgdruntime",
    "libgfortran",
    "libgphobos",
    "libstdc++",
    "libsupc++",
    "libobjc",
)
for path in (INSTALL / TARGET / "sysroot" / "lib").iterdir():
    if path.is_dir() or not path.name.startswith(RUNTIME_PREFIXES):
        copy(path, tree / TARGET / "sysroot" / "lib" / path.name)
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
    "lto1",
    "lto-wrapper",
    "liblto_plugin.so",
    "liblto_plugin.so.0",
    "liblto_plugin.so.0.0.0",
):
    for path in (INSTALL / "libexec" / "gcc" / TARGET).glob(f"*/{name}"):
        copy(path, tree / "libexec" / "gcc" / TARGET / path.parent.name / path.name)

# lto-dump only inspects LTO bytecode; no compile ever runs it.
for path in tree.glob(f"bin/{TARGET}-lto-dump*"):
    path.unlink()

# The build is -g with checking, and the debug info dwarfs the code. Keep the
# symbol tables, drop the line info, and leave the pinned binutils untouched.
def strip_debug(path: Path) -> None:
    with path.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            return
    subprocess.run(["strip", "--strip-debug", path], check=True)


for pattern in (f"{TARGET}-gcc*", f"{TARGET}-cpp", f"{TARGET}-gcov*"):
    for path in (tree / "bin").glob(pattern):
        if not path.is_symlink():
            strip_debug(path)
for path in (tree / "libexec" / "gcc" / TARGET).glob("*/*"):
    if path.is_file() and not path.is_symlink():
        strip_debug(path)

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
# The bootstrap sysroot ships read-only directories; copytree preserves their
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
