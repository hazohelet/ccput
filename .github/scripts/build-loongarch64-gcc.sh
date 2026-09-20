#!/usr/bin/env bash
set -euo pipefail

: "${TARGET:?TARGET is required}"

workspace=$PWD
cross="/usr/$TARGET"
install="$workspace/gcc-install"
build="$workspace/gcc-build"

if [[ ! -x "/usr/bin/$TARGET-as" ]]; then
  echo "::error::missing cross assembler: /usr/bin/$TARGET-as (binutils-$TARGET?)"
  exit 1
fi
if [[ ! -f "$cross/lib/crt1.o" ]]; then
  echo "::error::missing target startup files: $cross/lib/crt1.o (libc6-dev-*-cross?)"
  exit 1
fi
if [[ ! -d "$cross/include/bits" ]]; then
  echo "::error::missing target headers: $cross/include/bits"
  exit 1
fi

mkdir -p "$install/bin" "$install/$TARGET/bin" "$install/share/licenses"

# The target tools come from the Ubuntu archive. They link binutils' shared
# libraries, so carry those next to them and find them through $ORIGIN: the
# toolchain then runs wherever the tarball is unpacked.
for tool in addr2line ar as c++filt elfedit ld ld.bfd nm objcopy objdump ranlib readelf size strings strip; do
  cp -L "/usr/bin/$TARGET-$tool" "$install/bin/$TARGET-$tool"
  cp -L "/usr/bin/$TARGET-$tool" "$install/$TARGET/bin/$tool"
done
for lib in \
  /usr/lib/x86_64-linux-gnu/libbfd-*-loong64.so \
  /usr/lib/x86_64-linux-gnu/libopcodes-*-loong64.so \
  /usr/lib/x86_64-linux-gnu/libctf-loong64.so \
  /usr/lib/x86_64-linux-gnu/libctf-loong64.so.0*
do
  cp -L "$lib" "$install/bin/"
  cp -L "$lib" "$install/$TARGET/bin/"
done
for binary in "$install/bin/$TARGET-"* "$install/$TARGET/bin/"*; do
  patchelf --set-rpath '$ORIGIN' "$binary"
done

# Fail fast rather than partway through all-target-libgcc.
for tool in ar ranlib; do
  if ! "$install/$TARGET/bin/$tool" --version >/dev/null 2>&1; then
    echo "::error::cross $tool does not run"
    exit 1
  fi
done

# Upstream gcc looks for a lib64-style lp64d sysroot; the Ubuntu cross
# packages keep everything under /usr/$TARGET and express the lib↔lib64
# split as symlinks. Reassemble the layout gcc expects as real files
# (dereferenced, so no symlink dangles once the pieces move), with the
# runtime objects also visible at /lib64 for the loader.
sysroot="$install/$TARGET/sysroot"
mkdir -p "$sysroot/usr/lib64" "$sysroot/lib64"
cp -aL "$cross/include" "$sysroot/usr/"
cp -aL "$cross/lib/." "$sysroot/usr/lib64/"
cp -aL "$cross/lib64/." "$sysroot/usr/lib64/"
for path in "$sysroot/usr/lib64"/*; do
  name=${path##*/}
  case "$name" in
    *.so.[0-9]* | ld-linux*) cp -a "$path" "$sysroot/lib64/$name" ;;
  esac
done
# The ld scripts embed /usr/$TARGET paths; point them into the sysroot.
for script in $(grep -l "GNU ld script" "$sysroot/usr/lib64"/*.so 2>/dev/null || true); do
  sed -i "s|/usr/$TARGET/lib64|=/lib64|g; s|/usr/$TARGET/lib|=/usr/lib64|g" "$script"
done

for pkg in binutils-loongarch64-linux-gnu libc6-dev-loong64-cross libc6-loong64-cross; do
  if [[ -f "/usr/share/doc/$pkg/copyright" ]]; then
    mkdir -p "$install/share/licenses/$pkg"
    cp "/usr/share/doc/$pkg/copyright" "$install/share/licenses/$pkg/"
  fi
done
mkdir -p "$install/share/licenses/gcc"
cp "$workspace/gcc/COPYING3" "$workspace/gcc/COPYING.RUNTIME" "$install/share/licenses/gcc/"

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
  --disable-werror
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

make -j"$(nproc)" all-gcc all-target-libgcc 2>&1 | tee build.log
grep -q '^#define ENABLE_ASSERT_CHECKING 1' gcc/auto-host.h
make install-gcc install-target-libgcc 2>&1 | tee install.log

"$install/bin/$TARGET-gcc" -v
"$install/bin/$TARGET-gcc" -print-sysroot | grep -F "$install/$TARGET/sysroot"
