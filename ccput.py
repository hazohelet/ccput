#!/usr/bin/env python3
"""Plan the builds a workflow run makes, and index what it published.

Every toolchain is built here from upstream source: a family's nightly from the
trunk commit the run pins, and its stable releases from the newest upstream
release tag of every major series from the family's `since` on. A stable build
is made once -- its release, `<family>-<version>`, existing is what marks it
done -- while the nightlies are rebuilt every day into the `YYYYMMDD` release.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = Path(os.environ.get("CCPUT_DIST", ROOT / "dist"))
FAMILIES = {
    f["family"]: f for f in json.loads((ROOT / "families.json").read_text())["families"]
}

UPSTREAM = {
    "gcc": {
        "url": "https://github.com/gcc-mirror/gcc.git",
        "tag": re.compile(r"releases/gcc-(\d+)\.(\d+)\.(\d+)"),
    },
    "clang": {
        "url": "https://github.com/llvm/llvm-project.git",
        "tag": re.compile(r"llvmorg-(\d+)\.(\d+)\.(\d+)"),
    },
}


def die(message: str):
    raise SystemExit(f"ccput: {message}")


def ls_remote(url: str, *patterns: str) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "ls-remote", url, *patterns],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    ).stdout
    return [tuple(line.split("\t", 1)) for line in out.splitlines() if "\t" in line]


def trunk_commit(compiler: str) -> str:
    """HEAD of the upstream repository, matched on the ref column exactly: a
    bare HEAD pattern also matches refs/remotes/<name>/HEAD tails."""
    heads = [sha for sha, ref in ls_remote(UPSTREAM[compiler]["url"], "HEAD") if ref == "HEAD"]
    if len(heads) != 1 or not re.fullmatch(r"[0-9a-f]{40}", heads[0]):
        die(f"could not pin the {compiler} trunk: {heads}")
    return heads[0]


def stable_versions(compiler: str, since: int) -> dict[str, str]:
    """The newest X.Y.Z of every major series from `since` on, mapped to the
    upstream tag that names it: 16.2.0 alone, not 16.1.0 and 16.2.0."""
    upstream = UPSTREAM[compiler]
    newest: dict[int, tuple[int, int, int]] = {}
    tags = {}
    for _, ref in ls_remote(upstream["url"], "refs/tags/*"):
        name = ref.removeprefix("refs/tags/")
        if m := upstream["tag"].fullmatch(name):
            version = tuple(int(g) for g in m.groups())
            tags[version] = name
            if version[0] >= since and version > newest.get(version[0], (0, 0, 0)):
                newest[version[0]] = version
    return {".".join(map(str, v)): tags[v] for _, v in sorted(newest.items())}


def built_here(tag: str) -> bool:
    """Whether a published stable release holds this workflow's own build.
    Releases from before the switch to building from source carry Compiler
    Explorer's tarball under the same tag; those are rebuilt in place."""
    repo = os.environ.get("GITHUB_REPOSITORY", "hazohelet/ccput")
    url = f"https://github.com/{repo}/releases/download/{tag}/manifest.json"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            manifest = json.load(r)
    except (urllib.error.URLError, json.JSONDecodeError):
        return False
    sources = [a.get("source", "") for a in manifest.get("assets", [])]
    return bool(sources) and all(s.startswith("https://github.com/") for s in sources)


def is_version(build: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+", build))


def wanted(selectors: set[str], family: str, build: str | None) -> bool:
    """Whether the dispatch's selectors cover one build. An empty selection is
    everything; `<family>-trunk` is the nightly; `<family>` every stable
    version; `<family>-<X.Y.Z>` one of them."""
    if not selectors:
        return True
    if build is None:
        return f"{family}-trunk" in selectors
    return family in selectors or f"{family}-{build}" in selectors


def cmd_plan(args) -> int:
    tag = args.tag or datetime.now(timezone.utc).strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}", tag):
        die("a nightly release tag is a YYYYMMDD date")
    selectors = set(args.select)
    stems = {
        re.sub(r"-(trunk|\d+\.\d+\.\d+)$", "", s) for s in selectors
    }
    if unknown := stems - FAMILIES.keys():
        die(f"unknown family {sorted(unknown)[0]!r} (known: {', '.join(FAMILIES)})")
    have = set(os.environ.get("CCPUT_RELEASES", "").split())

    pins: dict[str, str] = {}
    releases: dict[tuple[str, int], dict[str, str]] = {}
    include = []
    for name, family in FAMILIES.items():
        compiler = family["compiler"]
        common = {
            "compiler": compiler,
            "target": family["target"],
            "cross": family["cross"],
            "driver": family["driver"],
            "arch": family["arch"],
        }
        if wanted(selectors, name, None):
            if compiler not in pins:
                pins[compiler] = trunk_commit(compiler)
            include.append(
                {
                    "family": f"{name}-trunk",
                    "build": tag,
                    "tag": tag,
                    "kind": "nightly",
                    "ref": pins[compiler],
                    **common,
                }
            )
        key = (compiler, family["since"])
        if key not in releases:
            releases[key] = stable_versions(compiler, family["since"])
        for version, ref in releases[key].items():
            if not wanted(selectors, name, version):
                continue
            stable_tag = f"{name}-{version}"
            if stable_tag in have and not args.rebuild:
                if built_here(stable_tag):
                    print(f"{stable_tag}: already released", file=sys.stderr)
                    continue
                print(f"{stable_tag}: released from Compiler Explorer; rebuilding", file=sys.stderr)
            include.append(
                {
                    "family": name,
                    "build": version,
                    "tag": stable_tag,
                    "kind": "stable",
                    "ref": ref,
                    **common,
                }
            )

    gcc = [e for e in include if e["compiler"] == "gcc"]
    clang = [e for e in include if e["compiler"] == "clang"]
    nightly = any(e["kind"] == "nightly" for e in include)
    for e in include:
        print(f"{e['family']}-{e['build']}: {e['kind']} from {e['ref']}", file=sys.stderr)
    print(
        f"{len(gcc)} gcc and {len(clang)} clang builds "
        f"({sum(e['kind'] == 'stable' for e in include)} stable)",
        file=sys.stderr,
    )
    outputs = {
        "gcc_matrix": json.dumps({"include": gcc}),
        "clang_matrix": json.dumps({"include": clang}),
        "gcc_any": json.dumps(bool(gcc)),
        "clang_any": json.dumps(bool(clang)),
        "cross": json.dumps(any(e["cross"] for e in include)),
        "nightly": json.dumps(nightly),
        "tag": tag,
    }
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as f:
            for key, value in outputs.items():
                f.write(f"{key}={value}\n")
    else:
        print(json.dumps(outputs, indent=1))
    return 0


def cmd_manifest(args) -> int:
    records = sorted(DIST.rglob("*.record.json"))
    entries = [
        e
        for e in (json.loads(r.read_text()) for r in records)
        if e.get("tag") == args.tag
    ]
    if not entries:
        die(f"no records for release {args.tag} under {DIST}")
    # A partial run (one family re-dispatched) must not shrink the index:
    # merge with the manifest already on the release, fresh records winning.
    published = DIST / "manifest.json"
    if published.is_file():
        try:
            old = json.loads(published.read_text())
        except json.JSONDecodeError:
            old = {}
        if old.get("release") == args.tag:
            by_family = {e["family"]: e for e in entries}
            for entry in old.get("assets", []):
                by_family.setdefault(entry["family"], entry)
            entries = list(by_family.values())
    manifest = {
        "release": args.tag,
        "note": "Compilers built by this repository's workflow from upstream source; see each asset's provenance.",
        "families": sorted({e["family"] for e in entries}),
        "assets": sorted(entries, key=lambda e: e["family"]),
    }
    (DIST / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"manifest for {args.tag}: {len(entries)} families")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="what this run builds")
    plan.add_argument(
        "select",
        nargs="*",
        help="<family>-trunk, <family> (every stable version) or "
        "<family>-<X.Y.Z>; nothing selects everything",
    )
    plan.add_argument("--tag", default="", help="nightly release (default: today, UTC)")
    plan.add_argument(
        "--rebuild",
        action="store_true",
        help="rebuild selected stable versions even if already released",
    )
    plan.set_defaults(handler=cmd_plan)

    manifest = sub.add_parser("manifest", help="write an index of the release")
    manifest.add_argument("tag")
    manifest.set_defaults(handler=cmd_manifest)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
