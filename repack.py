#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

BUCKET = "https://compiler-explorer.s3.amazonaws.com"
ROOT = Path(__file__).resolve().parent
DIST = Path(os.environ.get("CCPUT_DIST", ROOT / "dist"))
TABLE = json.loads((ROOT / "families.json").read_text())
FAMILIES = {f["name"]: f for f in TABLE["families"]}
DROP = TABLE["drop"]
BUILT = {f["family"]: f for f in TABLE["built"]}


def die(message: str):
    raise SystemExit(f"repack: {message}")


def component_re(pattern: str) -> re.Pattern:
    out = ""
    for ch in pattern:
        out += "[^/]*" if ch == "*" else r"\d+" if ch == "#" else re.escape(ch)
    return re.compile(out + r"\Z")


def matches(pattern: str, path: str) -> bool:
    prefix = pattern.endswith("/")
    pat = pattern.rstrip("/").split("/")
    parts = path.split("/")
    if len(parts) < len(pat) or (not prefix and len(parts) != len(pat)):
        return False
    return all(component_re(p).match(t) for p, t in zip(pat, parts))


def wanted(family: dict, path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(matches(k, path) for k in family["keep"]) and not any(
        component_re(d).match(name) for d in DROP
    )


def bucket_keys(prefix: str):
    """Every key under `prefix`, paging the S3 listing to the end."""
    token = None
    for _ in range(100):
        query = {"list-type": "2", "prefix": prefix}
        if token:
            query["continuation-token"] = token
        with urllib.request.urlopen(
            f"{BUCKET}/?{urllib.parse.urlencode(query)}", timeout=60
        ) as r:
            root = ET.fromstring(r.read())
        local = lambda e: e.tag.rsplit("}", 1)[-1]
        truncated = False
        for element in root:
            if local(element) == "Contents":
                for child in element:
                    if local(child) == "Key":
                        yield child.text
            elif local(element) == "IsTruncated" and element.text == "true":
                truncated = True
            elif local(element) == "NextContinuationToken":
                token = element.text
        if not truncated:
            return
    die(f"the listing for {prefix} did not finish in 100 pages")


def newest_date(family: str) -> str | None:
    """The newest date the bucket holds a nightly tarball for `family`."""
    pat = re.compile(rf"opt/{re.escape(family)}-(\d{{8}})\.tar\.xz")
    dates = {
        m.group(1) for k in bucket_keys(f"opt/{family}-") if (m := pat.fullmatch(k))
    }
    return max(dates) if dates else None


def is_version(build: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+", build))


def version_key(version: str) -> list[int]:
    return [int(part) for part in version.split(".")]


def stable_builds(name: str) -> list[str]:
    """The newest version of every major series the bucket holds a stable
    tarball for: 16.2.0 alone, not 16.0.0/16.1.0/16.2.0. Series older than a
    floor that keeps the set useful are dropped (gcc from 9, clang from 10).
    """
    pat = re.compile(rf"opt/{re.escape(name)}-(\d+)\.(\d+)\.(\d+)\.tar\.xz")
    newest: dict[int, str] = {}
    for key in bucket_keys(f"opt/{name}-"):
        if m := pat.fullmatch(key):
            version = f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
            major = int(m.group(1))
            if major not in newest or version_key(version) > version_key(
                newest[major]
            ):
                newest[major] = version
    floor = 10 if name.startswith("clang") else 9
    return [newest[major] for major in sorted(newest) if major >= floor]


def source_url(family: str, date: str) -> str:
    return f"{BUCKET}/opt/{family}-{date}.tar.xz"


def repacked_member(
    member: tarfile.TarInfo, family: dict, date: str
) -> tarfile.TarInfo | None:
    root = f"{family['source_root']}-{date}"
    whole = member.name.lstrip("./").rstrip("/")
    if not whole or whole == "." or whole == root:
        return None
    if not whole.startswith(root + "/"):
        die(f"the tarball holds {member.name!r}, expected everything under {root}/")
    rest = whole[len(root) + 1 :]
    if not wanted(family, rest):
        return None
    member.name = f"{family['name']}-{date}/{rest}"
    if member.islnk():
        member.linkname = (
            f"{family['name']}-{date}/" + member.linkname.lstrip("./")[len(root) + 1 :]
        )
    if member.isdir():
        member.mode |= 0o700
    return member


def remove_tree(tree: Path):
    if tree.exists():
        subprocess.run(["chmod", "-R", "u+rwX", str(tree)], check=False)
    subprocess.run(["rm", "-rf", str(tree)], check=True)


def unpack(family: dict, date: str, into: Path) -> tuple[int, str, int, int]:
    url = source_url(family["name"], date)
    curl = subprocess.Popen(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--connect-timeout",
            "30",
            "--speed-limit",
            "1024",
            "--speed-time",
            "120",
            url,
        ],
        stdout=subprocess.PIPE,
    )
    digest, size = hashlib.sha256(), 0

    class Hashing:
        def read(self, n: int = -1) -> bytes:
            nonlocal size
            chunk = curl.stdout.read(n)
            digest.update(chunk)
            size += len(chunk)
            return chunk

    xz = subprocess.Popen(
        ["xz", "--decompress", "--stdout"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    import threading

    pump_error: list[BaseException] = []

    def pump():
        try:
            source = Hashing()
            while chunk := source.read(1 << 20):
                xz.stdin.write(chunk)
        except BaseException as e:
            pump_error.append(e)
        finally:
            try:
                xz.stdin.close()
            except BrokenPipeError:
                pass

    pumping = threading.Thread(target=pump, daemon=True)
    pumping.start()

    kept = skipped = 0

    def members(archive: tarfile.TarFile):
        nonlocal kept, skipped
        for member in archive:
            rewritten = repacked_member(member, family, date)
            if rewritten is None:
                skipped += 1
                continue
            kept += 1
            yield rewritten

    with tarfile.open(fileobj=xz.stdout, mode="r|") as archive:
        archive.extractall(into, members=members(archive), filter="tar")

    pumping.join()
    if pump_error:
        raise pump_error[0]
    if xz.wait() != 0:
        die(f"xz failed decompressing {family['name']}-{date}")
    if curl.wait() != 0:
        die(f"curl failed downloading {url}")
    return size, digest.hexdigest(), kept, skipped


def revisions(version: str) -> dict:
    """The commit each compiler was built from, as its own --version reports it:
    a `Compiler-Explorer-Build-gcc-<rev>` tag for gcc, and the llvm-project git
    hash clang appends for clang."""
    out = {}
    if "Compiler-Explorer-Build-gcc-" in version:
        out["gcc_revision"] = version.split("Compiler-Explorer-Build-gcc-")[1].split(
            "-"
        )[0]
    if m := re.search(r"llvm-project\.git\s+([0-9a-f]{7,40})", version):
        out["llvm_revision"] = m.group(1)
    return out


def describe(tree: Path, family: dict, date: str) -> dict:
    driver = tree / f"{family['name']}-{date}" / family["driver"]
    if not driver.is_file():
        die(
            f"{driver} is missing after unpacking; families.json names the wrong driver, "
            f"or its keep patterns do not cover it"
        )
    version = subprocess.run(
        [driver, "--version"], capture_output=True, text=True, timeout=120
    )
    about = {"driver": family["driver"], "version": version.stdout.strip()}
    about.update(revisions(about["version"]))
    for log in (tree / f"{family['name']}-{date}").rglob("build.log.bz2"):
        found = subprocess.run(
            f"bzcat {log} | grep -aoE 'gcc-git-[0-9a-f]+' | head -1",
            shell=True,
            capture_output=True,
            text=True,
        )
        if found.stdout.strip():
            about["gcc_revision"] = found.stdout.strip().removeprefix("gcc-git-")
        break
    return about


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(name: str, build: str) -> dict:
    """The bucket-facing family a `repack NAME BUILD` addresses.

    BUILD is a YYYYMMDD date (a nightly of the trunk family) or an X.Y.Z
    version (a stable release). A stable tarball lives under the family's
    name without `-trunk` (gcc-trunk -> gcc) and unpacks under its
    source_root without `-trunk` (gcc-trunk -> gcc), everything else —
    driver, keep patterns — being the same tree.
    """
    if name not in FAMILIES and name + "-trunk" in FAMILIES:
        name += "-trunk"
    family = FAMILIES.get(name) or die(f"unknown family {name!r}")
    if is_version(build):
        family = {
            **family,
            "name": family["name"].removesuffix("-trunk"),
            "source_root": family["source_root"].removesuffix("-trunk"),
        }
    return family


def provenance_record(
    family: str,
    build: str,
    size: int,
    sha: str,
    kept: int,
    skipped: int,
    about: dict,
    tag: str,
) -> dict:
    return {
        "family": family,
        "version" if is_version(build) else "date": build,
        "tag": tag,
        "source": source_url(family, build),
        "source_bytes": size,
        "source_sha256": sha,
        "kept_files": kept,
        "skipped_files": skipped,
        **about,
    }


def index_entry(provenance: dict, packed: Path) -> dict:
    return {
        **provenance,
        "asset": packed.name,
        "asset_bytes": packed.stat().st_size,
        "asset_sha256": sha256_of(packed),
    }


def cmd_repack(args) -> int:
    build = args.build
    if not re.fullmatch(r"\d{8}|\d+\.\d+\.\d+", build):
        die("a build is a YYYYMMDD date or an X.Y.Z version")
    family = resolve(args.family, build)
    tag = args.tag or (
        f"{family['name']}-{build}"
        if is_version(build)
        else datetime.now(timezone.utc).strftime("%Y%m%d")
    )
    DIST.mkdir(exist_ok=True)
    tree = DIST / f".staging-{family['name']}-{build}"
    remove_tree(tree)
    tree.mkdir(parents=True)

    print(
        f"{family['name']}-{build}: downloading {source_url(family['name'], build)}",
        flush=True,
    )
    size, sha, kept, skipped = unpack(family, build, tree)
    about = describe(tree, family, build)

    provenance = provenance_record(
        family["name"], build, size, sha, kept, skipped, about, tag
    )
    (tree / f"{family['name']}-{build}" / "provenance.json").write_text(
        json.dumps(provenance, indent=1) + "\n"
    )

    packed = DIST / f"{family['name']}-{build}.tar.xz"
    packed.unlink(missing_ok=True)
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
            str(packed),
            "-C",
            str(tree),
            f"{family['name']}-{build}",
        ],
        check=True,
    )
    remove_tree(tree)

    record = index_entry(provenance, packed)
    (DIST / f"{family['name']}-{build}.record.json").write_text(
        json.dumps(record, indent=1) + "\n"
    )
    print(
        f"{family['name']}-{build}: {size / 2**20:.0f} MiB in -> {record['asset_bytes'] / 2**20:.0f} MiB out "
        f"({kept} files kept, {skipped} dropped)",
        flush=True,
    )
    return 0


def cmd_plan(args) -> int:
    tag = args.tag or datetime.now(timezone.utc).strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}", tag):
        die("a nightly release tag is a YYYYMMDD date")
    requested = set(args.family)
    unknown = requested - FAMILIES.keys() - BUILT.keys()
    if unknown:
        die(f"unknown family {sorted(unknown)[0]!r}")
    have = set(os.environ.get("CCPUT_RELEASES", "").split())

    # The gcc nightly families this workflow builds itself, not repacks.
    build_include = []
    for name, family in BUILT.items():
        if requested and name not in requested:
            continue
        if f"{name}-{tag}" in have:
            print(f"{name}-{tag}: already released", file=sys.stderr)
            continue
        build_include.append(
            {
                "family": name,
                "target": family["target"],
                "cross": family["cross"],
                "driver": family["driver"],
                "arch": family["arch"],
                "shims": family["shims"],
                "tag": tag,
            }
        )
    build_any = bool(build_include)

    include, nightly = [], 0
    bucket_families = (
        [name for name in args.family if name in FAMILIES] if requested else FAMILIES
    )
    for name in bucket_families:
        if FAMILIES[name].get("nightly") != "build":
            date = newest_date(name)
            if date is None:
                print(f"{name}: nothing in the bucket", file=sys.stderr)
            else:
                include.append(
                    {"family": name, "build": date, "tag": tag, "kind": "nightly"}
                )
                nightly += 1
        stable = name.removesuffix("-trunk")
        for version in stable_builds(stable):
            stable_tag = f"{stable}-{version}"
            if stable_tag in have:
                print(f"{stable_tag}: already released", file=sys.stderr)
                continue
            head = {}
            if stable in ("gcc", "gcc-assertions") and int(
                version.split(".")[0]
            ) < 12:
                head = {"os": "ubuntu-22.04"}
            include.append(
                {
                    "family": stable,
                    "build": version,
                    "tag": stable_tag,
                    "kind": "stable",
                    **head,
                }
            )
    matrix = {"include": include}
    build_matrix = {"include": build_include}
    print(json.dumps(matrix))
    print(
        f"{len(include)} to repack ({nightly} nightly, {len(include) - nightly} stable); "
        f"{len(build_include)} gcc assertions build"
        f"{'' if len(build_include) == 1 else 's'}",
        file=sys.stderr,
    )
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as f:
            f.write(f"matrix={json.dumps(matrix)}\n")
            f.write(f"build_matrix={json.dumps(build_matrix)}\n")
            f.write(f"any={'true' if include else 'false'}\n")
            f.write(f"build_any={'true' if build_any else 'false'}\n")
            f.write(f"nightly={'true' if nightly or build_any else 'false'}\n")
            hosts = ("gcc", "gcc-assertions", "clang", "clang-assertions")
            cross = any(
                e["family"].removesuffix("-trunk") not in hosts for e in include
            ) or any(e["cross"] for e in build_include)
            f.write(f"cross={'true' if cross else 'false'}\n")
            f.write(f"tag={tag}\n")
    return 0


def cmd_manifest(args) -> int:
    records = sorted(DIST.rglob("*.record.json"))
    if not records:
        die(f"no records under {DIST}")
    entries = [
        e
        for e in (json.loads(r.read_text()) for r in records)
        if e.get("tag") == args.tag
    ]
    if not entries:
        die(f"no records for release {args.tag} under {DIST}")
    manifest = {
        "release": args.tag,
        "note": "Compiler nightlies, either repacked from Compiler Explorer or built by this workflow; see each asset's provenance.",
        "families": sorted({e["family"] for e in entries}),
        "assets": sorted(entries, key=lambda e: e["family"]),
    }
    (DIST / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"manifest for {args.tag}: {len(entries)} families")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="what the bucket has that the release lacks")
    plan.add_argument("family", nargs="*")
    plan.add_argument(
        "--tag", default="", help="release to write to (default: today, UTC)"
    )
    plan.set_defaults(handler=cmd_plan)

    repack = sub.add_parser(
        "repack", help="download one build and write a smaller one"
    )
    repack.add_argument("family", help="e.g. gcc-trunk, or gcc for a stable build")
    repack.add_argument(
        "build", help="a YYYYMMDD date (nightly) or X.Y.Z version (stable)"
    )
    repack.add_argument(
        "--tag", default="", help="release this asset belongs to (default: the tag "
        "plan would give it: today for a nightly, <family>-<version> for stable)"
    )
    repack.set_defaults(handler=cmd_repack)

    manifest = sub.add_parser("manifest", help="write an index of the release")
    manifest.add_argument("tag")
    manifest.set_defaults(handler=cmd_manifest)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
