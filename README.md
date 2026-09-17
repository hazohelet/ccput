# ccput

Compiler Explorer's gcc and clang builds, repacked to the parts a C compile
touches (~1 GiB per set instead of ~11 GiB) and published as GitHub releases.
Nothing is built here: every byte comes from Compiler Explorer's public S3
bucket; this mirror keeps what the bucket rotates away.

- **nightlies**: one release per day (tag `YYYYMMDD`), every trunk family,
  each asset carrying a `provenance.json` (source URL, sha256, driver,
  revisions) and indexed by the release's `manifest.json`.
- **stable**: one release per major version per family, tagged
  `<family>-<version>` (`gcc-16.2.0`, `arm64-gcc-16.1.0`, `clang-23.1.0`),
  every major from gcc 9 / clang 10 on, host and cross (arm, arm64, riscv64,
  powerpc64, powerpc64le, loongarch64), repacked once and never repeated.

```sh
wget https://github.com/hazohelet/ccput/releases/download/gcc-16.2.0/gcc-16.2.0.tar.xz
tar -xf gcc-16.2.0.tar.xz
```
