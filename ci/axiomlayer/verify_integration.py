#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
USES = re.compile(
    r"(?:^|[\s{,\-])(?:['\"]?uses['\"]?)\s*:\s*['\"]?([^\s,}\]#'\"]+)",
    re.MULTILINE,
)
NIX_WRAPPER_RUN = "run: sh integration/ci/axiomlayer/install-nix-ci.sh"
ACTIVE_WORKFLOW = ".github/workflows/axiomlayer-integration.yml"
ACTIVE_WORKFLOW_SHA256 = (
    "603b9d382afcc2a0176199624f294000fe08129c526a212037a5409c02f218f3"
)
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
EXPECTED_ACTIONS = {
    "actions/checkout": {
        "version": "7.0.1",
        "commit": CHECKOUT_SHA,
    }
}
EXPECTED_NATIVE_SYSTEMS = [
    {"runner": "ubuntu-24.04", "system": "x86_64-linux"},
    {"runner": "ubuntu-24.04-arm", "system": "aarch64-linux"},
    {"runner": "macos-15-intel", "system": "x86_64-darwin"},
    {"runner": "macos-15", "system": "aarch64-darwin"},
]
NATIVE_MATRIX_YAML = """      matrix:
        include:
          - runner: ubuntu-24.04
            system: x86_64-linux
          - runner: ubuntu-24.04-arm
            system: aarch64-linux
          - runner: macos-15-intel
            system: x86_64-darwin
          - runner: macos-15
            system: aarch64-darwin
"""
STRICT_YAML_RUBY = r"""
require "psych"

def reject_ambiguous_mapping(node, path = [])
  if node.respond_to?(:anchor) && node.anchor
    warn "YAML anchors are forbidden at #{path.join('.')}"
    exit 1
  end
  if node.respond_to?(:tag) && node.tag
    warn "explicit YAML tags are forbidden at #{path.join('.')}"
    exit 1
  end
  case node
  when Psych::Nodes::Alias
    warn "YAML aliases are forbidden at #{path.join('.')}"
    exit 1
  when Psych::Nodes::Mapping
    seen = {}
    node.children.each_slice(2) do |key, value|
      unless key.is_a?(Psych::Nodes::Scalar) && key.anchor.nil? && key.tag.nil?
        warn "non-scalar or tagged YAML key is forbidden at #{path.join('.')}"
        exit 1
      end
      name = key.value
      if name == "<<" || seen.key?(name)
        warn "duplicate or merged YAML key #{name.inspect} at #{path.join('.')}"
        exit 1
      end
      seen[name] = true
      reject_ambiguous_mapping(value, path + [name])
    end
  when Psych::Nodes::Sequence
    node.children.each_with_index do |child, index|
      reject_ambiguous_mapping(child, path + [index.to_s])
    end
  end
end

begin
  stream = Psych.parse_stream(STDIN.read, filename: "workflow.yml")
rescue Psych::SyntaxError => error
  warn error.message
  exit 1
end
unless stream.children.length == 1 && stream.children.first.root
  warn "workflow must contain exactly one YAML document"
  exit 1
end
reject_ambiguous_mapping(stream.children.first.root)
"""
UPSTREAM_BASELINE_COMMIT = "d902acf46aecb02ff20305362c3c21a4b6e67457"
UPSTREAM_GITHUB_TREE = "92adeb17bcef059737ea9d7c0982f59e45e05634"
NIX_BOOTSTRAP = {
    "version": "2.35.2",
    "installerUrl": "https://releases.nixos.org/nix/nix-2.35.2/install",
    "installerSha256": (
        "9adda97297d9e8ab360df95c729eabff4f4f93d6db091953c3a68f29e3fb130c"
    ),
    "wrapper": {
        "path": "ci/axiomlayer/install-nix-ci.sh",
        "sha256": ("9a583b136a808771c848037ff9a5366ff341295cca87329c59e442be7acf1b58"),
    },
    "surface": {
        "path": "ci/axiomlayer/run-surface.sh",
        "sha256": ("ba3bf3c4520639cde35660d1e45f21fa40187bb42e1623dcd4da8aad29997c72"),
    },
    "expressions": {
        "evaluation": {
            "path": "ci/axiomlayer/evaluate-surface.nix",
            "sha256": (
                "329a5580e8b8357890edcf57c65c33da5c8f23ba87571a836b1a177f9432a4d6"
            ),
        },
        "smoke": {
            "path": "ci/axiomlayer/fleet-smoke.nix",
            "sha256": (
                "1e5a50d3227092b681882d70a5cd18c7cb62c1639fbd1a3e8f9c05ddb6edc303"
            ),
        },
    },
    "nativeTarballSha256": {
        "aarch64-darwin": (
            "1695c13aba5afa7c2ecd6dc4a9393f602e7bbc440ed45e81602c831546580ec3"
        ),
        "x86_64-darwin": (
            "d725518d89f3b0b8d4af702a9d38d519814014cbe125afb3ed0545c9d755f6a5"
        ),
        "aarch64-linux": (
            "4d0302a2910f5eec1c33b8deef634f04899a75737e7001ec49908d003ae5efda"
        ),
        "x86_64-linux": (
            "0c3960a9792331a22081c3c7a5d8465db9b17c50b3acdf18587fa4c6f2cb1158"
        ),
    },
}


def fail(message: str) -> None:
    raise SystemExit(message)


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            fail(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def reject_json_constant(value: str) -> None:
    fail(f"non-finite JSON number: {value}")


def load_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        fail(f"JSON control file is missing or is a symlink: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(
            handle,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_json_constant,
        )


def verify_unambiguous_yaml(text: str) -> None:
    ruby = shutil.which("ruby")
    if ruby is None:
        fail("Ruby is required for strict workflow parsing")
    with tempfile.TemporaryDirectory(prefix="axiom-nixpkgs-yaml-") as temporary:
        result = subprocess.run(
            [ruby, "--disable-gems", "-rpsych", "-e", STRICT_YAML_RUBY],
            check=False,
            input=text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                "HOME": temporary,
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "RUBYLIB": "",
                "RUBYOPT": "",
                "TMPDIR": temporary,
            },
        )
    if result.returncode != 0:
        fail(f"active workflow is malformed or ambiguous YAML: {result.stderr.strip()}")


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


def reject_symlinks(base: Path) -> None:
    if base.is_symlink():
        fail(f"control surface is a symlink: {base}")
    if not base.is_dir():
        fail(f"control surface directory is missing: {base}")
    for current, directories, files in os.walk(base, followlinks=False):
        current_path = Path(current)
        for name in [*directories, *files]:
            candidate = current_path / name
            if candidate.is_symlink():
                fail(f"control surface contains a symlink: {candidate}")


def verify_nix_bootstrap(root: Path, manifest: dict[str, Any]) -> None:
    expect(manifest.get("nix"), NIX_BOOTSTRAP, "Nix bootstrap contract")

    wrapper_path = root / NIX_BOOTSTRAP["wrapper"]["path"]
    if wrapper_path.is_symlink() or not wrapper_path.is_file():
        fail(f"Nix bootstrap wrapper is missing: {wrapper_path}")
    wrapper_text = wrapper_path.read_text(encoding="utf-8")

    required_literals = {
        "NIX_VERSION=2.35.2": "Nix version",
        (
            "INSTALLER_URL=https://releases.nixos.org/nix/nix-2.35.2/install"
        ): "installer URL",
        (
            "INSTALLER_SHA256="
            "9adda97297d9e8ab360df95c729eabff4f4f93d6db091953c3a68f29e3fb130c"
        ): "installer digest",
        "/usr/bin/curl --disable --fail --location": "sterile installer download",
        "--noproxy '*'": "proxy-free installer download",
    }
    for system, digest in NIX_BOOTSTRAP["nativeTarballSha256"].items():
        required_literals[f"PLATFORM_SHA256={digest}"] = f"{system} tarball digest"
    for literal, label in required_literals.items():
        if wrapper_text.count(literal) != 1:
            fail(f"Nix bootstrap wrapper has invalid {label}")
    expect(wrapper_text.count("env -i \\"), 3, "Nix bootstrap sterile boundary count")
    expect(wrapper_text.count('PATH="$safe_path"'), 2, "fixed installer path count")

    forbidden_credentials = {
        "github_token": "GitHub token",
        "gh_token": "GitHub CLI token",
        "github.token": "GitHub expression token",
        "github_access_token": "GitHub access token input",
        "actions_runtime_token": "Actions runtime token",
        "actions_id_token_request_token": "Actions OIDC token",
        "cachix_auth_token": "Cachix token",
        "nix_access_tokens": "Nix access token",
        "http_proxy": "HTTP proxy",
        "https_proxy": "HTTPS proxy",
        "all_proxy": "proxy",
        ".netrc": "netrc credential file",
        "credential.helper": "Git credential helper",
    }
    lowered_wrapper = wrapper_text.lower()
    for needle, label in forbidden_credentials.items():
        if needle in lowered_wrapper:
            fail(f"Nix bootstrap wrapper references forbidden {label}: {needle}")

    expected_wrapper_digest = NIX_BOOTSTRAP["wrapper"]["sha256"]
    expect(sha256(wrapper_path), expected_wrapper_digest, "Nix bootstrap wrapper")

    surface_path = root / NIX_BOOTSTRAP["surface"]["path"]
    if surface_path.is_symlink() or not surface_path.is_file():
        fail(f"Nix evaluation surface is missing or is a symlink: {surface_path}")
    surface_text = surface_path.read_text(encoding="utf-8")
    required_surface_literals = {
        "clean_nix() {": "sterile Nix command wrapper",
        "  /usr/bin/env -i \\": "sterile Nix process environment",
        'NIX_CONFIG="$nix_config" \\': "fixed Nix configuration",
        'git -C "$snapshot" archive --format=tar "$expected_commit" |': (
            "exact source export"
        ),
        'source_store=$(clean_nix "$nix_bin" store add-path \\': (
            "content-addressed source"
        ),
        'harness_store=$(clean_nix "$nix_bin" store add-path \\': (
            "content-addressed harness"
        ),
        'clean_nix "$nix_bin" eval \\': "sterile package evaluation",
        'result=$(clean_nix "$nix_bin" build \\': "sterile package build",
    }
    for literal, label in required_surface_literals.items():
        if surface_text.count(literal) != 1:
            fail(f"Nix evaluation surface has invalid {label}")
    for forbidden_name in (
        "ACTIONS_RUNTIME_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "NIX_ACCESS_TOKENS",
        "CACHIX_AUTH_TOKEN",
    ):
        if forbidden_name in surface_text:
            fail(f"Nix evaluation surface references forbidden {forbidden_name}")
    for forbidden_literal in (
        "--impure",
        "--file",
        'NIX_CONFIG="${NIX_CONFIG:-}"',
        'PATH="$PATH"',
    ):
        if forbidden_literal in surface_text:
            fail(f"Nix evaluation surface uses forbidden {forbidden_literal}")
    expect(surface_text.count("--option pure-eval true"), 2, "pure evaluation count")
    expect(
        surface_text.count("--option restrict-eval true"),
        2,
        "restricted evaluation count",
    )
    expect(surface_text.count("--expr"), 2, "expression-mode evaluation count")
    expected_surface_digest = NIX_BOOTSTRAP["surface"]["sha256"]
    expect(sha256(surface_path), expected_surface_digest, "Nix evaluation surface")
    for name, expression in NIX_BOOTSTRAP["expressions"].items():
        expression_path = root / expression["path"]
        if expression_path.is_symlink() or not expression_path.is_file():
            fail(f"Nix {name} expression is missing or is a symlink")
        expect(sha256(expression_path), expression["sha256"], f"Nix {name} expression")
    print(f"nix_installer_sha256={NIX_BOOTSTRAP['installerSha256']}")
    print(f"nix_wrapper_sha256={expected_wrapper_digest}")
    print(f"nix_surface_sha256={expected_surface_digest}")
    print("nix_expression_digests=verified")
    print("credential_sterile_nix_bootstrap=verified")
    print("credential_sterile_nix_evaluation=verified")


def verify_upstream_archive(
    root: Path,
    baseline_repository: Path,
    manifest: dict[str, Any],
) -> int:
    archive = manifest.get("upstreamArchive", {})
    baseline = archive.get("baselineCommit", "")
    if not FULL_SHA.fullmatch(baseline):
        fail("upstream archive baseline is not a full commit SHA")
    expect(baseline, UPSTREAM_BASELINE_COMMIT, "immutable upstream baseline")
    expect(
        archive.get("baselineGitHubTree"),
        UPSTREAM_GITHUB_TREE,
        "immutable upstream .github tree",
    )
    expect(archive.get("root"), ".github/upstream-archive", "archive root")
    expect(
        archive.get("readme"),
        ".github/upstream-archive/README.md",
        "archive README",
    )
    expect(archive.get("mode"), "100644", "archive file mode")

    files = archive.get("files")
    if not isinstance(files, dict) or not files:
        fail("upstream archive file manifest is empty")

    ancestry = subprocess.run(
        [
            "git",
            "-C",
            str(baseline_repository),
            "merge-base",
            "--is-ancestor",
            baseline,
            "HEAD",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if ancestry.returncode != 0:
        fail("upstream archive baseline is not an ancestor of the integration HEAD")
    expect(
        git(baseline_repository, "rev-parse", f"{baseline}:.github"),
        UPSTREAM_GITHUB_TREE,
        "baseline .github tree",
    )

    baseline_files = set(
        git(
            baseline_repository,
            "ls-tree",
            "-r",
            "--name-only",
            baseline,
            ".github",
        ).splitlines()
    )
    expect(set(files), baseline_files, "complete inherited .github baseline")

    archive_root = root / archive["root"]
    readme = root / archive["readme"]
    if readme.is_symlink() or not readme.is_file():
        fail("archive README is missing or is a symlink")

    expected_archives = {archive["readme"]}
    for original, expected_digest in files.items():
        original_path = PurePosixPath(original)
        if original_path.as_posix() != original:
            fail(f"archive original path is not canonical POSIX: {original}")
        try:
            relative = original_path.relative_to(".github")
        except ValueError:
            fail(f"archive original path escapes .github: {original}")
        if original_path.is_absolute() or ".." in original_path.parts:
            fail(f"unsafe archive original path: {original}")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
            fail(f"archive digest is not SHA-256: {original}")

        archived_relative = (PurePosixPath(archive["root"]) / relative).as_posix()
        expected_archives.add(archived_relative)
        archived_path = root / archived_relative
        if archived_path.is_symlink() or not archived_path.is_file():
            fail(f"archived baseline file is missing or is a symlink: {original}")
        if (root / original).exists() or (root / original).is_symlink():
            fail(f"inherited GitHub file remains live: {original}")

        tree_entry = git(baseline_repository, "ls-tree", baseline, original).split()
        if len(tree_entry) < 4:
            fail(f"baseline tree entry is missing: {original}")
        expect(tree_entry[0], archive["mode"], f"baseline mode {original}")
        archived_mode = format(0o100000 | (archived_path.stat().st_mode & 0o777), "o")
        expect(archived_mode, archive["mode"], f"archive mode {original}")

        baseline_bytes = git_blob(baseline_repository, f"{baseline}:{original}")
        archived_bytes = archived_path.read_bytes()
        if archived_bytes != baseline_bytes:
            fail(f"archive is not byte-identical to baseline: {original}")
        expect(sha256(archived_path), expected_digest, f"archive SHA-256 {original}")

    actual_archives = {
        path.relative_to(root).as_posix()
        for path in archive_root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    expect(actual_archives, expected_archives, "archive inventory")
    print(f"upstream_baseline_commit={baseline}")
    print(f"archived_upstream_files={len(files)}")
    print("archive_byte_identity=verified")
    return len(files)


def split_job_blocks(workflow: str) -> dict[str, str]:
    jobs_offset = workflow.find("\njobs:\n")
    if jobs_offset < 0:
        fail("active workflow has no jobs mapping")
    jobs_text = workflow[jobs_offset + len("\njobs:\n") :]
    matches = list(re.finditer(r"(?m)^  ([a-z][a-z0-9-]*):\n", jobs_text))
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(jobs_text)
        blocks[match.group(1)] = jobs_text[match.start() : end]
    return blocks


def verify_job_authorization(workflow: str) -> None:
    blocks = split_job_blocks(workflow)
    expect(
        set(blocks),
        {
            "policy",
            "fleet-pin",
            "resolve-upstream",
            "upstream-tip",
            "workflow-required",
        },
        "active job set",
    )
    terminal = blocks["workflow-required"]
    terminal_markers = {
        "    name: Required Nixpkgs integration authority\n": "required check name",
        (
            "    needs:\n"
            "      - policy\n"
            "      - fleet-pin\n"
            "      - resolve-upstream\n"
            "      - upstream-tip\n"
        ): "complete evidence dependency set",
        "    if: ${{ always() }}\n": "unconditional terminal execution",
        "    permissions: {}\n": "zero terminal token permissions",
        '          test "$POLICY_RESULT" = success\n': "policy result authority",
        '          test "$FLEET_PIN_RESULT" = success\n': "fleet-pin result authority",
        '              test "$RESOLVE_UPSTREAM_RESULT" = skipped\n': "non-canary resolver result",
        '              test "$UPSTREAM_TIP_RESULT" = skipped\n': "non-canary upstream result",
        '              test "$RESOLVE_UPSTREAM_RESULT" = success\n': "canary resolver result",
        '              test "$UPSTREAM_TIP_RESULT" = success\n': "canary upstream result",
        '          test "$GITHUB_SERVER_URL" = https://github.com\n': "GitHub server identity",
        '          test "$EVENT_REPOSITORY" = axiomlayer/nixpkgs\n': "event repository identity",
        '              test "$PR_HEAD_REPOSITORY" = axiomlayer/nixpkgs\n': "same-repository pull request",
        '          test "$EVENT_SHA" = "$GITHUB_SHA"\n': "event SHA binding",
        '              test "$GITHUB_SHA" = "$PR_MERGE_COMMIT"\n': (
            "pull-request merge SHA binding"
        ),
        '              test "$GITHUB_SHA" = "$PUSH_AFTER"\n': "push SHA binding",
        '              test "$GITHUB_REF_PROTECTED" = true\n': "protected-main authority",
        "            schedule|workflow_dispatch)\n": "canary event route",
    }
    for literal, label in terminal_markers.items():
        expected_count = (
            2
            if literal
            in {
                '              test "$RESOLVE_UPSTREAM_RESULT" = skipped\n',
                '              test "$UPSTREAM_TIP_RESULT" = skipped\n',
                '              test "$GITHUB_REF_PROTECTED" = true\n',
            }
            else 1
        )
        expect(terminal.count(literal), expected_count, f"terminal {label}")

    common = {
        "github.repository == 'axiomlayer/nixpkgs'": "exact repository identity",
        "github.event.repository.default_branch == 'master'": "default branch identity",
        "github.ref_protected == true": "protected ref guard",
        (
            "github.workflow_ref == 'axiomlayer/nixpkgs/.github/workflows/"
            "axiomlayer-integration.yml@refs/heads/master'"
        ): "protected-main workflow identity",
    }
    pull_request = {
        "github.event_name == 'pull_request'": "pull-request event guard",
        "github.base_ref == 'master'": "pull-request base guard",
        (
            "github.event.pull_request.head.repo.full_name == 'axiomlayer/nixpkgs'"
        ): "same-repository pull-request guard",
        (
            "github.sha == github.event.pull_request.merge_commit_sha"
        ): "pull-request merge SHA guard",
        (
            "github.ref == format('refs/pull/{0}/merge', "
            "github.event.pull_request.number)"
        ): "pull-request merge-ref guard",
        (
            "github.workflow_ref == format('axiomlayer/nixpkgs/.github/workflows/"
            "axiomlayer-integration.yml@refs/pull/{0}/merge', "
            "github.event.pull_request.number)"
        ): "pull-request workflow identity",
    }
    for name in ("policy", "fleet-pin", "resolve-upstream", "upstream-tip"):
        block = blocks[name]
        for literal, label in common.items():
            if literal not in block:
                fail(f"job {name} is missing {label}")
        if "github.ref == 'refs/heads/master'" not in block:
            fail(f"job {name} is missing protected main ref identity")
        if name in {"policy", "fleet-pin"}:
            for literal, label in pull_request.items():
                if literal not in block:
                    fail(f"job {name} is missing {label}")
            if (
                "github.event_name == 'push' && github.sha == github.event.after"
                not in block
            ):
                fail(f"job {name} is missing protected push authorization")
        else:
            for event_name in ("schedule", "workflow_dispatch"):
                if f"github.event_name == '{event_name}'" not in block:
                    fail(f"job {name} is missing {event_name} authorization")

    expected_runners = {
        "policy": "ubuntu-24.04",
        "fleet-pin": "${{ matrix.runner }}",
        "resolve-upstream": "ubuntu-24.04",
        "upstream-tip": "${{ matrix.runner }}",
        "workflow-required": "ubuntu-24.04",
    }
    for name, expected_runner in expected_runners.items():
        expect(
            blocks[name].count(f"    runs-on: {expected_runner}\n"),
            1,
            f"job {name} runner",
        )


def verify_workflows(
    root: Path,
    baseline_repository: Path,
    manifest: dict[str, Any],
) -> None:
    workflow_dir = root / ".github/workflows"
    if not workflow_dir.is_dir() or workflow_dir.is_symlink():
        fail("active workflow directory is missing or is a symlink")
    active = sorted(
        path.relative_to(root).as_posix()
        for path in workflow_dir.iterdir()
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    )
    expected_active = [ACTIVE_WORKFLOW]
    expect(manifest.get("activeWorkflows"), expected_active, "declared workflow set")
    expect(
        manifest.get("requiredStatusCheck"),
        "Required Nixpkgs integration authority",
        "required status check",
    )
    expect(active, expected_active, "active workflow set")
    if any(path.is_symlink() for path in workflow_dir.iterdir()):
        fail("active workflow directory contains a symlink")

    archived_count = verify_upstream_archive(root, baseline_repository, manifest)
    live_github_files = {
        path.relative_to(root).as_posix()
        for path in (root / ".github").rglob("*")
        if path.is_file()
        and not path.is_relative_to(root / manifest["upstreamArchive"]["root"])
    }
    expect(
        live_github_files,
        {
            ".github/axiomlayer/README.md",
            ".github/axiomlayer/fleet-pin.json",
            ".github/workflows/axiomlayer-integration.yml",
        },
        "live GitHub control file set",
    )

    expect(manifest.get("actions"), EXPECTED_ACTIONS, "action contract")
    declared_actions = {
        name: details["commit"] for name, details in manifest["actions"].items()
    }
    used_actions: set[str] = set()

    action_definitions = sorted(
        path
        for path in (root / ".github/actions").rglob("*")
        if path.is_file() and path.suffix in {".yml", ".yaml"}
    )
    expect(action_definitions, [], "active composite action set")

    for relative in active:
        workflow_path = root / relative
        expect(
            manifest.get("activeWorkflowSha256"),
            ACTIVE_WORKFLOW_SHA256,
            "declared active workflow SHA-256",
        )
        expect(
            sha256(workflow_path),
            ACTIVE_WORKFLOW_SHA256,
            "active workflow SHA-256",
        )
        text = workflow_path.read_text(encoding="utf-8")
        verify_unambiguous_yaml(text)
        if not re.search(r"(?m)^permissions:\s*\n\s{2}contents:\s*read\s*$", text):
            fail(f"{relative} must grant only top-level contents:read")
        lowered = text.lower()
        forbidden = {
            "pull_request_target": "privileged pull-request trigger",
            "secrets.": "repository or organization secret",
            "secrets[": "bracket-form repository or organization secret",
            "environment:": "deployment environment",
            "continue-on-error:": "soft-failed workflow step or job",
            "permissions: write-all": "global write permission",
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
            "curl ": "outbound curl command",
            "wget ": "outbound wget command",
            "scp ": "outbound secure-copy command",
            "rsync ": "outbound synchronization command",
            "docker push": "container publication",
            "npm publish": "npm publication",
            "deno publish": "Deno publication",
            "twine upload": "Python publication",
            "gh api": "GitHub API mutation surface",
            "self-hosted": "fleet self-hosted runner execution",
            "runner-group": "runner group selection",
            "runs-on: fleet-": "fleet runner selection",
            "repository: axiomlayer/dotfiles": "private control repository checkout",
            "cachix/": "delegated Cachix action",
            "install-nix-action": "delegated Nix bootstrap action",
            "nix-installer-action": "delegated Nix installer action",
            "determinate-nix-action": "delegated Determinate Nix action",
            "install_url:": "delegated installer URL input",
            "github.token": "GitHub token expression",
            "github_access_token": "GitHub access token input",
            "github_token": "GitHub token variable",
            "gh_token": "GitHub CLI token variable",
            "cachix_auth_token": "Cachix token variable",
            "nix_access_tokens": "Nix access token variable",
            "actions_runtime_token": "Actions runtime token variable",
            "actions_id_token_request_token": "Actions OIDC token variable",
            "https://releases.nixos.org/nix/nix-": "inline Nix installer",
            "axiomlayer/nixpkgs@": "non-workflow repository identity",
        }
        for needle, label in forbidden.items():
            if needle in lowered:
                fail(f"{relative} contains forbidden {label}: {needle}")
        if re.search(r"(?i)\bsecrets\s*(?:\.|\[)", text):
            fail(f"{relative} references a secret context")
        if re.search(r"(?mi)^\s*environment\s*:", text):
            fail(f"{relative} declares a deployment environment")

        for required in ("pull_request:", "push:", "schedule:", "workflow_dispatch:"):
            if required not in text:
                fail(f"{relative} is missing proactive trigger {required}")
        if "AxiomLayer/" in text:
            fail(f"{relative} contains a non-lowercase AxiomLayer identity")
        verify_job_authorization(text)
        expect(text.count(NATIVE_MATRIX_YAML), 2, "exact native matrix count")

        expect(
            text.count(NIX_WRAPPER_RUN), 2, f"{relative} Nix wrapper invocation count"
        )

        step_blocks = re.split(r"(?m)(?=^      - name: )", text)
        checkout_blocks = [
            block for block in step_blocks if "uses: actions/checkout@" in block
        ]
        expect(len(checkout_blocks), 6, f"{relative} checkout step count")
        expect(
            sum(block.count("uses: actions/checkout@") for block in checkout_blocks),
            text.count("uses: actions/checkout@"),
            f"{relative} named checkout coverage",
        )
        checkout_specs = [
            {
                "repository": None,
                "ref": "${{ github.sha }}",
                "path": "integration",
                "fetch-depth": "0",
                "sparse": (
                    ".github/axiomlayer",
                    ".github/upstream-archive",
                    ".github/workflows",
                    "ci/axiomlayer",
                ),
            },
            {
                "repository": "axiomlayer/nixpkgs",
                "ref": "${{ env.FLEET_NIXPKGS_COMMIT }}",
                "path": "snapshot",
                "fetch-depth": "1",
                "sparse": (),
            },
            {
                "repository": None,
                "ref": "${{ github.sha }}",
                "path": "integration",
                "fetch-depth": "1",
                "sparse": (".github/axiomlayer", "ci/axiomlayer"),
            },
            {
                "repository": "axiomlayer/nixpkgs",
                "ref": "${{ env.FLEET_NIXPKGS_COMMIT }}",
                "path": "snapshot",
                "fetch-depth": "1",
                "sparse": (),
            },
            {
                "repository": None,
                "ref": "${{ github.sha }}",
                "path": "integration",
                "fetch-depth": "1",
                "sparse": (".github/axiomlayer", "ci/axiomlayer"),
            },
            {
                "repository": "NixOS/nixpkgs",
                "ref": "${{ needs.resolve-upstream.outputs.commit }}",
                "path": "snapshot",
                "fetch-depth": "1",
                "sparse": (),
            },
        ]
        for index, (block, spec) in enumerate(
            zip(checkout_blocks, checkout_specs, strict=True), start=1
        ):
            expect(
                block.count(f"uses: actions/checkout@{CHECKOUT_SHA} # v7.0.1"),
                1,
                f"{relative} checkout {index} exact action pin",
            )
            expect(
                block.count("persist-credentials: false"),
                1,
                f"{relative} checkout {index} credential persistence",
            )
            expect(
                block.count("clean: true"),
                1,
                f"{relative} checkout {index} clean-worktree policy",
            )
            if re.search(r"(?mi)^\s*token\s*:", block):
                fail(f"{relative} checkout {index} supplies an explicit token")
            for key in ("ref", "path", "fetch-depth"):
                expect(
                    block.count(f"          {key}: {spec[key]}\n"),
                    1,
                    f"{relative} checkout {index} {key}",
                )
            repositories = re.findall(r"(?m)^\s+repository:\s*(\S+)\s*$", block)
            expected_repositories = (
                [] if spec["repository"] is None else [spec["repository"]]
            )
            expect(
                repositories,
                expected_repositories,
                f"{relative} checkout {index} repository",
            )
            sparse_paths = tuple(
                match.group(1)
                for match in re.finditer(
                    r"(?m)^            ((?:\.github|ci)/\S+)\s*$",
                    block,
                )
            )
            expect(
                sparse_paths,
                spec["sparse"],
                f"{relative} checkout {index} sparse paths",
            )
            expect(
                block.count("          sparse-checkout-cone-mode: false\n"),
                1 if spec["sparse"] else 0,
                f"{relative} checkout {index} sparse cone mode",
            )

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

    expect(manifest.get("nativeSystems"), EXPECTED_NATIVE_SYSTEMS, "native matrix")
    runners = {entry["runner"] for entry in EXPECTED_NATIVE_SYSTEMS}
    dated_runner = re.compile(
        r"^(?:ubuntu-[0-9]{2}\.[0-9]{2}(?:-arm)?|macos-[0-9]+(?:-intel)?)$"
    )
    if any(not dated_runner.fullmatch(runner) for runner in runners):
        fail(f"native runner set is not date-pinned: {sorted(runners)}")
    for required_runner in runners | {"ubuntu-24.04"}:
        if required_runner not in "\n".join(
            (root / relative).read_text(encoding="utf-8") for relative in active
        ):
            fail(f"declared hosted runner is unused: {required_runner}")

    text_suffixes = {".json", ".md", ".nix", ".py", ".sh", ".yaml", ".yml"}
    scoped_paths = [
        path
        for base in (
            root / ".github/axiomlayer",
            root / ".github/workflows",
            root / "ci/axiomlayer",
        )
        for path in base.rglob("*")
        if path.is_file() and path.suffix in text_suffixes
    ]
    scoped_text = "\n".join(
        path.read_text(encoding="utf-8") for path in scoped_paths
    ).lower()
    retired_name = "codex" + "_security" + "_gate"
    retired_slug = "codex" + "-security" + "-gate"
    if retired_name in scoped_text or retired_slug in scoped_text:
        fail("retired security gate reintroduced")
    jsr_host = "jsr" + ".io"
    jsr_scheme = "jsr" + ":"
    if jsr_host in scoped_text or jsr_scheme in scoped_text:
        fail("JSR dependency reintroduced")

    print(f"active_workflows={len(active)}")
    print(f"archived_upstream_files={archived_count}")
    print(f"active_composite_actions={len(action_definitions)}")
    print(f"full_sha_action_pins={len(used_actions)}")
    print("hosted_runner_boundary=verified")
    print("exact_source_authorization=verified")
    print("workflow_isolation=verified")


def verify_policy(
    root: Path,
    source: Path,
    control: Path | None,
    manifest: dict[str, Any],
) -> None:
    expect(manifest.get("schema"), "axiom-nixpkgs-upstream-integration-v1", "schema")
    expect(manifest.get("fork"), "axiomlayer/nixpkgs", "fork")
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
    expect(control_pin.get("repository"), "axiomlayer/dotfiles", "control repository")
    expect(control_pin.get("pullRequest"), 49, "control pull request")
    if not FULL_SHA.fullmatch(control_pin.get("commit", "")):
        fail("control commit is not a full SHA")
    expected_control_files = {
        "config/nix-integration-inputs.json",
        "config/upstream-promotion-policy.json",
        "flake.lock",
        "promotion/candidate.json",
    }
    expect(
        set(control_pin.get("files", {})), expected_control_files, "control file set"
    )
    for relative, digest in control_pin["files"].items():
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            fail(f"control file digest is not SHA-256: {relative}")
    policy_digest = control_pin.get("canonicalPolicySha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", policy_digest):
        fail("canonical policy digest is not SHA-256")

    if control is not None:
        expect(
            git(control, "rev-parse", "HEAD"), control_pin["commit"], "control commit"
        )
        for relative, expected_digest in control_pin["files"].items():
            expect(
                sha256(control / relative), expected_digest, f"control file {relative}"
            )

        policy = load_json(control / "config/upstream-promotion-policy.json")
        inputs = load_json(control / "config/nix-integration-inputs.json")
        lock = load_json(control / "flake.lock")
        candidate = load_json(control / "promotion/candidate.json")

        policy_pin = one(policy["sources"], "id", "nixpkgs")
        expect(policy_pin.get("role"), "integration", "policy role")
        expect(policy_pin.get("acquisition"), "fork", "policy acquisition")
        expect(
            policy_pin.get("upstream"),
            manifest["upstream"]["repository"],
            "policy upstream",
        )
        expect(
            policy_pin.get("repository", "").lower(),
            manifest["fork"],
            "policy fork",
        )
        expect(policy_pin.get("version"), pin["version"], "policy version")
        expect(policy_pin.get("commit"), commit, "policy commit")

        input_pin = inputs["inputs"]["nixpkgs"]
        expect(
            input_pin.get("repository", "").lower(),
            manifest["fork"],
            "input repository",
        )
        expect(input_pin.get("revision"), commit, "input revision")
        expect(
            input_pin.get("sha256"), pin["sourceArchiveSha256"], "source archive digest"
        )
        expect(input_pin.get("narHash"), pin["narHash"], "source NAR hash")
        if commit not in input_pin.get("url", ""):
            fail("Nixpkgs input URL does not contain the exact fleet commit")

        locked = lock["nodes"]["nixpkgs"]["locked"]
        expect(locked.get("url"), input_pin["url"], "flake lock URL")
        expect(locked.get("narHash"), input_pin["narHash"], "flake lock NAR hash")

        candidate_pin = one(candidate["sourceSnapshots"], "id", "nixpkgs")
        expect(
            candidate_pin.get("repository", "").lower(),
            manifest["fork"],
            "candidate repository",
        )
        expect(candidate_pin.get("version"), pin["version"], "candidate version")
        expect(candidate_pin.get("commit"), commit, "candidate commit")

        actual_policy_digest = canonical_digest(policy)
        expect(actual_policy_digest, policy_digest, "canonical policy digest")
        expect(
            candidate.get("policySha256"),
            actual_policy_digest,
            "candidate policy digest",
        )

        activation = inputs["activation"]
        expect(activation.get("allowLockMutation"), False, "lock mutation policy")
        expect(activation.get("allowRegistryLookup"), False, "registry lookup policy")
        expect(activation.get("acceptFlakeConfig"), False, "flake config policy")
        expect(
            policy["delivery"].get("authorityDelegated"), False, "delivery authority"
        )
        expect(
            sorted(policy["acceptanceTargets"]),
            sorted(manifest["consumerTargets"]),
            "consumer target set",
        )
        control_status = "verified"
    else:
        control_status = "minimized-binding-only"

    expected_targets = sorted(manifest["consumerTargets"])
    expect(
        expected_targets,
        [
            "darwin-aarch64",
            "darwin-x86_64",
            "linux-aarch64",
            "linux-x86_64",
            "windows-aarch64",
            "windows-x86_64",
            "wsl-aarch64",
            "wsl-x86_64",
        ],
        "consumer target set",
    )
    if manifest["consumerTargets"].get("windows-aarch64") != "wsl-aarch64":
        fail("Windows ARM64 must consume Nixpkgs through WSL ARM64")
    if manifest["consumerTargets"].get("windows-x86_64") != "wsl-x86_64":
        fail("Windows x86_64 must consume Nixpkgs through WSL x86_64")

    expect(manifest.get("nativeSystems"), EXPECTED_NATIVE_SYSTEMS, "native matrix")
    expect(
        set(manifest["evaluatedPackages"]),
        {"deno", "fnm", "go", "node", "npm", "python", "ripgrep", "sqlite"},
        "evaluated package set",
    )
    expect(
        set(manifest["builtPackages"]),
        {"hello", "ripgrep", "sqlite"},
        "built package set",
    )

    print(f"fleet_nixpkgs_commit={commit}")
    print(f"fleet_nixpkgs_version={pin['version']}")
    print(f"snapshot_tree={pin['tree']}")
    print(f"control_commit={control_pin['commit']}")
    print(f"control_policy_sha256={policy_digest}")
    print(f"control_source={control_status}")
    print("fleet_pin_contract=verified")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("all", "policy", "workflows"), default="all")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--baseline-repository", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    reject_symlinks(root / ".github")
    reject_symlinks(root / "ci/axiomlayer")
    manifest = load_json(root / ".github/axiomlayer/fleet-pin.json")

    if args.mode in {"all", "workflows"}:
        verify_nix_bootstrap(root, manifest)
        baseline_repository = (
            args.baseline_repository.resolve()
            if args.baseline_repository is not None
            else root
        )
        verify_workflows(root, baseline_repository, manifest)
    if args.mode in {"all", "policy"}:
        if args.source is None:
            fail("--source is required for policy verification")
        control = args.control.resolve() if args.control is not None else None
        verify_policy(root, args.source.resolve(), control, manifest)


if __name__ == "__main__":
    main()
