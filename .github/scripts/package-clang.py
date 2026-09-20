#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
FAMILY = os.environ["FAMILY"]
DRIVER = os.environ["DRIVER"]
BUILD = os.environ["BUILD"]
TAG = os.environ["TAG"]
INSTALL = ROOT / "llvm-install"
SOURCE = ROOT / "llvm"


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
root = staging / f"{FAMILY}-{BUILD}"
root.mkdir(parents=True)

# The parts a C compile touches: the driver and its versioned twin, the
# resource directory (headers and sanitizer runtimes), and the license.
for path in (INSTALL / "bin").iterdir():
    if path.name.startswith("clang") or path.suffix == ".cfg":
        copy(path, root / "bin" / path.name)
copy(INSTALL / "lib" / "clang", root / "lib" / "clang")
copy(SOURCE / "llvm" / "include" / "llvm" / "Support" / "LICENSE.TXT",
     root / "include" / "llvm" / "Support" / "LICENSE.TXT")

revision = subprocess.check_output(
    ["git", "-C", SOURCE, "rev-parse", "HEAD"], text=True
).strip()
version = subprocess.check_output([root / DRIVER, "--version"], text=True).strip()
provenance = {
    "family": FAMILY,
    "date": BUILD,
    "tag": TAG,
    "source": f"https://github.com/llvm/llvm-project/commit/{revision}",
    "llvm_revision": revision,
    "driver": DRIVER,
    "version": version,
    "assertions": True,
}
(root / "provenance.json").write_text(json.dumps(provenance, indent=1) + "\n")

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
        root.name,
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
