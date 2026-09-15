#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
USES = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)


def fail(message: str) -> None:
    raise SystemExit(message)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    encoded = (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def git_blob(repo: Path, revision_path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", revision_path],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def expect(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label} mismatch: expected {expected!r}, got {actual!r}")


def one(items: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    matches = [item for item in items if item.get(key) == value]
    if len(matches) != 1:
        fail(f"expected exactly one {key}={value!r}, found {len(matches)}")
    return matches[0]


def verify_workflows(root: Path, manifest: dict[str, Any]) -> None:
    workflow_dir = root / ".github/workflows"
    active = sorted(
        path.relative_to(root).as_posix()
        for path in workflow_dir.iterdir()
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    )
    expected_active = sorted(manifest["activeWorkflows"])
    expect(active, expected_active, "active workflow set")

    archived = manifest["archivedUpstreamWorkflows"]
    archived_dir = root / ".github/upstream-workflows"
    actual_archived = sorted(
        path.relative_to(root).as_posix()
        for path in archived_dir.iterdir()
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    )
    expect(actual_archived, sorted(archived), "archived workflow set")

    for relative, expected_digest in archived.items():
        path = root / relative
        expect(sha256(path), expected_digest, f"archived workflow {relative}")
        for reference in USES.findall(path.read_text(encoding="utf-8")):
            if reference.startswith("./"):
                continue
            if "@" not in reference:
                fail(f"archived external action has no ref: {reference}")
            _name, commit = reference.rsplit("@", 1)
            if not FULL_SHA.fullmatch(commit):
                fail(f"archived external action is not full-SHA pinned: {reference}")

    for relative, expected_digest in manifest["archivedUpstreamDocuments"].items():
        path = root / relative
        expect(sha256(path), expected_digest, f"archived upstream document {relative}")

    declared_actions = {
        name: details["commit"] for name, details in manifest["actions"].items()
    }
    used_actions: set[str] = set()

    action_definitions = sorted(
        path
        for path in (root / ".github/actions").rglob("*")
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    )
    for path in action_definitions:
        for reference in USES.findall(path.read_text(encoding="utf-8")):
            if reference.startswith("./"):
                continue
            if "@" not in reference:
                fail(f"composite external action has no ref: {reference}")
            _name, commit = reference.rsplit("@", 1)
            if not FULL_SHA.fullmatch(commit):
                fail(f"composite external action is not full-SHA pinned: {reference}")

    for relative in active:
        text = (root / relative).read_text(encoding="utf-8")
        lowered = text.lower()
        forbidden = {
            "pull_request_target": "privileged pull-request trigger",
            "secrets.": "repository or organization secret",
            "environment:": "deployment environment",
            "id-token: write": "OIDC write permission",
            "contents: write": "content write permission",
            "packages: write": "package write permission",
            "pull-requests: write": "pull-request write permission",
            "actions/upload-artifact@": "artifact publication",
            "actions/attest@": "attestation publication",
            "cachix/cachix-action@": "cache publication",
            "docker/login-action@": "registry login",
            "gh release": "GitHub release command",
            "git push": "Git push command",
            "nix copy --to": "Nix store publication",
            "runs-on: self-hosted": "untrusted self-hosted PR execution",
        }
        for needle, label in forbidden.items():
            if needle in lowered:
                fail(f"{relative} contains forbidden {label}: {needle}")

        for required in ("pull_request:", "push:", "schedule:", "workflow_dispatch:"):
            if required not in text:
                fail(f"{relative} is missing proactive trigger {required}")

        for reference in USES.findall(text):
            if reference.startswith("./"):
                continue
            if "@" not in reference:
                fail(f"external action has no ref: {reference}")
            name, commit = reference.rsplit("@", 1)
            if not FULL_SHA.fullmatch(commit):
                fail(f"external action is not full-SHA pinned: {reference}")
            expected = declared_actions.get(name)
            if expected is None:
                fail(f"external action is not declared in fleet-pin.json: {name}")
            expect(commit, expected, f"action pin {name}")
            used_actions.add(name)

    expect(used_actions, set(declared_actions), "declared/used action set")

    text_suffixes = {".json", ".md", ".nix", ".py", ".sh", ".yaml", ".yml"}
    scoped_text = "\n".join(
        path.read_text(encoding="utf-8")
        for base in (root / ".github/axiomlayer", root / "ci/axiomlayer")
        for path in base.rglob("*")
        if path.is_file() and path.suffix in text_suffixes
    ).lower()
    retired_name = "codex" + "_security" + "_gate"
    if retired_name in scoped_text:
        fail("retired security gate reintroduced")

    print(f"active_workflows={len(active)}")
    print(f"archived_upstream_workflows={len(archived)}")
    print(f"upstream_composite_actions={len(action_definitions)}")
    print(f"full_sha_action_pins={len(used_actions)}")
    print("workflow_isolation=verified")


def verify_policy(
    root: Path,
    source: Path,
    control: Path,
    manifest: dict[str, Any],
) -> None:
    expect(manifest.get("schema"), "axiom-nixpkgs-upstream-integration-v1", "schema")
    expect(manifest.get("fork"), "AxiomLayer/nixpkgs", "fork")
    expect(manifest.get("upstream", {}).get("repository"), "NixOS/nixpkgs", "upstream")
    expect(manifest.get("upstream", {}).get("branch"), "nixos-26.05", "upstream branch")

    pin = manifest["fleetPin"]
    commit = pin["commit"]
    if not FULL_SHA.fullmatch(commit):
        fail(f"fleet commit is not a full SHA: {commit}")
    expect(git(source, "rev-parse", "HEAD"), commit, "snapshot commit")
    expect(git(source, "rev-parse", "HEAD^{tree}"), pin["tree"], "snapshot tree")
    release = git_blob(source, "HEAD:lib/.version").decode("utf-8").strip()
    expect(release, pin["release"], "release")
    flake_digest = hashlib.sha256(git_blob(source, "HEAD:flake.nix")).hexdigest()
    expect(flake_digest, pin["flakeNixSha256"], "snapshot flake.nix")

    control_pin = manifest["control"]
    expect(git(control, "rev-parse", "HEAD"), control_pin["commit"], "control commit")
    for relative, expected_digest in control_pin["files"].items():
        expect(sha256(control / relative), expected_digest, f"control file {relative}")

    policy = load_json(control / "config/upstream-promotion-policy.json")
    inputs = load_json(control / "config/nix-integration-inputs.json")
    lock = load_json(control / "flake.lock")
    candidate = load_json(control / "promotion/candidate.json")

    policy_pin = one(policy["sources"], "id", "nixpkgs")
    expect(policy_pin.get("role"), "integration", "policy role")
    expect(policy_pin.get("acquisition"), "fork", "policy acquisition")
    expect(policy_pin.get("upstream"), manifest["upstream"]["repository"], "policy upstream")
    expect(policy_pin.get("repository"), manifest["fork"], "policy fork")
    expect(policy_pin.get("version"), pin["version"], "policy version")
    expect(policy_pin.get("commit"), commit, "policy commit")

    input_pin = inputs["inputs"]["nixpkgs"]
    expect(input_pin.get("repository"), manifest["fork"], "input repository")
    expect(input_pin.get("revision"), commit, "input revision")
    expect(input_pin.get("sha256"), pin["sourceArchiveSha256"], "source archive digest")
    expect(input_pin.get("narHash"), pin["narHash"], "source NAR hash")
    if commit not in input_pin.get("url", ""):
        fail("Nixpkgs input URL does not contain the exact fleet commit")

    locked = lock["nodes"]["nixpkgs"]["locked"]
    expect(locked.get("url"), input_pin["url"], "flake lock URL")
    expect(locked.get("narHash"), input_pin["narHash"], "flake lock NAR hash")

    candidate_pin = one(candidate["sourceSnapshots"], "id", "nixpkgs")
    expect(candidate_pin.get("repository"), manifest["fork"], "candidate repository")
    expect(candidate_pin.get("version"), pin["version"], "candidate version")
    expect(candidate_pin.get("commit"), commit, "candidate commit")

    policy_digest = canonical_digest(policy)
    expect(policy_digest, control_pin["canonicalPolicySha256"], "canonical policy digest")
    expect(candidate.get("policySha256"), policy_digest, "candidate policy digest")

    activation = inputs["activation"]
    expect(activation.get("allowLockMutation"), False, "lock mutation policy")
    expect(activation.get("allowRegistryLookup"), False, "registry lookup policy")
    expect(activation.get("acceptFlakeConfig"), False, "flake config policy")
    expect(policy["delivery"].get("authorityDelegated"), False, "delivery authority")

    expected_targets = sorted(manifest["consumerTargets"])
    expect(sorted(policy["acceptanceTargets"]), expected_targets, "consumer target set")
    if manifest["consumerTargets"].get("windows-aarch64") != "wsl-aarch64":
        fail("Windows ARM64 must consume Nixpkgs through WSL ARM64")
    if manifest["consumerTargets"].get("windows-x86_64") != "wsl-x86_64":
        fail("Windows x86_64 must consume Nixpkgs through WSL x86_64")

    native_systems = {entry["system"] for entry in manifest["nativeSystems"]}
    expect(
        native_systems,
        {"aarch64-darwin", "aarch64-linux", "x86_64-darwin", "x86_64-linux"},
        "native system set",
    )
    expect(
        set(manifest["evaluatedPackages"]),
        {"deno", "fnm", "go", "node", "npm", "python", "ripgrep", "sqlite"},
        "evaluated package set",
    )
    expect(set(manifest["builtPackages"]), {"hello", "ripgrep", "sqlite"}, "built package set")

    print(f"fleet_nixpkgs_commit={commit}")
    print(f"fleet_nixpkgs_version={pin['version']}")
    print(f"snapshot_tree={pin['tree']}")
    print(f"control_commit={control_pin['commit']}")
    print(f"control_policy_sha256={policy_digest}")
    print("fleet_pin_contract=verified")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("all", "policy", "workflows"), default="all")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--control", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    manifest = load_json(root / ".github/axiomlayer/fleet-pin.json")

    if args.mode in {"all", "workflows"}:
        verify_workflows(root, manifest)
    if args.mode in {"all", "policy"}:
        if args.source is None or args.control is None:
            fail("--source and --control are required for policy verification")
        verify_policy(root, args.source.resolve(), args.control.resolve(), manifest)


if __name__ == "__main__":
    main()
