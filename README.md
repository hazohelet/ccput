# ccput

Assertions-enabled gcc and clang, built from upstream source by this
repository's GitHub Actions workflow, trimmed to the parts a C compile touches
and published as GitHub releases.

Every family is built twice over:

- **nightlies**: one release per day (tag `YYYYMMDD`), holding each family's
  trunk build as `<family>-trunk-YYYYMMDD.tar.xz`. Every family in a run builds
  the same gcc or llvm-project commit, pinned when the run starts.
- **stable**: one release per major version per family, tagged
  `<family>-<version>` (`gcc-assertions-16.2.0`,
  `arm64-gcc-assertions-15.3.0`, `clang-assertions-22.1.8`), built from the
  newest upstream release tag of that major (`releases/gcc-X.Y.Z`,
  `llvmorg-X.Y.Z`) -- gcc from 9, clang from 9, loongarch64 from gcc 12 where
  it first appears. A stable build is made once; the daily run picks up new
  upstream releases on its own.

| family | target | driver |
| --- | --- | --- |
| `gcc-assertions` | x86_64 host | `bin/gcc` |
| `arm-gcc-assertions` | `arm-linux-gnueabihf` (armv7-a+fp, hard float) | `arm-linux-gnueabihf/bin/arm-linux-gnueabihf-gcc` |
| `arm64-gcc-assertions` | `aarch64-linux-gnu` | `aarch64-linux-gnu/bin/aarch64-linux-gnu-gcc` |
| `riscv64-gcc-assertions` | `riscv64-linux-gnu` | `riscv64-linux-gnu/bin/riscv64-linux-gnu-gcc` |
| `powerpc64-gcc-assertions` | `powerpc64-linux-gnu` | `powerpc64-linux-gnu/bin/powerpc64-linux-gnu-gcc` |
| `powerpc64le-gcc-assertions` | `powerpc64le-linux-gnu` | `powerpc64le-linux-gnu/bin/powerpc64le-linux-gnu-gcc` |
| `loongarch64-gcc-assertions` | `loongarch64-linux-gnu` | `loongarch64-linux-gnu/bin/loongarch64-linux-gnu-gcc` |
| `clang-assertions` | x86_64 host, every backend | `bin/clang` |

The configuration is the same for a nightly and a stable build:

- **gcc**: C and LTO only, `--enable-checking=yes,extra,rtl`, built with
  Ubuntu 24.04's own compiler; a cross build takes its binutils and its glibc
  sysroot from Ubuntu 24.04's cross packages and ships them inside the archive.
  No sanitizer runtimes are built. Older releases always configure arm with
  `--with-float=hard` (gcc 13 infers it from the triple, earlier ones do not),
  and any backport a release needs to build against Ubuntu 24.04's binutils or
  glibc lives under `patches/gcc-<major>/` and is named in the archive's
  provenance.
- **clang**: `Release` with `LLVM_ENABLE_ASSERTIONS=ON`, every backend, built
  with gcc 14. Releases older than LLVM 16 get forced
  `<cstdint>`/`<limits>`/`<string>`/`<cstdio>` includes for that host
  compiler. Only the nightly builds compiler-rt: the sanitizer runtimes of
  older releases do not build or run against the current glibc and kernel, so
  a stable clang ships the compiler and its resource headers alone.

Each archive carries a `provenance.json` naming its exact upstream commit, and
each release a `manifest.json` indexing its archives. Before an archive is
published, `selfcheck.py` checks that no binary in it needs a shared library
beyond glibc, libstdc++, zlib, zstd and tinfo unless the archive ships it (a
cross build carries its binutils' own libraries), then compiles, links and
runs a program with it at
`-O0`/`-O1`/`-O2`/`-O3`/`-Os` and checks the results agree -- a cross build
under the pinned qemu-user from `flake.nix`. The nightlies are also checked
under UBSan and ASan.

```sh
wget https://github.com/hazohelet/ccput/releases/download/gcc-assertions-16.2.0/gcc-assertions-16.2.0.tar.xz
tar -xf gcc-assertions-16.2.0.tar.xz
```

The `build` workflow (`.github/workflows/repack.yml`) runs daily and can be dispatched for a subset:
`<family>-trunk` builds that nightly, `<family>` every stable version not yet
released, `<family>-<X.Y.Z>` one stable version; `rebuild` replaces stable
versions already released. `./ccput.py plan` prints the same plan locally.

Releases from before this repository built its own compilers -- `gcc-X.Y.Z`,
`clang-X.Y.Z`, `<arch>-gcc-X.Y.Z` and so on -- are Compiler Explorer builds
repacked; nothing updates them any more. Where such a release shares its tag
with a build made here (`gcc-assertions-16.2.0`, `clang-assertions-13.0.1`,
...), the build made here replaces its archive.
