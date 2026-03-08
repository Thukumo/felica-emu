{
  description = "FeliCa emulator development environment";

  inputs = {
    nixpkgs.url = "flake:nixpkgs";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [
          (final: prev: {
            python3 = prev.python3.override {
              packageOverrides = pyFinal: pyPrev: {
                ndeflib = pyPrev.ndeflib.overridePythonAttrs (_: {
                  doCheck = false;
                });
                nfcpy = pyPrev.nfcpy.overridePythonAttrs (_: {
                  doCheck = false;
                });
              };
            };
          })
        ];
      };
    in
    {
      devShells.${system}.default =
        let
          pythonEnv = pkgs.python3.withPackages (ps: [ 
            ps.nfcpy ps.tqdm ps.pyusb ps.pick ps.rich ps.pytest 
          ]);
          
          # シンプルなラッパー作成
          mkNfcScript = name: module: needsSudo: pkgs.writeShellScriptBin name ''
            export PYTHONPATH="$PWD"
            ${if needsSudo then "sudo modprobe -r port100 pn533_usb pn533 2>/dev/null || true" else ""}
            exec ${if needsSudo then "sudo -E " else ""}${pythonEnv}/bin/python -m nfc_emu.${module} "$@"
          '';

          nfcScripts = [
            (mkNfcScript "nfc-dump" "dump_card" true)
            (mkNfcScript "nfc-emu" "emulate_card" true)
            (mkNfcScript "nfc-inspect" "inspect_dump" false)
            (mkNfcScript "nfc-probe" "probe_card" true)
          ];
        in
        pkgs.mkShell {
          packages = [ pythonEnv ] ++ nfcScripts;
          shellHook = ''
            echo "NFC Emu development environment loaded."
            echo "Available commands: nfc-dump, nfc-emu, nfc-inspect, nfc-probe"
          '';
        };
    };
}
