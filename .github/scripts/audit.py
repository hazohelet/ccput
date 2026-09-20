#!/usr/bin/env python3
"""Compare the bucket against this mirror: which gcc/clang families and
trunk nightlies exist upstream that the mirror does not track."""

import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

BUCKET = "https://compiler-explorer.s3.amazonaws.com"
ROOT = Path(__file__).resolve().parents[2]
FAMILIES = json.loads((ROOT / "families.json").read_text())["families"]
NAMES = {f["name"] for f in FAMILIES}
# Families whose nightly is built in CI, not repacked from the bucket; the
# bucket's nightly tarballs for them are not consumed and never stale.
BUCKET_TRUNK = {f["name"] for f in FAMILIES if f.get("nightly") != "build"}


def bucket_keys():
    token, out = None, []
    for _ in range(100):
        query = {"list-type": "2", "prefix": "opt/"}
        if token:
            query["continuation-token"] = token
        with urllib.request.urlopen(
            f"{BUCKET}/?{urllib.parse.urlencode(query)}", timeout=60
        ) as r:
            root = ET.fromstring(r.read())
        ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
        out += [e.text for e in root.iter(ns + "Key")]
        token = root.findtext(ns + "NextContinuationToken")
        if root.findtext(ns + "IsTruncated") != "true":
            return out
    raise SystemExit("audit: the listing did not finish in 100 pages")


stable = defaultdict(set)
trunk = defaultdict(set)
for key in bucket_keys():
    if m := re.fullmatch(r"opt/([a-z0-9-]+)-(\d+\.\d+\.\d+)\.tar\.xz", key):
        stable[m.group(1)].add(m.group(2))
    elif m := re.fullmatch(r"opt/([a-z0-9-]+-trunk)-(\d{8})\.tar\.xz", key):
        trunk[m.group(1)].add(m.group(2))

gccish = lambda n: n.endswith("gcc") or "gcc-" in n or n.startswith("clang")
ours_stable = {n.removesuffix("-trunk") for n in NAMES}

IGNORED_TRUNK = {"bpf-gcc-trunk", "riscv32-gcc-trunk"}

print("== arch-gcc trunk families the bucket builds but the mirror does not track")
# <arch>-gcc-trunk is the shape of a family the mirror would want; the bucket's
# many clang-p*/gcc-contracts* trunk builds are Compiler Explorer's
# experimental forks, counted below but never a finding.
candidate = (
    lambda f: re.fullmatch(r"[a-z0-9]+-gcc-trunk", f)
    and f not in NAMES
    and f not in IGNORED_TRUNK
)
new_trunk = sorted(f for f in trunk if candidate(f))
for f in new_trunk:
    print(f"  {f} (newest nightly {max(trunk[f])})")
if not new_trunk:
    print("  none")
forks = sorted(f for f in trunk if f not in NAMES and not candidate(f))
print(
    f"(plus {len(forks)} fork or declined trunk builds, out of scope: "
    + ", ".join(forks[:6])
    + ", ...)" if forks else ""
)

print("== stable families the bucket holds but the mirror does not release")
extra = sorted(
    (f, len(v)) for f, v in stable.items() if f not in ours_stable and gccish(f)
)
for f, n in extra:
    print(f"  {f} ({n} versions)")
if not extra:
    print("  none")

print("== tracked trunk families, newest nightly and its age")
today = subprocess.run(
    ["date", "-u", "+%Y%m%d"], capture_output=True, text=True
).stdout.strip()
stale = []
for name in sorted(BUCKET_TRUNK & set(trunk)):
    newest = max(trunk[name])
    age = int(today) - int(newest)
    mark = ""
    if age > 7:
        mark = f"  STALE ({age} days)"
        stale.append(name)
    print(f"  {name}: {newest}{mark}")

if new_trunk:
    print(
        f"\naudit: {len(new_trunk)} untracked trunk famil"
        f"{'y' if len(new_trunk) == 1 else 'ies'} above; add "
        f"{'it' if len(new_trunk) == 1 else 'them'} to families.json or ignore",
        file=sys.stderr,
    )
    sys.exit(1)
print("\naudit: nothing untracked")
