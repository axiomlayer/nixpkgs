{ source, system }:

let
  pkgs = import source {
    inherit system;
    config.allowUnfree = false;
  };

  runtimePackages = {
    deno = pkgs.deno;
    fnm = pkgs.fnm;
    go = pkgs.go;
    node = pkgs.nodejs_24;
    npm = pkgs.nodejs-slim_24.npm;
    python = pkgs.python314;
    ripgrep = pkgs.ripgrep;
    sqlite = pkgs.sqlite;
  };

  packageInfo =
    _name: package:
    let
      drvPath = builtins.tryEval package.drvPath;
      version = builtins.tryEval (pkgs.lib.getVersion package);
    in
    assert drvPath.success;
    assert version.success;
    {
      drvPath = drvPath.value;
      version = version.value;
    };

  nixosToplevel =
    if pkgs.stdenv.hostPlatform.isLinux then
      let
        evaluated = import (source + "/nixos/lib/eval-config.nix") {
          inherit system;
          modules = [
            (
              { lib, ... }:
              {
                nixpkgs.hostPlatform = system;
                boot.loader.grub.enable = false;
                fileSystems."/".device = "nodev";
                fileSystems."/".fsType = "none";
                system.stateVersion = lib.trivial.release;
              }
            )
          ];
        };
      in
      evaluated.config.system.build.toplevel.drvPath
    else
      null;
in
assert pkgs.stdenv.hostPlatform.system == system;
{
  inherit system nixosToplevel;
  release = pkgs.lib.trivial.release;
  packages = builtins.mapAttrs packageInfo runtimePackages;
}
