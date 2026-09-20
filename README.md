# ccput

Compiler Explorer's gcc and clang builds, repacked to the parts a C compile
touches (~1 GiB per set instead of ~11 GiB) and published as GitHub releases.
The assertions-enabled LoongArch64 GCC nightly is built from GCC trunk by CI;
the other toolchains come from Compiler Explorer's public S3 bucket, which this
mirror preserves after the bucket rotates them away.

- **nightlies**: one release per day (tag `YYYYMMDD`), every trunk family,
  each asset carrying a `provenance.json` (source URL, sha256, driver,
  revisions) and indexed by the release's `manifest.json`.
  `loongarch64-gcc-assertions-trunk` is a C-and-LTO-only GCC build with
  `--enable-checking=yes`; it reuses pinned CE binutils and a CE sysroot. Its
  reproducible build environment is available as `nix develop .#gcc-loongarch64`.
- **stable**: one release per major version per family, tagged
  `<family>-<version>` (`gcc-16.2.0`, `arm64-gcc-16.1.0`, `clang-23.1.0`),
  every major from gcc 9 / clang 10 on, host and cross (arm, arm64, riscv64,
  powerpc64, powerpc64le, loongarch64), repacked once and never repeated.

```sh
wget https://github.com/hazohelet/ccput/releases/download/gcc-16.2.0/gcc-16.2.0.tar.xz
tar -xf gcc-16.2.0.tar.xz
```
