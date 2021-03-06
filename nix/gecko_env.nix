{ releng_pkgs }:

let
  inherit (releng_pkgs.pkgs) rustChannelOf bash autoconf213 clang_4 llvm_4 llvmPackages_4 gcc-unwrapped glibc;
  inherit (releng_pkgs.pkgs.devEnv) gecko;

  # Rust 1.28.1-beta6
  rustChannel = rustChannelOf { date = "2018-06-30"; channel = "beta"; };

  # Add missing gcc libraries needed by clang (see https://github.com/mozilla/release-services/issues/1256)
  gcc_libs = builtins.concatStringsSep ":" [
    "${gcc-unwrapped}/include/c++/${gcc-unwrapped.version}"
    "${gcc-unwrapped}/include/c++/${gcc-unwrapped.version}/backward"
    "${gcc-unwrapped}/include/c++/${gcc-unwrapped.version}/x86_64-unknown-linux-gnu"
    "${glibc.dev}/include"
  ];

in gecko.overrideDerivation (old: {
  # Dummy src, cannot be null
  src = ./.;
  configurePhase = ''
    mkdir -p $out/bin
    mkdir -p $out/conf
  '';
  buildPhase = ''

    # Gecko build environment
    geckoenv=$out/bin/gecko-env

    echo "#!${bash}/bin/sh" > $geckoenv
    echo "export SHELL=xterm" >> $geckoenv
    env | grep -E '^(PATH|PKG_CONFIG_PATH|CMAKE_INCLUDE_PATH)='| sed 's/^/export /' >> $geckoenv
    echo "export CPLUS_INCLUDE_PATH=$CMAKE_INCLUDE_PATH:${gcc_libs}:\$EXTRAS_INCLUDE_PATH" >> $geckoenv
    echo "export C_INCLUDE_PATH=$CMAKE_INCLUDE_PATH:${gcc_libs}:\$EXTRAS_INCLUDE_PATH" >> $geckoenv
    echo "export INCLUDE_PATH=$CMAKE_INCLUDE_PATH:${gcc_libs}:\$EXTRAS_INCLUDE_PATH" >> $geckoenv

    # Add self in PATH, needed to exec
    echo "export PATH=$out/bin:\$PATH" >> $geckoenv

    # Clean python environment
    echo "export PYTHONPATH=" >> $geckoenv

    # Build LDFLAGS and LIBRARY_PATH
    echo "export LDFLAGS=\"$NIX_LDFLAGS\"" >> $geckoenv
    echo "export LIBRARY_PATH=\"$CMAKE_LIBRARY_PATH\"" >> $geckoenv
    echo "export LD_LIBRARY_PATH=\"$CMAKE_LIBRARY_PATH\"" >> $geckoenv

    # Setup Clang & Autoconf
    echo "export CC=${clang_4}/bin/clang" >> $geckoenv
    echo "export CXX=${clang_4}/bin/clang++" >> $geckoenv
    echo "export LD=${clang_4}/bin/ld" >> $geckoenv
    echo "export LLVM_CONFIG=${llvm_4}/bin/llvm-config" >> $geckoenv
    echo "export LLVMCONFIG=${llvm_4}/bin/llvm-config" >> $geckoenv # we need both
    echo "export AUTOCONF=${autoconf213}/bin/autoconf" >> $geckoenv

    # Build custom mozconfig
    mozconfig=$out/conf/mozconfig
    echo > $mozconfig "
    ac_add_options --enable-clang-plugin
    ac_add_options --with-clang-path=${clang_4}/bin/clang
    ac_add_options --with-libclang-path=${llvmPackages_4.libclang}/lib
    mk_add_options AUTOCLOBBER=1
    "
    echo "export CLANG_MOZCONFIG=$mozconfig" >> $geckoenv

    # Use updated rust version
    echo "export PATH=${rustChannel.rust}/bin:${rustChannel.cargo}/bin:\$PATH" >> $geckoenv
  '';
  installPhase = ''
    geckoenv=$out/bin/gecko-env

    # Exec command line from arguments
    echo "set -x" >> $geckoenv
    echo "exec \$@" >> $geckoenv

    chmod +x $geckoenv
  '';
  propagatedBuildInputs = old.propagatedBuildInputs
    ++ [
      # Update rust to latest stable
      rustChannel.rust
      rustChannel.cargo
    ];
})
