{
  description = "CI environments for building and checking ccput toolchains";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];

      forAllSystems =
        f: nixpkgs.lib.genAttrs systems (system: f (import nixpkgs { inherit system; }));

      qemuTargets = [
        "aarch64"
        "arm"
        "i386"
        "loongarch64"
        "mips"
        "mips64"
        "mips64el"
        "mipsel"
        "ppc"
        "ppc64"
        "ppc64le"
        "riscv64"
        "s390x"
        "sparc64"
      ];

      mkQemuUser = pkgs:
        pkgs.stdenv.mkDerivation rec {
          pname = "qemu-user";
          version = "11.1.1";

          src = pkgs.fetchurl {
            url = "https://download.qemu.org/qemu-${version}.tar.xz";
            hash = "sha256-B5/7/4pxEbvIkCIQfLq/O7/WFNX8nXzGdZkRlqyhJII=";
          };

          strictDeps = true;

          nativeBuildInputs = with pkgs; [
            pkg-config
            meson
            ninja
            perl
            flex
            bison
            python3Packages.python
            python3Packages.distlib
            python3Packages.setuptools
            python3Packages.wheel
          ];

          buildInputs = with pkgs; [
            glib
            pixman
            zlib
          ];

          dontUseMesonConfigure = true;
          dontUseNinjaBuild = true;
          dontUseNinjaCheck = true;
          dontUseNinjaInstall = true;

          enableParallelBuilding = true;

          postPatch = ''
            patchShebangs .
          '';

          configurePhase = ''
            runHook preConfigure

            mkdir build
            cd build

            ../configure \
              --prefix="$out" \
              --target-list=${
                pkgs.lib.concatMapStringsSep "," (t: "${t}-linux-user") qemuTargets
              } \
              --enable-linux-user \
              --disable-bsd-user \
              --disable-system \
              --enable-tcg \
              --disable-docs \
              --disable-download \
              --without-default-features

            cd ..

            runHook postConfigure
          '';

          emulators = map (t: "qemu-${t}") qemuTargets;

          buildPhase = ''
            runHook preBuild

            ninja -C build $emulators

            runHook postBuild
          '';

          installPhase = ''
            runHook preInstall

            mkdir -p "$out/bin"

            for bin in $emulators; do
              install -Dm755 "build/$bin" "$out/bin/$bin"
            done

            for bin in "$out"/bin/qemu-*; do
              "$bin" --version >/dev/null
            done

            runHook postInstall
          '';

          meta = with pkgs.lib; {
            description =
              "Minimal QEMU ${version} linux-user emulators "
              + "(${toString (builtins.length qemuTargets)} targets)";
            homepage = "https://www.qemu.org/";
            license = licenses.gpl2Only;
            platforms = platforms.linux;
          };
        };

      # Kitware's own prebuilt cmake, unpacked as is: nixpkgs' cmake is patched
      # to ignore /usr, where the runner's zlib and headers live. Pinned here
      # rather than following nixpkgs so a cmake bump is its own commit.
      mkCmake = pkgs:
        let
          version = "4.5.1";
          arch = {
            x86_64-linux = {
              name = "x86_64";
              hash = "sha256-g7cRgftitP6PJgZ6kyFEhXLnpDvQT/bH9UokwzaY8W0=";
            };
            aarch64-linux = {
              name = "aarch64";
              hash = "sha256-O+wgjxHtlsFzAy7q+Xk3xU3SRqzv7iKs13lKa05w1TY=";
            };
          }.${pkgs.stdenv.hostPlatform.system};
        in
        pkgs.stdenvNoCC.mkDerivation {
          pname = "cmake-kitware";
          inherit version;

          src = pkgs.fetchurl {
            url = "https://github.com/Kitware/CMake/releases/download/v${version}/cmake-${version}-linux-${arch.name}.tar.gz";
            inherit (arch) hash;
          };

          # It runs on the runner's own Ubuntu, against its glibc and /usr, so
          # nothing in it is rewritten for the store.
          dontConfigure = true;
          dontBuild = true;
          dontFixup = true;

          installPhase = ''
            runHook preInstall
            mkdir -p "$out"
            cp -a bin share "$out"/
            runHook postInstall
          '';

          meta = with pkgs.lib; {
            description = "Kitware's prebuilt CMake ${version}";
            homepage = "https://cmake.org/";
            license = licenses.bsd3;
            platforms = [ "x86_64-linux" "aarch64-linux" ];
          };
        };

    in {
      packages = forAllSystems (pkgs: rec {
        qemu-user = mkQemuUser pkgs;
        cmake = mkCmake pkgs;
        default = qemu-user;
      });
    };
}
