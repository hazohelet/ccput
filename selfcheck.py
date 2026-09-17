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
    "arm-gcc": "arm",
    "arm64-gcc": "aarch64",
    "riscv64-gcc": "riscv64",
    "powerpc64-gcc": "ppc64",
    "powerpc64le-gcc": "ppc64le",
    "loongarch64-gcc": "loongarch64",
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

        probe = work / "probe.c"
        probe.write_text(PROBE)

        host = family.removesuffix("-trunk") in (
            "gcc",
            "gcc-assertions",
            "clang",
            "clang-assertions",
        )
        link = [] if host else ["-static"]

        for opt in ("-O0", "-O2", "-Os"):
            subprocess.run(
                [driver, "-w", opt, *link, probe, "-o", work / "probe"], check=True
            )
        print(f"selfcheck: {family}-{build} compiles and links at -O0/-O2/-Os")

        if not host:
            kind = subprocess.run(
                ["file", "-b", work / "probe"], capture_output=True, text=True
            ).stdout.strip()
            if not re.search(r"ELF|executable", kind, re.I):
                die("not an executable")
            print(f"selfcheck: {family}-{build} links to {kind}")
            subprocess.run(
                [
                    driver,
                    "-w",
                    "-O1",
                    "-fsanitize=undefined,address",
                    "-fno-sanitize-recover=all",
                    "-c",
                    probe,
                    "-o",
                    work / "probe-san.o",
                ],
                check=True,
            )
            print(
                f"selfcheck: {family}-{build} instruments under UBSan and ASan "
                "(no target runtime to link)"
            )
            arch = QEMU_ARCH.get(family.removesuffix("-trunk"))
            if os.environ.get("CCPUT_QEMU") and arch:
                emulator = Path(os.environ["CCPUT_QEMU"]) / f"qemu-{arch}"
                ran = subprocess.run(
                    [emulator, work / "probe"], capture_output=True, text=True
                )
                if ran.returncode != 0 or not ran.stdout.strip().isdigit():
                    die(
                        f"qemu-{arch} could not run the probe "
                        f"(exit {ran.returncode}): {ran.stderr.strip()[:200]}"
                    )
                print(f"selfcheck: {family}-{build} runs under qemu-{arch}")
            return 0

        expected = subprocess.run(
            [work / "probe"], capture_output=True, text=True
        ).stdout
        if not expected:
            die("the probe printed nothing")
        print(f"selfcheck: {family}-{build} runs")

        subprocess.run(
            [
                driver,
                "-w",
                "-O1",
                *link,
                "-fsanitize=undefined,address",
                "-fno-sanitize-recover=all",
                probe,
                "-o",
                work / "probe-san",
            ],
            check=True,
        )
        got = subprocess.run(
            [work / "probe-san"], capture_output=True, text=True
        ).stdout
        if got != expected:
            die(f"sanitised build printed {got!r}, expected {expected!r}")
        print(f"selfcheck: {family}-{build} builds and runs clean under UBSan and ASan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
