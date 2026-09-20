#!/usr/bin/env bash
set -euo pipefail

: "${FAMILY:?FAMILY is required}"

# Build clang from llvm-project trunk the way Compiler Explorer builds its
# assertions compilers: Release with LLVM_ENABLE_ASSERTIONS=ON, compiler-rt
# for the sanitizer runtimes, every backend target.

workspace=$PWD
install="$workspace/llvm-install"
build="$workspace/llvm-build"
src="$workspace/llvm"

if [[ ! -d "$src/llvm" ]]; then
  echo "::error::missing llvm-project checkout at $src"
  exit 1
fi

cmake -S "$src/llvm" -B "$build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$install" \
  -DLLVM_ENABLE_ASSERTIONS=ON \
  -DLLVM_ENABLE_PROJECTS=clang \
  -DLLVM_ENABLE_RUNTIMES=compiler-rt \
  -DLLVM_INCLUDE_TESTS=OFF \
  -DLLVM_INCLUDE_BENCHMARKS=OFF \
  -DLLVM_INCLUDE_EXAMPLES=OFF \
  -DLLVM_PARALLEL_LINK_JOBS=2 \
  2>&1 | tee "$workspace/configure.log"

ninja -C "$build" install-clang install-clang-resource-headers install-runtimes \
  2>&1 | tee "$workspace/build.log"

"$install/bin/clang" --version
# The probe includes a system header so the resource-dir builtins must be
# installed for it to compile: without install-clang-resource-headers this
# fails on stddef.h.
printf '#include <stdio.h>\nint main(void){printf("ok\\n");return 0;}\n' \
  > "$workspace/probe.c"
"$install/bin/clang" "$workspace/probe.c" -o "$workspace/probe"
"$workspace/probe"
"$install/bin/clang" -fsanitize=undefined,address "$workspace/probe.c" \
  -o "$workspace/probe-san"
"$workspace/probe-san"
