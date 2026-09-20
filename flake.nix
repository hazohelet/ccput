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

    in {
      packages = forAllSystems (pkgs: rec {
        qemu-user = mkQemuUser pkgs;
        default = qemu-user;
      });

      devShells = forAllSystems (pkgs: {
        gcc-loongarch64 = pkgs.mkShell {
          packages = with pkgs; [
            bash
            binutils
            bison
            cacert
            curl
            flex
            gawk
            git
            gnumake
            gmp
            isl
            libmpc
            mpfr
            python3
            texinfo
            wget
            xz
            zlib
            zstd
          ];
        };
      });
    };
}
