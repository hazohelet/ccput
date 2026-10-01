#!/usr/bin/env bash
set -euo pipefail

: "${FAMILY:?FAMILY is required}"

# Build clang from an llvm-project checkout -- trunk or a release tag -- as an
# assertions compiler: Release with LLVM_ENABLE_ASSERTIONS=ON, every backend
# target, and for the nightly compiler-rt for the sanitizer runtimes.

workspace=$PWD
install="$workspace/llvm-install"
build="$workspace/llvm-build"
src="$workspace/llvm"

if [[ ! -d "$src/llvm" ]]; then
  echo "::error::missing llvm-project checkout at $src"
  exit 1
fi

# The version lives in llvm/CMakeLists.txt up to LLVM 18 and in
# cmake/Modules/LLVMVersion.cmake from LLVM 19 on.
major=""
for file in "$src/cmake/Modules/LLVMVersion.cmake" "$src/llvm/CMakeLists.txt"; do
  if [[ -f "$file" ]]; then
    major=$(sed -n 's/^ *set(LLVM_VERSION_MAJOR \([0-9]*\)).*/\1/p' "$file" | head -1)
    [[ -n "$major" ]] && break
  fi
done
if [[ ! "$major" =~ ^[0-9]+$ ]]; then
  echo "::error::cannot read LLVM_VERSION_MAJOR from the checkout"
  exit 1
fi
echo "building LLVM $major"

# A stable release builds the compiler alone. The sanitizer runtimes of older
# releases do not build or run against the current glibc and kernel, so only
# the nightly carries compiler-rt.
runtimes=()
targets=(install-clang install-clang-resource-headers)
if [[ "${KIND:?KIND is required}" == nightly ]]; then
  runtimes=(-DLLVM_ENABLE_RUNTIMES=compiler-rt)
  targets+=(install-runtimes)
fi

# The host compiler is gcc 14 for every release: older LLVM sources lean on
# transitive libstdc++ includes that gcc 13 and 14 no longer provide, which
# the forced includes below restore without touching the sources.
cxxflags=""
if (( major < 16 )); then
  cxxflags="-include cstdint -include limits -include string -include cstdio"
fi

# The runner image's own cmake may be 4.x, which refuses the
# cmake_minimum_required of older LLVM releases; Ubuntu's 3.28 accepts every
# release and is what the runtimes sub-build inherits through CMAKE_COMMAND.
cmake=/usr/bin/cmake
"$cmake" --version

# Every optional dependency the runner happens to have installed would be
# linked in and then missing wherever the archive is unpacked: older releases
# pick up the runner's libz3 on their own, so it is turned off explicitly.
"$cmake" -S "$src/llvm" -B "$build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$install" \
  -DCMAKE_C_COMPILER=gcc-14 \
  -DCMAKE_CXX_COMPILER=g++-14 \
  -DCMAKE_CXX_FLAGS="$cxxflags" \
  -DLLVM_ENABLE_ASSERTIONS=ON \
  -DLLVM_ENABLE_PROJECTS=clang \
  "${runtimes[@]}" \
  -DLLVM_INCLUDE_TESTS=OFF \
  -DLLVM_INCLUDE_BENCHMARKS=OFF \
  -DLLVM_INCLUDE_EXAMPLES=OFF \
  -DLLVM_INCLUDE_DOCS=OFF \
  -DLLVM_ENABLE_BINDINGS=OFF \
  -DLLVM_ENABLE_Z3_SOLVER=OFF \
  -DLLVM_PARALLEL_LINK_JOBS=2 \
  2>&1 | tee "$workspace/configure.log"

ninja -C "$build" "${targets[@]}" \
  2>&1 | tee "$workspace/build.log"

"$install/bin/clang" --version
# The assertions must really be on: a Release build without them would pass
# every check below. -debug-only exists only in a build with assertions.
debug=$("$install/bin/clang" -mllvm -debug-only=isel -c -x c /dev/null -o /dev/null 2>&1 || true)
if grep -q "Unknown command line argument" <<<"$debug"; then
  echo "::error::clang $major was built without assertions"
  exit 1
fi
# The probe includes a system header so the resource-dir builtins must be
# installed for it to compile: without install-clang-resource-headers this
# fails on stddef.h.
printf '#include <stdio.h>\nint main(void){printf("ok\\n");return 0;}\n' \
  > "$workspace/probe.c"
"$install/bin/clang" "$workspace/probe.c" -o "$workspace/probe"
"$workspace/probe"
if [[ "$KIND" == nightly ]]; then
  "$install/bin/clang" -fsanitize=undefined,address "$workspace/probe.c" \
    -o "$workspace/probe-san"
  "$workspace/probe-san"
fi
