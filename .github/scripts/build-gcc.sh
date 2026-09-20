#!/usr/bin/env bash
set -euo pipefail

: "${FAMILY:?FAMILY is required}"
: "${TARGET:?TARGET is required}"
: "${DRIVER:?DRIVER is required}"
# A cross build sets CROSS (the Debian arch of libc6-dev-<CROSS>-cross) and
# takes its target tools and sysroot from the Ubuntu archive; a native build
# leaves CROSS empty and uses the host as-is. ARCH_FLAGS adds configure
# flags.

workspace=$PWD
install="$workspace/gcc-install"
build="$workspace/gcc-build"

if [[ -n "$CROSS" ]]; then
  cross="/usr/$TARGET"

  if [[ ! -x "/usr/bin/$TARGET-as" ]]; then
    echo "::error::missing cross assembler: /usr/bin/$TARGET-as (binutils-$TARGET?)"
    exit 1
  fi
  if [[ ! -f "$cross/lib/crt1.o" ]]; then
    echo "::error::missing target startup files: $cross/lib/crt1.o (libc6-dev-$CROSS-cross?)"
    exit 1
  fi
  if [[ ! -d "$cross/include/bits" ]]; then
    echo "::error::missing target headers: $cross/include/bits"
    exit 1
  fi

  mkdir -p "$install/bin" "$install/$TARGET/bin"

  # The target tools link binutils' shared libraries, so carry those next to
  # them and find them through $ORIGIN: the toolchain then runs wherever the
  # tarball is unpacked.
  for tool in addr2line ar as c++filt elfedit ld ld.bfd nm objcopy objdump ranlib readelf size strings strip; do
    cp -L "/usr/bin/$TARGET-$tool" "$install/bin/$TARGET-$tool"
    cp -L "/usr/bin/$TARGET-$tool" "$install/$TARGET/bin/$tool"
  done
  for lib in \
    /usr/lib/x86_64-linux-gnu/libbfd-*.so \
    /usr/lib/x86_64-linux-gnu/libopcodes-*.so \
    /usr/lib/x86_64-linux-gnu/libctf*.so*
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

  # Upstream gcc looks for the target's usual lib dirs inside the sysroot;
  # the Ubuntu cross packages keep everything under /usr/$TARGET and express
  # the lib↔lib64 split as symlinks. Assemble one real directory and make
  # every name gcc or the dynamic loader may use point at it.
  sysroot="$install/$TARGET/sysroot"
  mkdir -p "$sysroot/usr/lib"
  cp -aL "$cross/include" "$sysroot/usr/"
  cp -aL "$cross/lib/." "$sysroot/usr/lib/"
  if [[ -d "$cross/lib64" ]]; then
    cp -aL "$cross/lib64/." "$sysroot/usr/lib/"
  fi
  ln -sfn usr/lib "$sysroot/lib"
  ln -sfn usr/lib "$sysroot/lib64"
  ln -sfn lib "$sysroot/usr/lib64"
  # The ld scripts embed /usr/$TARGET paths; point them into the sysroot.
  for script in $(grep -l "GNU ld script" "$sysroot/usr/lib"/*.so 2>/dev/null || true); do
    sed -i "s|/usr/$TARGET/lib64|=/usr/lib|g; s|/usr/$TARGET/lib|=/usr/lib|g" "$script"
  done
fi

mkdir -p "$install/share/licenses/gcc"
cp "$workspace/gcc/COPYING3" "$workspace/gcc/COPYING.RUNTIME" "$install/share/licenses/gcc/"
if [[ -n "$CROSS" ]]; then
  for pkg in "binutils-$TARGET" "libc6-dev-$CROSS-cross" "libc6-$CROSS-cross"; do
    if [[ -f "/usr/share/doc/$pkg/copyright" ]]; then
      mkdir -p "$install/share/licenses/$pkg"
      cp "/usr/share/doc/$pkg/copyright" "$install/share/licenses/$pkg/"
    fi
  done
fi

mkdir "$build"
cd "$build"

configure=(
  "$workspace/gcc/configure"
  "--prefix=$install"
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
if [[ -n "$CROSS" ]]; then
  configure+=("--target=$TARGET" "--with-sysroot=$install/$TARGET/sysroot")
fi
# shellcheck disable=SC2206
configure+=(${ARCH_FLAGS:-})

printf 'configure command:' > configure.log
printf ' %q' "${configure[@]}" >> configure.log
printf '\n' >> configure.log
"${configure[@]}" 2>&1 | tee -a configure.log

make -j"$(nproc)" all-gcc all-target-libgcc all-target-libatomic 2>&1 | tee build.log
grep -q '^#define ENABLE_ASSERT_CHECKING 1' gcc/auto-host.h
make install-gcc install-target-libgcc install-target-libatomic 2>&1 | tee install.log

# gcc's linux specs link -latomic_asneeded and -lgcc_s_asneeded on several
# targets (LoongArch64, AArch64, PowerPC64, ...); those are Debian/Loongson
# shim libraries that the Ubuntu cross sysroot does not carry. Install the
# same shims next to the target runtimes for every cross: as-needed wrappers
# over the real libraries installed just above, a no-op where unused.
if [[ -n "$CROSS" ]]; then
  libdir="$install/$TARGET/lib"
  cat > "$libdir/libatomic_asneeded.so" <<'EOF'
/* GNU ld script
   Add DT_NEEDED entry for -latomic only if needed.  */
INPUT ( AS_NEEDED ( -latomic ) )
EOF
  ln -sf libatomic.a "$libdir/libatomic_asneeded.a"
  cat > "$libdir/libgcc_s_asneeded.so" <<'EOF'
/* GNU ld script
   Add DT_NEEDED entry for libgcc_s.so only if needed.  */
INPUT ( AS_NEEDED ( -lgcc_s ) )
EOF
fi

printf 'int main(void){return 0;}\n' > "$workspace/probe.c"
if [[ -n "$CROSS" ]]; then
  "$install/bin/$TARGET-gcc" -v
  "$install/bin/$TARGET-gcc" -print-sysroot | grep -F "$install/$TARGET/sysroot"
  "$install/bin/$TARGET-gcc" -static "$workspace/probe.c" -o "$workspace/probe"
  file "$workspace/probe" | grep -F "ELF"
else
  "$install/bin/gcc" -v
  "$install/bin/gcc" "$workspace/probe.c" -o "$workspace/probe"
  "$workspace/probe"
fi
