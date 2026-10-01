#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

QEMU_ARCH = {
    "arm-gcc-assertions": "arm",
    "arm64-gcc-assertions": "aarch64",
    "riscv64-gcc-assertions": "riscv64",
    "powerpc64-gcc-assertions": "ppc64",
    "powerpc64le-gcc-assertions": "ppc64le",
    "loongarch64-gcc-assertions": "loongarch64",
}

PROBE = r"""
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
int main(int argc, char **argv) {
  (void)argv;
  uint64_t crc = 0;
  uint64_t *v = malloc(4 * sizeof *v);
  if (!v) return 1;
  for (int i = 0; i < 4; i++) v[i] = (uint64_t)(i + argc);
  for (int i = 0; i < 4; i++) crc = crc * 1000003ULL + v[i];
  free(v);
  printf("%llu\n", (unsigned long long)crc);
  return 0;
}
"""


OPTS = ("-O0", "-O1", "-O2", "-O3", "-Os")


# Shared libraries an archive may expect of the system it is unpacked on:
# glibc, the C++ runtime, and compression and terminal libraries every
# distribution installs. Anything else a binary needs must ship in the archive.
SYSTEM_LIBS = re.compile(
    r"(ld-linux-x86-64|libc|libm|libdl|libpthread|librt|libstdc\+\+|libgcc_s"
    r"|libz|libzstd|libtinfo)\.so(\.\d+)*"
)


def portable(tree: Path) -> None:
    """Every ELF in the archive must find its libraries on any system."""
    shipped = {p.name for p in tree.rglob("*.so*")}
    for path in sorted(tree.rglob("*")):
        if not path.is_file() or path.is_symlink() or "sysroot" in path.parts:
            continue
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        dynamic = subprocess.run(
            ["readelf", "-d", path], capture_output=True, text=True
        ).stdout
        for lib in re.findall(r"\(NEEDED\)\s+Shared library: \[(.+?)\]", dynamic):
            if not (SYSTEM_LIBS.fullmatch(lib) or lib in shipped):
                die(f"{path.relative_to(tree)} needs {lib}, which the archive does not ship")


def die(message: str) -> None:
    raise SystemExit(f"selfcheck: {message}")


def main() -> int:
    if len(sys.argv) != 3:
        die("usage: selfcheck.py FAMILY BUILD (a YYYYMMDD date or X.Y.Z version)")
    family, build = sys.argv[1], sys.argv[2]
    dist = Path(os.environ.get("CCPUT_DIST", ROOT / "dist"))
    asset = dist / f"{family}-{build}.tar.xz"

    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        with tarfile.open(asset) as archive:
            archive.extractall(work, filter="tar")
        tree = work / f"{family}-{build}"

        record = tree / "provenance.json"
        if not record.is_file():
            die(f"{asset} carries no provenance.json")
        driver = tree / json.loads(record.read_text())["driver"]
        if not (driver.is_file() and os.access(driver, os.X_OK)):
            die(f"{driver} is not executable")

        portable(tree)
        print(f"selfcheck: {family}-{build} needs no library beyond the base system")

        probe = work / "probe.c"
        probe.write_text(PROBE)

        stem = family.removesuffix("-trunk")
        host = stem in ("gcc-assertions", "clang-assertions")
        link = [] if host else ["-static"]
        arch = QEMU_ARCH.get(stem)
        emulator = (
            Path(os.environ["CCPUT_QEMU"]) / f"qemu-{arch}"
            if os.environ.get("CCPUT_QEMU") and arch
            else None
        )

        # Every optimisation level must give a program that runs and agrees
        # with the others: an assertions compiler that miscompiles the probe,
        # or links a binary the target cannot run, is not published.
        outputs = {}
        for opt in OPTS:
            binary = work / f"probe{opt}"
            subprocess.run([driver, "-w", opt, *link, probe, "-o", binary], check=True)
            if not host:
                kind = subprocess.run(
                    ["file", "-b", binary], capture_output=True, text=True
                ).stdout.strip()
                if not re.search(r"ELF|executable", kind, re.I):
                    die(f"{opt} did not link an executable: {kind}")
                if emulator is None:
                    continue
            ran = subprocess.run(
                [binary] if host else [emulator, binary], capture_output=True, text=True
            )
            if ran.returncode != 0 or not ran.stdout.strip().isdigit():
                die(
                    f"the {opt} probe failed (exit {ran.returncode}): "
                    f"{ran.stderr.strip()[:200]}"
                )
            outputs[opt] = ran.stdout
        if len(set(outputs.values())) > 1:
            die(f"the optimisation levels disagree: {outputs}")
        where = "natively" if host else f"under qemu-{arch}" if emulator else "(not run)"
        print(
            f"selfcheck: {family}-{build} compiles, links and runs {where} at "
            f"{'/'.join(OPTS)}"
        )

        # The sanitizers are checked on the nightlies alone: the runtimes of
        # older releases do not build or run against the current glibc and
        # kernel, so stable builds ship without them.
        if re.fullmatch(r"\d+\.\d+\.\d+", build):
            return 0
        instrument = [
            driver,
            "-w",
            "-O1",
            "-fsanitize=undefined,address",
            "-fno-sanitize-recover=all",
        ]
        if not host or stem == "gcc-assertions":
            # The gcc builds are C+LTO only and ship no sanitizer runtimes, and
            # a cross build has no target runtime to link: instrumentation
            # compiles, links are not expected to.
            subprocess.run([*instrument, "-c", probe, "-o", work / "probe-san.o"], check=True)
            print(f"selfcheck: {family}-{build} instruments under UBSan and ASan")
            return 0
        subprocess.run([*instrument, probe, "-o", work / "probe-san"], check=True)
        got = subprocess.run([work / "probe-san"], capture_output=True, text=True).stdout
        if got != outputs["-O0"]:
            die(f"sanitised build printed {got!r}, expected {outputs['-O0']!r}")
        print(f"selfcheck: {family}-{build} builds and runs clean under UBSan and ASan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
