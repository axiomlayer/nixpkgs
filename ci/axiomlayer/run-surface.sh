#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: run-surface.sh HARNESS SNAPSHOT COMMIT SYSTEM NIX_VERSION" >&2
  exit 2
fi

harness=$(cd "$1" && pwd)
snapshot=$(cd "$2" && pwd)
expected_commit=$3
system=$4
expected_nix_version=$5

actual_commit=$(git -C "$snapshot" rev-parse HEAD)
if [[ "$actual_commit" != "$expected_commit" ]]; then
  echo "snapshot commit mismatch: expected $expected_commit, got $actual_commit" >&2
  exit 1
fi

actual_nix_version=$(nix --version)
if [[ "$actual_nix_version" != "nix (Nix) $expected_nix_version" ]]; then
  echo "Nix version mismatch: expected $expected_nix_version, got $actual_nix_version" >&2
  exit 1
fi

native_system=$(nix eval --impure --raw --expr builtins.currentSystem)
if [[ "$native_system" != "$system" ]]; then
  echo "runner system mismatch: expected $system, got $native_system" >&2
  exit 1
fi

temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT

nix eval \
  --impure \
  --json \
  --file "$harness/ci/axiomlayer/evaluate-surface.nix" \
  --arg source "$snapshot" \
  --argstr system "$system" \
  > "$temporary/evaluation.json"

python3 - "$temporary/evaluation.json" "$system" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
system = sys.argv[2]
value = json.loads(path.read_text(encoding="utf-8"))
expected_packages = {"deno", "fnm", "go", "node", "npm", "python", "ripgrep", "sqlite"}

if value.get("system") != system:
    raise SystemExit(f"evaluated system mismatch: {value.get('system')!r}")
if value.get("release") != "26.05":
    raise SystemExit(f"evaluated release mismatch: {value.get('release')!r}")
if set(value.get("packages", {})) != expected_packages:
    raise SystemExit("evaluated package set mismatch")
for name, package in value["packages"].items():
    if not package.get("drvPath", "").endswith(".drv"):
        raise SystemExit(f"{name} did not evaluate to a derivation")
    if not package.get("version"):
        raise SystemExit(f"{name} has no evaluated version")
if system.endswith("-linux") and not value.get("nixosToplevel", "").endswith(".drv"):
    raise SystemExit("Linux surface did not evaluate a NixOS toplevel")
if system.endswith("-darwin") and value.get("nixosToplevel") is not None:
    raise SystemExit("Darwin surface unexpectedly evaluated a NixOS toplevel")

print(f"evaluated_system={system}")
print("evaluated_packages=" + ",".join(sorted(expected_packages)))
print("nixos_evaluation=" + ("verified" if system.endswith("-linux") else "not-applicable"))
PY

result=$(nix build \
  --impure \
  --file "$harness/ci/axiomlayer/fleet-smoke.nix" \
  --arg source "$snapshot" \
  --argstr system "$system" \
  --no-link \
  --print-out-paths \
  -L)

test -f "$result/versions.txt"
grep -Fx 'fts5=verified' "$result/versions.txt"
sed -n '1,4p' "$result/versions.txt"
git -C "$snapshot" diff --exit-code

echo "snapshot_commit=$actual_commit"
echo "native_surface=$system"
echo "smoke_build=$result"
