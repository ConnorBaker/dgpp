{
  description = "DGPP: C++/CUDA inference engine for NVIDIA DGX Spark (GB10)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/master";

  outputs =
    { self, nixpkgs }:
    let
      # DGX Spark is aarch64 Linux with a GB10 (sm_121a). CMakeLists.txt pins
      # CMAKE_CUDA_ARCHITECTURES to 121a; the capability here must agree.
      system = "aarch64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config = {
          allowUnfree = true;
          cudaSupport = true;
          cudaCapabilities = [ "12.1a" ];
        };
        overlays = [
          (final: prev: {
            pythonPackagesExtensions = prev.pythonPackagesExtensions ++ [
              (pyFinal: pyPrev: {
                # safetensors' test suite needs PyTorch (nativeCheckInputs);
                # nothing else in the dev shell does. Under cudaSupport that is
                # a CUDA PyTorch, absent from the binary cache, so building the
                # shell would compile PyTorch, MAGMA and Open MPI from source
                # just to test safetensors. Skip the tests and drop their inputs.
                safetensors = pyPrev.safetensors.overridePythonAttrs {
                  doCheck = false;
                  nativeCheckInputs = [ ];
                };
              })
            ];
          })
        ];
      };
      inherit (pkgs) lib cudaPackages;

      dgpp = cudaPackages.backendStdenv.mkDerivation {
        pname = "dgpp";
        # No git revision: it would change the derivation on every commit,
        # rebuilding the server even when no build input changed. The store
        # path already identifies the exact sources.
        version = "0.1.0";
        # Only what CMakeLists.txt builds from or installs, so edits to the
        # flake, top-level docs or benchmark records do not rebuild the server.
        src = lib.fileset.toSource {
          root = ./.;
          fileset = lib.fileset.unions [
            ./.env.example
            ./CMakeLists.txt
            ./LICENSE
            ./apps
            (lib.fileset.difference ./benchmarks ./benchmarks/results)
            ./cmake
            ./deploy
            ./docs
            ./requirements-download.txt
            ./scripts
            ./src
            ./tests
            ./third_party
            ./tools
          ];
        };

        strictDeps = true;
        __structuredAttrs = true;

        # cmake/version.cmake stamps "<base>+g<sha12>" from git; the sandbox has
        # no git, and the flake deliberately omits the revision (see version).
        postPatch = ''
          substituteInPlace cmake/version.cmake \
            --replace-fail 'find_package(Git QUIET)' 'set(GIT_FOUND FALSE)' \
            --replace-fail 'set(_version "''${BASE_VERSION}+unknown")' 'set(_version "''${BASE_VERSION}")'
        '';

        nativeBuildInputs = [
          pkgs.cmake
          pkgs.ninja
          pkgs.python3
          cudaPackages.cuda_nvcc
          pkgs.autoAddDriverRunpath
        ];

        buildInputs = [
          cudaPackages.cuda_cudart
          cudaPackages.cccl
          cudaPackages.libcublas
          pkgs.rdma-core
          pkgs.pcre2
        ];

        # Mirrors the "release" preset in CMakePresets.json.
        cmakeFlags = [
          (lib.cmakeFeature "CMAKE_BUILD_TYPE" "Release")
          (lib.cmakeFeature "CMAKE_CXX_FLAGS_RELEASE" "-O3 -DNDEBUG -g0")
          (lib.cmakeFeature "CMAKE_CUDA_FLAGS_RELEASE" "-O3 -DNDEBUG -Xcompiler=-g0")
          (lib.cmakeBool "DGPP_BUILD_BENCHMARKS" false)
          (lib.cmakeBool "DGPP_WERROR" false)
          (lib.cmakeFeature "DGPP_SANITIZE" "")
          # `install` would otherwise depend on `all`, which includes the tests.
          (lib.cmakeBool "CMAKE_SKIP_INSTALL_ALL_DEPENDENCY" true)
        ];

        # Only the server, as the "release" build preset does.
        ninjaFlags = [ "dgpp_serve_app" ];

        meta = {
          description = "C++/CUDA inference engine for NVIDIA DGX Spark (GB10)";
          license = lib.licenses.asl20;
          platforms = [ system ];
          mainProgram = "dgpp-serve";
        };
      };
    in
    {
      packages.${system} = {
        inherit dgpp;
        default = dgpp;
      };

      devShells.${system}.default = pkgs.mkShell.override { stdenv = cudaPackages.backendStdenv; } {
        inputsFrom = [ dgpp ];
        # What scripts/setup.sh, scripts/dgpp-cluster and its preflight
        # (scripts/cluster_doctor.py) run, so they use Nixpkgs rather than
        # the host's packages. The host NVIDIA driver comes from nixglhost,
        # passed to `dgpp-cluster up --head-wrap nixglhost` on non-NixOS.
        # dgpp itself is deliberately absent: entering the shell must not
        # build the server. Pass `--bin "$(nix build --print-out-paths
        # .#dgpp)/bin/dgpp-serve"` to `dgpp-cluster up` instead.
        packages = [
          # requirements-download.txt and requirements-tools.txt
          (pkgs.python3.withPackages (ps: [
            ps.huggingface-hub
            ps.jinja2
            ps.safetensors
            ps.tokenizers
          ]))
          # Codex through the LiteLLM Responses bridge (docs/codex.md). Neither
          # depends on CUDA, so cudaSupport leaves them as cached.
          pkgs.codex
          pkgs.litellm
          pkgs.coreutils
          pkgs.curl
          pkgs.glibc.bin # ldd
          pkgs.iproute2
          pkgs.jq
          pkgs.openssh
          pkgs.poppler-utils # pdftotext, for PDF inputs
          pkgs.rsync
        ];
      };

      formatter.${system} = pkgs.nixfmt;
    };
}
