#!/usr/bin/env bash
set -euo pipefail

host_path=/usr/bin:/bin:/usr/sbin:/sbin
PATH=$host_path
export PATH

if [[ $# -ne 5 ]]; then
  echo "usage: run-surface.sh HARNESS SNAPSHOT COMMIT SYSTEM NIX_VERSION" >&2
  exit 2
fi

harness=$(cd "$1" && pwd)
snapshot=$(cd "$2" && pwd)
expected_commit=$3
system=$4
expected_nix_version=$5

case "$system" in
  x86_64-linux | aarch64-linux | x86_64-darwin | aarch64-darwin) ;;
  *)
    echo "unsupported Nix system: $system" >&2
    exit 1
    ;;
esac

actual_commit=$(git -C "$snapshot" rev-parse HEAD)
if [[ "$actual_commit" != "$expected_commit" ]]; then
  echo "snapshot commit mismatch: expected $expected_commit, got $actual_commit" >&2
  exit 1
fi

case "$(uname -s).$(uname -m)" in
  Linux.x86_64) native_system=x86_64-linux ;;
  Linux.aarch64 | Linux.arm64) native_system=aarch64-linux ;;
  Darwin.x86_64) native_system=x86_64-darwin ;;
  Darwin.arm64 | Darwin.aarch64) native_system=aarch64-darwin ;;
  *)
    echo "unsupported native host: $(uname -s).$(uname -m)" >&2
    exit 1
    ;;
esac
if [[ "$native_system" != "$system" ]]; then
  echo "runner system mismatch: expected $system, got $native_system" >&2
  exit 1
fi

temporary=$(mktemp -d /tmp/axiom-nix-surface.XXXXXXXX)
trap 'rm -rf "$temporary"' EXIT
mkdir -p "$temporary/home" "$temporary/source"

nix_bin=/nix/var/nix/profiles/default/bin/nix
if [[ ! -x "$nix_bin" ]]; then
  echo "Nix binary is missing: $nix_bin" >&2
  exit 1
fi
ci_user=$(id -un)
safe_path=/nix/var/nix/profiles/default/bin:$host_path
nix_config=$(printf '%s\n' \
  'experimental-features = nix-command flakes' \
  'flake-registry =' \
  'accept-flake-config = false' \
  'warn-dirty = false' \
  'pure-eval = true' \
  'restrict-eval = true')

# Nixpkgs is executable input. Even on an ephemeral hosted runner, candidate
# evaluation must not inherit GitHub's runtime tokens, event paths, proxy
# credentials, credential helpers, or repository-controlled environment.
clean_nix() {
  /usr/bin/env -i \
    HOME="$temporary/home" \
    USER="$ci_user" \
    LOGNAME="$ci_user" \
    PATH="$safe_path" \
    TMPDIR="$temporary" \
    LANG=C \
    LC_ALL=C \
    CI=true \
    NIX_CONFIG="$nix_config" \
    "$@"
}

actual_nix_version=$(clean_nix "$nix_bin" --version)
if [[ "$actual_nix_version" != "nix (Nix) $expected_nix_version" ]]; then
  echo "Nix version mismatch: expected $expected_nix_version, got $actual_nix_version" >&2
  exit 1
fi

# Export the exact commit, not the checkout worktree, then content-address both
# executable inputs in the Nix store. This makes pure evaluation possible and
# prevents candidate expressions from reading arbitrary mutable host paths.
git -C "$snapshot" archive --format=tar "$expected_commit" |
  tar -xf - -C "$temporary/source"
source_store=$(clean_nix "$nix_bin" store add-path \
  --name axiomlayer-nixpkgs-source "$temporary/source")
harness_store=$(clean_nix "$nix_bin" store add-path \
  --name axiomlayer-nixpkgs-harness "$harness/ci/axiomlayer")
store_path_pattern='^/nix/store/[0-9a-df-np-sv-z]{32}-axiomlayer-nixpkgs-(source|harness)$'
if [[ ! "$source_store" =~ $store_path_pattern ]]; then
  echo "unexpected source store path: $source_store" >&2
  exit 1
fi
if [[ ! "$harness_store" =~ $store_path_pattern ]]; then
  echo "unexpected harness store path: $harness_store" >&2
  exit 1
fi

evaluation_expression="(import $harness_store/evaluate-surface.nix) { source = $source_store; system = \"$system\"; }"
clean_nix "$nix_bin" eval \
  --option pure-eval true \
  --option restrict-eval true \
  --json \
  --expr "$evaluation_expression" \
  >"$temporary/evaluation.json"

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

build_expression="(import $harness_store/fleet-smoke.nix) { source = $source_store; system = \"$system\"; }"
result=$(clean_nix "$nix_bin" build \
  --option pure-eval true \
  --option restrict-eval true \
  --expr "$build_expression" \
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
