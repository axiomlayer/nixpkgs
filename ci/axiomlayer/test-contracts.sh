#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: test-contracts.sh HARNESS SNAPSHOT DOTFILES" >&2
  exit 2
fi

harness=$(cd "$1" && pwd)
snapshot=$(cd "$2" && pwd)
control=$(cd "$3" && pwd)
verifier="$harness/ci/axiomlayer/verify_integration.py"

python3 "$verifier" --root "$harness" --source "$snapshot" --control "$control"

temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT

mkdir -p "$temporary/workflow"
cp -R "$harness/.github" "$temporary/workflow/.github"
printf '\npermissions:\n  contents: write\n' >> "$temporary/workflow/.github/workflows/axiomlayer-integration.yml"
if python3 "$verifier" --mode workflows --root "$temporary/workflow" >/dev/null 2>&1; then
  echo "workflow verifier accepted write authority" >&2
  exit 1
fi

mkdir -p "$temporary/pin"
cp -R "$harness/.github" "$temporary/pin/.github"
python3 - "$temporary/pin/.github/axiomlayer/fleet-pin.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
value["fleetPin"]["commit"] = "0" * 40
path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
PY
if python3 "$verifier" --mode policy --root "$temporary/pin" --source "$snapshot" --control "$control" >/dev/null 2>&1; then
  echo "policy verifier accepted a changed fleet pin" >&2
  exit 1
fi

mkdir -p "$temporary/archive"
cp -R "$harness/.github" "$temporary/archive/.github"
printf '\n# changed\n' >> "$temporary/archive/.github/upstream-workflows/bot.yml"
if python3 "$verifier" --mode workflows --root "$temporary/archive" >/dev/null 2>&1; then
  echo "workflow verifier accepted a changed upstream archive" >&2
  exit 1
fi

echo "tamper_refusal=verified"
