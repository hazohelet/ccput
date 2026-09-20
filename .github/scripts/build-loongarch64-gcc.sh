#!/usr/bin/env bash
set -euo pipefail

: "${TARGET:?TARGET is required}"
: "${BOOTSTRAP_VERSION:?BOOTSTRAP_VERSION is required}"

workspace=$PWD
bootstrap="$workspace/bootstrap/loongarch64-gcc-${BOOTSTRAP_VERSION}/loongarch64-gcc/$TARGET"
install="$workspace/gcc-install"
build="$workspace/gcc-build"

test -x "$bootstrap/bin/$TARGET-as"
test -d "$bootstrap/$TARGET/sysroot/usr/include"

mkdir -p "$install/bin" "$install/$TARGET" "$install/share"
cp -a "$bootstrap/$TARGET/bin" "$install/$TARGET/"
cp -a "$bootstrap/$TARGET/sysroot" "$install/$TARGET/"
cp -a "$bootstrap/share/licenses" "$install/share/"

for tool in addr2line ar as c++filt elfedit ld ld.bfd nm objcopy objdump ranlib readelf size strings strip; do
  cp -a "$bootstrap/bin/$TARGET-$tool" "$install/bin/"
done

export PATH="$bootstrap/bin:$PATH"
mkdir "$build"
cd "$build"

configure=(
  "$workspace/gcc/configure"
  "--target=$TARGET"
  "--prefix=$install"
  "--with-sysroot=$install/$TARGET/sysroot"
  --with-arch=loongarch64
  --enable-languages=c,lto
  --enable-checking=yes
  --disable-multilib
  --disable-bootstrap
  --disable-nls
  --disable-libsanitizer
  --disable-libssp
  --disable-libgomp
  --disable-libquadmath
)
printf 'configure command:' > configure.log
printf ' %q' "${configure[@]}" >> configure.log
printf '\n' >> configure.log
"${configure[@]}" 2>&1 | tee -a configure.log

grep -q '^#define ENABLE_ASSERT_CHECKING 1' gcc/auto-host.h
make -j"$(nproc)" all-gcc all-target-libgcc 2>&1 | tee build.log
make install-gcc install-target-libgcc 2>&1 | tee install.log

"$install/bin/$TARGET-gcc" -v
"$install/bin/$TARGET-gcc" -print-sysroot | grep -F "$install/$TARGET/sysroot"
